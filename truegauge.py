#!/usr/bin/env python3
"""truegauge -- a status line for Claude Code that never shows an old number as a current one.

Shows: time left in the 5-hour and weekly plan windows with the percentage still left, the weekly
Fable window (separate allowance, letter F), tokens spent in this session (main + subagents), and how
full the context window is.

Sources (all documented Claude Code surfaces, nothing scraped, no token of yours leaves Claude Code):
  * status-line stdin JSON: rate_limits.five_hour / seven_day {used_percentage, resets_at},
    context_window.used_percentage, transcript_path, session_id.
  * transcript JSONL (transcript_path + <session>/subagents/*.jsonl): per-message `usage`.
  * Fable: the status line does not carry it. truegauge runs `claude -p /usage` in the background at
    most once per TRUEGAUGE_PROBE_S seconds (default 600) and reads the line
    "Current week (Fable): N% used". Off with TRUEGAUGE_FABLE=0.

Honesty rules (the reason this tool exists):
  * A session left idle re-sends its LAST reading. Per window the reading with the larger
    (resets_at, used_percentage) wins; an equal reset with a lower percentage is a replay and is ignored.
  * Use inside a window only grows. A reading that did not change for TRUEGAUGE_MAX_AGE_S (900 s) is shown
    as "<=N%" (at most N% left), never as a point value.
  * A window whose reset time has passed shows "reset", not the old number.
  * Any failure leaves the status line empty or partial, never wrong; it is recorded in errors.log.

Modes:
  (no args)       status-line mode: read stdin JSON, update state, print one line.
  --show          what the tool would conclude NOW, with its own health.
  --probe-usage   read `claude -p /usage` once and store the Fable window (normally run in background).
Config (environment): TRUEGAUGE_HOME (state dir, default ~/.truegauge), TRUEGAUGE_LANG (en|ru),
  TRUEGAUGE_FABLE (1|0), TRUEGAUGE_PROBE_S, TRUEGAUGE_MAX_AGE_S.
Stdlib only. License: Apache-2.0.
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time

__version__ = "0.1.0"

HOME = os.environ.get("TRUEGAUGE_HOME") or os.path.join(os.path.expanduser("~"), ".truegauge")
STATE = os.path.join(HOME, "state.json")
ERRLOG = os.path.join(HOME, "errors.log")
SESS_DIR = os.path.join(HOME, "sessions")
WINDOWS = ("five_hour", "seven_day", "spend_limit")
MAX_AHEAD = {"five_hour": 6 * 3600, "seven_day": 8 * 86400, "spend_limit": 400 * 86400}
EPOCH_MIN, EPOCH_MAX = 1e9, 1e11
ERRLOG_CAP = 64 * 1024
FABLE_LINE = re.compile(r"Current week \(Fable\):\s*(\d+(?:\.\d+)?)%\s*used(?:\s*\S\s*resets\s+(.+?))?\s*$", re.M)
WEEK_LINE = re.compile(r"Current week \(all models\):\s*(\d+(?:\.\d+)?)%\s*used(?:\s*\S\s*resets\s+(.+?))?\s*$", re.M)


def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _num(v):
    """A finite number, else None. bool is not a number here."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return v


# ---------------------------------------------------------------- state file

def _read(path=None):
    """-> (dict, problem). problem: None | 'missing' | 'corrupt' | 'unreadable'."""
    path = path or STATE
    for _ in range(3):
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = f.read()
        except FileNotFoundError:
            return {}, "missing"
        except OSError:
            time.sleep(0.02)
            continue
        try:
            d = json.loads(raw)
        except ValueError:
            return {}, "corrupt"
        return (d, None) if isinstance(d, dict) else ({}, "corrupt")
    return {}, "unreadable"


def _save(d, path=None):
    """Atomic replace. A failed tmp file is left in place and counted by --show (never deleted)."""
    path = path or STATE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.tmp%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=True, indent=1)
    for i in range(4):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # Windows: another process holds the target open
            if i == 3:
                raise
            time.sleep(0.02)


def _record_error(msg):
    """The failure goes into the state file; if that fails too, into a capped sidecar log."""
    now = int(time.time())
    try:
        d, problem = _read()
        if problem in (None, "missing"):
            d["last_error"] = {"at": now, "msg": str(msg)[:300]}
            _save(d)
            return
    except Exception:
        pass
    try:
        os.makedirs(HOME, exist_ok=True)
        if not os.path.exists(ERRLOG) or os.path.getsize(ERRLOG) < ERRLOG_CAP:
            with open(ERRLOG, "a", encoding="utf-8") as f:
                f.write("%d %s\n" % (now, str(msg)[:300]))
    except Exception:
        pass


