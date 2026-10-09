#!/usr/bin/env python3
"""truegauge -- a status line for Claude Code that never shows an old number as a current one.

Shows one short line, e.g. `4h 38% 3d 54% F4% T548K 55% Σ2.6M` (number and unit written together):
  4h 38%   5-hour window: time left (hours, then minutes under 1 h) and the share USED
  3d 54%   weekly window: time left in whole days (0d in the last day) and the share used
  F4%      the weekly Fable window (separate allowance), share used
  T548K    tokens in the context window now
  55%      how full the context window is
  Σ2.6M    tokens spent in this session (main conversation + subagents); `Σ≥2.6M` = a lower bound
A group whose data is unknown is left out, never shown as 0. `T K M` are Cyrillic with TRUEGAUGE_LANG=ru.

Sources (all documented Claude Code surfaces, nothing scraped, no token of yours leaves Claude Code):
  * status-line stdin JSON: rate_limits.five_hour / seven_day {used_percentage, resets_at},
    context_window.used_percentage and total_input_tokens, transcript_path, session_id.
  * transcript JSONL (transcript_path + <session>/subagents/*.jsonl): per-message `usage`.
  * Fable: the status line does not carry it. truegauge runs `claude -p /usage` in the background at
    most once per TRUEGAUGE_PROBE_S seconds (default 600) and reads the line
    "Current week (Fable): N% used". Off with TRUEGAUGE_FABLE=0.

Honesty rules (the reason this tool exists):
  * A session left idle re-sends its LAST reading. Per window the reading with the larger
    (resets_at, used_percentage) wins; an equal reset with a lower percentage is a replay and is ignored.
  * Use inside a window only grows. A reading that did not change for TRUEGAUGE_MAX_AGE_S (900 s) is shown
    as "≥N%" (used at least N%, rounded down), never as a point value.
  * A window whose reset time has passed is left out until a new reading -- not the old number, not a guessed 0%.
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
import math
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


class _Lock(object):
    """Cross-process lock for read-modify-write of the state file (several sessions share one account).
    Byte lock on a side file; the OS releases it when the process dies. Gives up after ~1 s and lets the
    caller proceed: a lost update is bounded (the next payload repairs it), a frozen status line is not."""

    def __init__(self, path=None):
        self.path = (path or STATE) + ".lock"
        self.f = None

    def __enter__(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            self.f = open(self.path, "a+b")
            for _ in range(50):
                try:
                    if os.name == "nt":
                        import msvcrt
                        self.f.seek(0)
                        msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(self.f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return self
                except OSError:
                    time.sleep(0.02)
        except OSError:
            pass
        self.close()
        return self

    def close(self):
        if self.f is not None:
            try:
                self.f.close()  # closing the handle releases the byte lock
            except OSError:
                pass
            self.f = None

    def __exit__(self, *a):
        self.close()


def _record_error(msg):
    """The failure goes into the state file; if that fails too, into a capped sidecar log."""
    now = int(time.time())
    try:
        with _Lock():
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
    with _Lock():
        return _update_locked(payload, now)


def _update_locked(payload, now):
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
    seen = d.get("received_at") if isinstance(d.get("received_at"), dict) else {}
    for w, new in got.items():
        old = d.get(w)
        if not _valid(old) or (new["resets_at"], new["used_percentage"]) > (old["resets_at"], old["used_percentage"]):
            d[w], changed = new, True
        seen[w] = int(now)  # this window is still being SENT (a window no longer sent goes stale on its own)
    d["received_at"] = seen
    if changed and problem == "corrupt":
        # first sighting after a damaged state may be an idle session's replay: no fresh point until a number moves.
        # A MISSING state (first install) is taken as fresh -- otherwise every new user starts with "<=".
        d.pop("updated_at", None)
    elif changed:
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
    rec = d.get("received_at") if isinstance(d.get("received_at"), dict) else {}
    for w in WINDOWS:
        item = d.get(w)
        if _valid(item):
            r = _num(rec.get(w))
            why = "tap has an unresolved error" if unresolved else None
            if r is None or now - r > max_age_s:
                why = "this window has not been sent for over %d s" % max_age_s
            res[w] = _judge(item["used_percentage"], item["resets_at"], upd, now, max_age_s, why)
    fb = d.get("fable")
    if isinstance(fb, dict) and _num(fb.get("used_percentage")) is not None and _num(fb.get("read_at")) is not None:
        # Fable has no machine-readable reset; when /usage printed the same reset text for Fable and the week, the
        # probe stored the WEEK's resets_at as known AT PROBE TIME. A later week (larger resets_at) ends the reading.
        rst = _num(fb.get("resets_at"))
        week = d.get("seven_day")
        if rst is not None and _valid(week) and week["resets_at"] > rst + 3600:
            res["fable"] = {"status": "expired", "resets_at": int(rst), "resets_in_s": int(rst - now),
                            "last_seen_used_percentage": fb["used_percentage"], "remaining_percentage": None,
                            "why": "a newer weekly window has started since the Fable reading"}
        else:
            v = _judge(fb["used_percentage"], rst, fb["read_at"], now, max_age_s * 2 if rst is not None else max_age_s, None)
            if rst is None and v["status"] == "stale_reading":
                # without a reset time an "at most" bound may outlive the window: say nothing rather than a bound
                v = {"status": "unknown", "why": "Fable reading is old and its reset time is unknown"}
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


def _run_usage():
    """`claude -p /usage` with no settings sources (no user hooks run), no session saved, no console window.
    On timeout the WHOLE process tree is killed (on Windows `claude` is a .cmd shim: cmd.exe -> node)."""
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError("claude executable not found on PATH")
    env = dict(os.environ, MSYS_NO_PATHCONV="1")
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    else:
        kw["start_new_session"] = True
    p = subprocess.Popen([exe, "-p", "/usage", "--no-session-persistence", "--setting-sources", ""],
                         cwd=HOME, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, **kw)
    try:
        out, _ = p.communicate(timeout=60)
    except subprocess.TimeoutExpired:
        how = "process tree killed"
        try:
            if os.name == "nt":
                rc = subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    creationflags=0x08000000, timeout=15).returncode
                if rc != 0:
                    p.kill()
                    how = "taskkill exit %d; only the first process killed, children may remain" % rc
            else:
                import signal
                os.killpg(p.pid, signal.SIGKILL)
        except Exception as e:
            try:
                p.kill()
            except OSError:
                pass
            how = "tree kill failed (%s); only the first process killed, children may remain" % type(e).__name__
        try:
            p.communicate(timeout=5)
        except Exception:
            how += "; pipes still held after kill"
        raise RuntimeError("claude -p /usage did not answer in 60 s (%s)" % how)
    return (out or b"").decode("utf-8", "replace")


def probe_usage(now=None, runner=None):
    """Run /usage once and store the Fable window. runner() -> text (injectable for tests)."""
    now = time.time() if now is None else now
    os.makedirs(HOME, exist_ok=True)
    try:
        text = _run_usage() if runner is None else runner()
        got = parse_usage(text)
        with _Lock():
            d, problem = _read()
            if problem == "unreadable":
                raise OSError("state file stayed unreadable; Fable reading not stored, state kept")
            if problem == "corrupt":
                d = {"recovered_from_corrupt_at": int(now)}
            if got["fable"] is None:
                # no Fable line: the plan has no separate Fable window, or the text changed -- keep nothing old
                d["fable"] = {"absent": True, "read_at": int(now)}
                if "Current week" not in text:
                    d["probe_error"] = {"at": int(now), "msg": ("no usage lines in /usage output: %r" % text[:200])[:300]}
                else:
                    d["probe_error"] = {"at": int(now), "msg": "no 'Current week (Fable)' line in /usage output "
                                        "(no separate Fable allowance, or the text format changed)", "info": True}
            else:
                pct, rtext = got["fable"]
                if not 0 <= pct <= 100:
                    raise ValueError("Fable percentage out of range: %r" % pct)
                week = d.get("seven_day")
                same = bool(got["week"] and rtext and rtext == got["week"][1])
                fb = {"used_percentage": pct, "read_at": int(now), "reset_text": rtext, "same_reset_as_week": same}
                if same and _valid(week) and week["resets_at"] > now:
                    fb["resets_at"] = int(week["resets_at"])  # the week known AT PROBE TIME, see read_state
                d["fable"] = fb
                d.pop("probe_error", None)
            d["probe_done_at"] = int(now)
            _save(d)
            return d.get("fable")
    except Exception as e:
        try:
            with _Lock():
                d, problem = _read()
                if problem in (None, "missing"):
                    d["probe_error"] = {"at": int(now), "msg": ("%s: %s" % (type(e).__name__, e))[:300]}
                    d["probe_done_at"] = int(now)
                    _save(d)
                    return None
        except Exception:
            pass
        _record_error("probe: %s: %s" % (type(e).__name__, e))
        return None


def maybe_start_probe(now=None, spawn=None):
    """Start the /usage probe in the background when the last one is older than TRUEGAUGE_PROBE_S.
    A probe that started and has not finished blocks new ones for 300 s (it is killed at 60 s)."""
    if os.environ.get("TRUEGAUGE_FABLE", "1") == "0":
        return False
    now = time.time() if now is None else now
    every = max(120, _env_int("TRUEGAUGE_PROBE_S", 600))
    with _Lock():
        d, problem = _read()
        if problem not in (None, "missing"):
            return False
        started = _num(d.get("probe_started_at")) or 0
        done = _num(d.get("probe_done_at")) or 0
        if started > done and now - started < 300:
            return False
        if now - max(started, done) < every:
            return False
        d["probe_started_at"] = int(now)
        _save(d)
    if spawn is not None:
        spawn()
        return True
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = 0x08000000 | 0x00000200  # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    subprocess.Popen([sys.executable, os.path.abspath(__file__), "--probe-usage"], cwd=HOME,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True, **kw)
    return True


# ---------------------------------------------------------------- session tokens

READ_CAP = 8 * 1024 * 1024  # bytes read per file per call; a big transcript is finished over the next calls
FIELDS = ("input_tokens", "cache_creation_input_tokens", "output_tokens", "cache_read_input_tokens")


def _sess_file(session_id):
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", str(session_id or "unknown"))[:80]
    return os.path.join(SESS_DIR, safe + ".json")


def _file_key(path):
    """One file, one key: slash direction, drive-letter case and `.` segments must not make two records of it."""
    return os.path.normcase(os.path.abspath(path))


def _merge_spellings(rec):
    """A record written under several keys that name one file keeps the key that read furthest (it has read
    everything the others read; summing them is the double count). -> (record, changed)."""
    out, changed = {}, False
    for k, st in rec.items():
        nk = _file_key(k)
        changed = changed or nk != k
        prev = out.get(nk)
        if prev is not None:
            changed = True
            if not (isinstance(st, dict) and isinstance(prev, dict) and st.get("offset", 0) > prev.get("offset", 0)):
                continue
        out[nk] = st
    return out, changed


def _tally_file(path, rec, key=None):
    """Read new complete lines of one JSONL. Each API message id keeps the MAX of each usage field over all its
    lines (Claude Code writes one message over several lines, not always adjacent). -> True if anything changed."""
    st = rec.setdefault(key or _file_key(path), {"offset": 0, "ids": {}, "noid": 0})
    try:
        size = os.path.getsize(path)
    except OSError:
        return False
    if size < st["offset"]:  # file replaced or truncated: start over for this file
        st.update(offset=0, ids={}, noid=0)
    if size == st["offset"]:
        return False
    with open(path, "rb") as f:
        f.seek(st["offset"])
        chunk = f.read(READ_CAP)
    st["at_eof"] = len(chunk) < READ_CAP  # everything after this read is at most one line still being written
    end = chunk.rfind(b"\n")
    if end < 0:
        if len(chunk) >= READ_CAP:  # one line longer than the cap: skip it, never stall on it
            st["offset"] += len(chunk)
            st["skipped_long_lines"] = st.get("skipped_long_lines", 0) + 1
            return True
        return False
    ids = st["ids"]
    for line in chunk[:end].split(b"\n"):
        if b'"usage"' not in line:
            continue
        try:
            m = json.loads(line).get("message")
        except (ValueError, AttributeError):
            st["bad_lines"] = st.get("bad_lines", 0) + 1
            continue
        if not isinstance(m, dict) or not isinstance(m.get("usage"), dict):
            if isinstance(m, dict) and "usage" in m:
                st["bad_lines"] = st.get("bad_lines", 0) + 1
            continue
        u = m["usage"]
        vals = [int(_num(u.get(k)) or 0) for k in FIELDS]
        mid = m.get("id")
        if not isinstance(mid, str) or not mid:
            st["noid"] += 1
            mid = "noid-%d" % st["noid"]
        old = ids.get(mid)
        ids[mid] = vals if old is None else [max(a, b) for a, b in zip(old, vals)]
    st["offset"] += end + 1
    return True


def _prune_sessions(now, keep_days=14):
    """Session caches are re-buildable from the transcripts: drop caches untouched for keep_days."""
    for p in glob.glob(os.path.join(SESS_DIR, "*.json")):
        try:
            if now - os.path.getmtime(p) > keep_days * 86400:
                os.remove(p)
        except OSError:
            pass


def session_tokens(transcript_path, session_id):
    """-> {'new': in+cache_create+out, 'cache_read': n, 'files': k, 'complete': bool} for the main transcript
    and its subagents. complete=False while a large transcript is still being read (shown as '>=')."""
    if not isinstance(transcript_path, str) or not transcript_path.endswith(".jsonl"):
        return None
    sf = _sess_file(session_id)
    rec, problem = _read(sf)
    if problem not in (None, "missing"):
        rec = {}
    rec, changed = _merge_spellings(rec)
    files = {}
    for p in [transcript_path] + sorted(glob.glob(os.path.join(glob.escape(transcript_path[:-6]), "subagents", "*.jsonl"))):
        files.setdefault(_file_key(p), p)
    for k, p in files.items():
        if os.path.exists(p):
            changed = _tally_file(p, rec, k) or changed
    if changed:
        _save(rec, sf)
        if problem == "missing":  # once per new session
            _prune_sessions(time.time())
    if not any(os.path.exists(p) for p in files.values()) and not rec:
        return None  # a wrong transcript_path is not "0 tokens"
    new = cr = bad = 0
    complete = True
    for p, st in rec.items():
        for v in st["ids"].values():
            new += v[0] + v[1] + v[2]
            cr += v[3]
        bad += st.get("bad_lines", 0) + st.get("skipped_long_lines", 0)
        if not st.get("at_eof", True):
            complete = False
    if bad:
        complete = False  # an unreadable line may have carried tokens: the count is a lower bound
    return {"new": new, "cache_read": cr, "files": len(rec), "complete": complete, "bad_lines": bad}


# ---------------------------------------------------------------- rendering

WORDS = {
    "en": {"d": "d", "h": "h", "m": "m", "T": "T", "K": "K", "M": "M"},
    "ru": {"d": "d", "h": "h", "m": "m", "T": "Т", "K": "К", "M": "М"},
}
GE = "≥"
SIGMA = "Σ"


def _half_up(x):
    return int(math.floor(x + 0.5))


def fmt_left(sec, w, days_only=False):
    """Time left, one unit, rounded DOWN: >= 1 day -> days, >= 1 h -> hours, else minutes (min 0m).
    days_only (the week): always days, `0d` in the last day -- so the week group can never look like the 5-hour one."""
    sec = max(0, int(sec))
    if days_only or sec >= 86400:
        return "%d%s" % (sec // 86400, w["d"])
    if sec >= 3600:
        return "%d%s" % (sec // 3600, w["h"])
    return "%d%s" % (sec // 60, w["m"])


def fmt_tokens(n, w, down=False):
    """Unit glued to the number: <1000 -> 812; <999500 -> 548K; else 2.6M (half up, `.` separator).
    down=True for a lower bound: rounded DOWN, never above the count (`Σ≥2.6M` for 2.65M)."""
    n = max(0, int(n))
    if n < 1000:
        return str(n)
    if down:
        if n < 1_000_000:
            return "%d%s" % (n // 1000, w["K"])
        tenths = n // 100_000
        return "%d.%d%s" % (tenths // 10, tenths % 10, w["M"])
    if n < 999_500:
        return "%d%s" % ((n + 500) // 1000, w["K"])
    tenths = (n + 50_000) // 100_000
    return "%d.%d%s" % (tenths // 10, tenths % 10, w["M"])


def _used(v):
    """Used share: fresh -> the rounded number; a stale reading can only have grown, so its old value is a
    LOWER bound: `>=` and the number rounded DOWN (never above the reading). Expired -> 0 (nothing used yet)."""
    if v.get("status") == "fresh":
        return "%d%%" % _half_up(v["used_percentage"])
    return "%s%d%%" % (GE, math.floor(v["used_percentage"]))  # stale_reading (render passes nothing else)


def render(st, tokens, ctx_pct, lang="en", ctx_tokens=None):
    """`4h 38% 3d 54% F4% T548K 55% Σ2.6M`: 5-hour window, week, Fable, tokens in the window now, context fill,
    session total. A group whose data is unknown is left out, never shown as 0."""
    w = WORDS.get(lang, WORDS["en"])
    parts = []
    for key in ("five_hour", "seven_day"):
        v = st.get(key)
        # an expired window is left out: after the reset nothing is known until a new reading (usage from
        # the Claude web app or another session counts too), so "0%" would be a guess shown as a number
        if not v or v["status"] not in ("fresh", "stale_reading"):
            continue
        parts.append("%s %s" % (fmt_left(v["resets_in_s"], w, days_only=(key == "seven_day")), _used(v)))
    fb = st.get("fable")
    if fb and fb.get("status") in ("fresh", "stale_reading"):  # expired Fable: left out until the probe reads again
        parts.append("F" + _used(fb))
    ct = _num(ctx_tokens)
    no_reading = ct is not None and ct <= 0  # Claude Code sends 0 before the first reply and after /compact: no reading, not an empty window
    if ct is not None and not no_reading:
        parts.append(w["T"] + fmt_tokens(ct, w))
    if ctx_pct is not None and not no_reading:  # a fill with 0 tokens is not from a real reading either
        parts.append("%d%%" % _half_up(ctx_pct))
    if tokens is not None:
        parts.append("%s%s%s" % (SIGMA, "" if tokens.get("complete", True) else GE, fmt_tokens(tokens["new"], w, down=not tokens.get("complete", True))))
    return " ".join(parts)


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
    cti = _num(cw.get("total_input_tokens")) if isinstance(cw, dict) else None
    return render(st, tokens, ctx, os.environ.get("TRUEGAUGE_LANG", "en"), cti)


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
            print("%s: %s%s" % (key, v.get("status"), (" (%s)" % v["why"]) if v.get("why") else ""))
    d, _ = _read()
    le = d.get("last_error")
    if isinstance(le, dict) and not t.get("unresolved_error"):
        print("tap: last error (resolved since): at %s: %s" % (le.get("at"), le.get("msg")))
    try:
        with open(ERRLOG, encoding="utf-8") as f:
            tail = f.read().splitlines()[-5:]
        print("errors.log (last %d lines%s):" % (len(tail), ", log FULL -- new errors not written"
                                                if os.path.getsize(ERRLOG) >= ERRLOG_CAP else ""))
        for line in tail:
            print("  " + line)
    except OSError:
        pass
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