# ---------------------------------------------------------------- plan windows

def extract(payload, now):
    """payload -> ({window: {used_percentage, resets_at}}, {window: reason})."""
    out, bad = {}, {}
    rl = payload.get("rate_limits") if isinstance(payload, dict) else None
    if not isinstance(rl, dict):
        return out, bad
    for w in WINDOWS:
        item = rl.get(w)
        if item is None:
            continue
        if not isinstance(item, dict):
            bad[w] = "not an object"
            continue
        pct, rst = _num(item.get("used_percentage")), _num(item.get("resets_at"))
        if pct is None:
            bad[w] = "used_percentage not a finite number: %r" % (item.get("used_percentage"),)
        elif pct < 0 or (w != "spend_limit" and pct > 100):
            bad[w] = "used_percentage out of range: %r" % (pct,)
        elif rst is None:
            bad[w] = "resets_at not a finite number: %r" % (item.get("resets_at"),)
        elif not (EPOCH_MIN <= rst <= EPOCH_MAX):
            bad[w] = "resets_at not epoch seconds: %r" % (rst,)
        elif rst > now + MAX_AHEAD[w]:
            bad[w] = "resets_at too far ahead: %r" % (rst,)
        else:
            out[w] = {"used_percentage": pct, "resets_at": int(rst)}
    return out, bad


def _valid(item):
    return (isinstance(item, dict) and _num(item.get("used_percentage")) is not None
            and _num(item.get("resets_at")) is not None)


def update(payload, now=None):
    """Merge one status-line payload into the state file. Returns the state after the merge."""
    if not isinstance(payload, dict):
        raise ValueError("status-line payload is not a JSON object")
    now = time.time() if now is None else now
    d, problem = _read()
    if problem == "unreadable":
        raise OSError("state file stayed unreadable after retries; update skipped, old state kept")
    if problem == "corrupt":
        d = {"recovered_from_corrupt_at": int(now)}
    got, bad = extract(payload, now)
    d["last_call"] = int(now)
    d["last_call_had_rate_limits"] = bool(got)
    d["last_rejected"] = bad
    rl = payload.get("rate_limits")
    if isinstance(rl, dict) and rl and not got:
        d["last_error"] = {"at": int(now), "msg": ("rate_limits present, no window parsed; keys=%s rejected=%s"
                                                   % (sorted(map(str, rl))[:10], bad))[:300]}
    changed = False
    for w, new in got.items():
        old = d.get(w)
        if not _valid(old) or (new["resets_at"], new["used_percentage"]) > (old["resets_at"], old["used_percentage"]):
            d[w], changed = new, True
    if changed:
        d["updated_at"] = int(now)
    d["last_ok_at"] = int(now)
    _save(d)
    return d


def _orphans(now):
    n = 0
    for p in glob.glob(STATE + ".tmp*"):
        try:
            if now - os.path.getmtime(p) > 60:
                n += 1
        except OSError:
            pass
    return n


def read_state(now=None, max_age_s=None):
    """-> {'_tap': health, window: {...}, 'fable': {...}}.
    Window status: fresh (remaining_percentage) | stale_reading (remaining_at_most) | expired."""
    now = time.time() if now is None else now
    max_age_s = _env_int("TRUEGAUGE_MAX_AGE_S", 900) if max_age_s is None else max_age_s
    d, problem = _read()
    err = d.get("last_error") if isinstance(d.get("last_error"), dict) else None
    last_ok = _num(d.get("last_ok_at")) or 0
    unresolved = bool(err and (_num(err.get("at")) or 0) >= last_ok)
    lc = _num(d.get("last_call"))
    res = {"_tap": {"file": problem or "ok", "last_call_age_s": None if lc is None else int(now - lc),
                    "last_call_had_rate_limits": d.get("last_call_had_rate_limits"),
                    "unresolved_error": err["msg"] if unresolved else None,
                    "last_rejected": d.get("last_rejected") or None, "orphan_tmp": _orphans(now),
                    "recovered_from_corrupt_at": d.get("recovered_from_corrupt_at")}}
    upd = _num(d.get("updated_at"))
    for w in WINDOWS:
        item = d.get(w)
        if _valid(item):
            res[w] = _judge(item["used_percentage"], item["resets_at"], upd, now, max_age_s,
                            "tap has an unresolved error" if unresolved else None)
    fb = d.get("fable")
    if isinstance(fb, dict) and _num(fb.get("used_percentage")) is not None and _num(fb.get("read_at")) is not None:
        # Fable has no machine-readable reset time; it shares the weekly reset when /usage printed the same text.
        rst = d["seven_day"]["resets_at"] if (fb.get("same_reset_as_week") and _valid(d.get("seven_day"))) else None
        v = _judge(fb["used_percentage"], rst, fb["read_at"], now, max_age_s * 2, None)
        if rst is None:
            v["resets_at"] = None
        res["fable"] = v
    elif isinstance(fb, dict) and fb.get("absent"):
        res["fable"] = {"status": "absent"}
    pe = d.get("probe_error")
    if isinstance(pe, dict):
        res["_tap"]["probe_error"] = pe.get("msg")
    return res


def _judge(pct, rst, reading_at, now, max_age_s, extra_why):
    v = {"resets_at": None if rst is None else int(rst), "resets_in_s": None if rst is None else int(rst - now)}
    if rst is not None and rst <= now:
        v.update(status="expired", last_seen_used_percentage=pct, remaining_percentage=None)
        return v
    age = None if reading_at is None else now - reading_at
    why = None
    if age is None:
        why = "no reading time"
    elif age < -60:
        why = "clock: reading is from the future"
    elif age > max_age_s:
        why = "numbers unchanged for %d s" % age
    elif extra_why:
        why = extra_why
    rem = max(0.0, 100.0 - pct)
    v.update(used_percentage=pct)
    if why is None:
        v.update(status="fresh", remaining_percentage=rem)
    else:
        v.update(status="stale_reading", remaining_percentage=None, remaining_at_most=rem, why=why)
    return v


# ---------------------------------------------------------------- Fable via /usage

def parse_usage(text):
    """`claude -p /usage` text -> {'fable': (pct, reset_text)|None, 'week': (pct, reset_text)|None}."""
    out = {}
    for key, rx in (("fable", FABLE_LINE), ("week", WEEK_LINE)):
        m = rx.search(text or "")
        out[key] = (float(m.group(1)), (m.group(2) or "").strip()) if m else None
    return out


def probe_usage(now=None, runner=None):
    """Run /usage once and store the Fable window. runner(cmd, cwd) -> text (injectable for tests)."""
    now = time.time() if now is None else now
    os.makedirs(HOME, exist_ok=True)
    try:
        if runner is None:
            exe = shutil.which("claude")
            if not exe:
                raise RuntimeError("claude executable not found on PATH")
            env = dict(os.environ, MSYS_NO_PATHCONV="1")
            cp = subprocess.run([exe, "-p", "/usage", "--no-session-persistence", "--setting-sources", "user"],
                                cwd=HOME, env=env, capture_output=True, timeout=60,
                                encoding="utf-8", errors="replace")
            text = (cp.stdout or "") + "\n" + (cp.stderr or "")
        else:
            text = runner()
        got = parse_usage(text)
        d, problem = _read()
        if problem == "corrupt":
            d = {"recovered_from_corrupt_at": int(now)}
        if got["fable"] is None:
            # no Fable line: the plan has no separate Fable window (or the text changed) -- say so, keep nothing old
            d["fable"] = {"absent": True, "read_at": int(now)}
            if "Current week" not in text:
                d["probe_error"] = {"at": int(now), "msg": ("no usage lines in /usage output: %r" % text[:200])[:300]}
            else:
                d.pop("probe_error", None)
        else:
            pct, rtext = got["fable"]
            if not 0 <= pct <= 100:
                raise ValueError("Fable percentage out of range: %r" % pct)
            old = d.get("fable") if isinstance(d.get("fable"), dict) else {}
            same = bool(got["week"] and rtext and rtext == got["week"][1])
            d["fable"] = {"used_percentage": pct, "read_at": int(now), "reset_text": rtext,
                          "same_reset_as_week": same}
            if _num(old.get("used_percentage")) is not None and pct < old["used_percentage"] and old.get("reset_text") == rtext:
                d["fable"]["note"] = "lower than previous reading with the same reset text"
            d.pop("probe_error", None)
        d["probe_done_at"] = int(now)
        _save(d)
        return d.get("fable")
    except Exception as e:
        d, problem = _read()
        if problem in (None, "missing"):
            d["probe_error"] = {"at": int(now), "msg": ("%s: %s" % (type(e).__name__, e))[:300]}
            d["probe_done_at"] = int(now)
            _save(d)
        else:
            _record_error("probe: %s: %s" % (type(e).__name__, e))
        return None


def maybe_start_probe(now=None, spawn=None):
    """Start the /usage probe in the background when the last one is older than TRUEGAUGE_PROBE_S."""
    if os.environ.get("TRUEGAUGE_FABLE", "1") == "0":
        return False
    now = time.time() if now is None else now
    every = max(120, _env_int("TRUEGAUGE_PROBE_S", 600))
    d, problem = _read()
    if problem not in (None, "missing"):
        return False
    last = max(_num(d.get("probe_started_at")) or 0, _num(d.get("probe_done_at")) or 0)
    if now - last < every:
        return False
    d["probe_started_at"] = int(now)
    _save(d)
    if spawn is not None:
        spawn()
        return True
    flags = 0
    if os.name == "nt":
        flags = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED_PROCESS | NEW_PROCESS_GROUP | NO_WINDOW
    subprocess.Popen([sys.executable, os.path.abspath(__file__), "--probe-usage"], cwd=HOME,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=flags, close_fds=True)
    return True


# ---------------------------------------------------------------- session tokens

def _sess_file(session_id):
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", str(session_id or "unknown"))[:80]
    return os.path.join(SESS_DIR, safe + ".json")


def _tally_file(path, rec):
    """Read new complete lines of one JSONL; count each API message id once (a message is split over lines)."""
    st = rec.setdefault(path, {"offset": 0, "seen": [], "pending": None, "tot": [0, 0, 0, 0]})
    try:
        size = os.path.getsize(path)
    except OSError:
        return
    if size < st["offset"]:  # file replaced or truncated: start over for this file
        st.update(offset=0, seen=[], pending=None, tot=[0, 0, 0, 0])
    if size == st["offset"]:
        return
    with open(path, "rb") as f:
        f.seek(st["offset"])
        chunk = f.read()
    end = chunk.rfind(b"\n")
    if end < 0:
        return
    seen = set(st["seen"])
    pend = st["pending"]  # [id, in, cache_create, out, cache_read] -- the message still being written

    def commit(p):
        if p and p[0] not in seen:
            seen.add(p[0])
            for i in range(4):
                st["tot"][i] += p[1 + i]

    for line in chunk[:end].split(b"\n"):
        if b'"usage"' not in line:
            continue
        try:
            m = json.loads(line).get("message")
        except ValueError:
            continue
        if not isinstance(m, dict) or not isinstance(m.get("usage"), dict):
            continue
        u, mid = m["usage"], m.get("id") or ("noid-%d" % len(seen))
        vals = [int(_num(u.get(k)) or 0) for k in
                ("input_tokens", "cache_creation_input_tokens", "output_tokens", "cache_read_input_tokens")]
        if pend and pend[0] == mid:
            pend = [mid] + [max(a, b) for a, b in zip(pend[1:], vals)]
        else:
            commit(pend)
            pend = [mid] + vals
    st["offset"] += end + 1
    st["pending"] = pend
    st["seen"] = sorted(seen)


def session_tokens(transcript_path, session_id):
    """-> {'new': in+cache_create+out, 'cache_read': n, 'files': k} for the main transcript and its subagents."""
    if not isinstance(transcript_path, str) or not transcript_path.endswith(".jsonl"):
        return None
    sf = _sess_file(session_id)
    rec, problem = _read(sf)
    if problem not in (None, "missing"):
        rec = {}
    files = [transcript_path] + sorted(glob.glob(os.path.join(transcript_path[:-6], "subagents", "*.jsonl")))
    for p in files:
        if os.path.exists(p):
            _tally_file(p, rec)
    _save(rec, sf)
    new = cr = 0
    for st in rec.values():
        tot = list(st["tot"])
        p = st.get("pending")
        if p and p[0] not in set(st["seen"]):
            tot = [a + b for a, b in zip(tot, p[1:])]
        new += tot[0] + tot[1] + tot[2]
        cr += tot[3]
    return {"new": new, "cache_read": cr, "files": len(rec)}


# ---------------------------------------------------------------- rendering

WORDS = {
    "en": {"5h": "5h", "7d": "week", "left": "left", "reset": "reset", "sess": "session", "tok": "tok",
           "ctx": "context", "d": "d", "h": "h", "m": "m", "k": "k", "M": "M"},
    "ru": {"5h": "5ч", "7d": "неделя", "left": "ост.", "reset": "сброшено", "sess": "сессия", "tok": "ток.",
           "ctx": "контекст", "d": "д", "h": "ч", "m": "м", "k": " тыс.", "M": " млн"},
}


def fmt_left(sec, w):
    sec = max(0, int(sec))
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return "%d%s %d%s" % (d, w["d"], h, w["h"])
    if h:
        return "%d%s%02d%s" % (h, w["h"], m, w["m"])
    return "%d%s" % (m, w["m"])


def fmt_tokens(n, w):
    if n >= 1_000_000:
        return ("%.1f" % (n / 1e6)).rstrip("0").rstrip(".") + w["M"]
    if n >= 1000:
        return "%d%s" % (round(n / 1000), w["k"])
    return str(n)


def _pct(v):
    if v.get("status") == "fresh":
        return "%d%%" % round(v["remaining_percentage"])
    return "<=%d%%" % round(v["remaining_at_most"])


def render(st, tokens, ctx_pct, lang="en"):
    w = WORDS.get(lang, WORDS["en"])
    parts = []
    for key, tag in (("five_hour", "5h"), ("seven_day", "7d")):
        v = st.get(key)
        if not v:
            continue
        if v["status"] == "expired":
            parts.append("%s: %s" % (w[tag], w["reset"]))
        else:
            parts.append("%s: %s %s %s" % (w[tag], fmt_left(v["resets_in_s"], w), w["left"], _pct(v)))
    fb = st.get("fable")
    if fb and fb.get("status") in ("fresh", "stale_reading"):
        parts.append("F %s %s" % (w["left"], _pct(fb)))
    elif fb and fb.get("status") == "expired":
        parts.append("F %s" % w["reset"])
    if tokens is not None:
        parts.append("%s %s %s" % (w["sess"], fmt_tokens(tokens["new"], w), w["tok"]))
    if ctx_pct is not None:
        parts.append("%s %d%%" % (w["ctx"], round(ctx_pct)))
    return " | ".join(parts)


def statusline(payload, now=None):
    now = time.time() if now is None else now
    update(payload, now)
    try:
        maybe_start_probe(now)
    except Exception as e:  # the probe is an extra; its failure must not cost the main line
        _record_error("probe start: %s: %s" % (type(e).__name__, e))
    st = read_state(now)
    tokens = None
    try:
        tokens = session_tokens(payload.get("transcript_path"), payload.get("session_id"))
    except Exception as e:
        _record_error("tokens: %s: %s" % (type(e).__name__, e))
    cw = payload.get("context_window")
    ctx = _num(cw.get("used_percentage")) if isinstance(cw, dict) else None
    return render(st, tokens, ctx, os.environ.get("TRUEGAUGE_LANG", "en"))


def show():
    st = read_state()
    t = st["_tap"]
    print("truegauge %s  state: %s" % (__version__, STATE))
    print("tap: file=%s last_call_age_s=%s had_rate_limits=%s orphan_tmp=%d"
          % (t["file"], t["last_call_age_s"], t["last_call_had_rate_limits"], t["orphan_tmp"]))
    for k in ("unresolved_error", "last_rejected", "recovered_from_corrupt_at", "probe_error"):
        if t.get(k):
            print("tap: %s: %s" % (k, t[k]))
    for key in WINDOWS + ("fable",):
        v = st.get(key)
        if not v:
            continue
        if v.get("status") == "fresh":
            print("%s: used %.1f%%, remaining %.1f%%, resets in %s s, fresh"
                  % (key, v["used_percentage"], v["remaining_percentage"], v["resets_in_s"]))
        elif v.get("status") == "stale_reading":
            print("%s: used %.1f%% when last changed, remaining AT MOST %.1f%%, resets in %s s, STALE (%s)"
                  % (key, v["used_percentage"], v["remaining_at_most"], v["resets_in_s"], v["why"]))
        elif v.get("status") == "expired":
            print("%s: window ended (last seen used %.1f%%), remainder unknown until a new reading"
                  % (key, v["last_seen_used_percentage"]))
        else:
            print("%s: %s" % (key, v.get("status")))
    return 0


def main(argv):
    if "--version" in argv:
        print(__version__)
        return 0
    if "--show" in argv:
        return show()
    if "--probe-usage" in argv:
        probe_usage()
        return 0
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", "replace")
        line = statusline(json.loads(raw))
        sys.stdout.buffer.write(line.encode("utf-8"))
    except Exception as e:  # the status line must not break; the failure is recorded, not hidden
        _record_error("%s: %s" % (type(e).__name__, e))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
