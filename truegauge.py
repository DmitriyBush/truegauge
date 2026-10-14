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

Colors (0.2): a window group (time + percent) turns red when the reading has reached a threshold:
  half_up(used%) >= TRUEGAUGE_RED_5H (default 98) for the 5-hour window, TRUEGAUGE_RED_7D (default 99) for the week.
  The test is on the RAW reading, so it is the same for a fresh `98%` and an old `≥97%` (an old reading only
  grew): ageing never changes the colour. Fable, tokens and context fill are never coloured; an expired window
  has no group. Threshold: an integer 1..100, or `off` / `0` = never; empty or unset = default; anything else
  = default, and `--show` names the refusal. TRUEGAUGE_RED_COLOR: `#rrggbb` (default #ff0000) or `ansi`
  (bold red of your terminal theme). The group is wrapped in `ESC[1;..m ... ESC[22;39m`; the space between groups
  is not. NO_COLOR set to anything non-empty: no colour at all (output identical to 0.1, except for a mark set by
  TRUEGAUGE_MARK, which NO_COLOR does not remove).

Cold cache (0.3): the context group (`T548K 55%`, both parts together) turns blue when the main session's prompt
cache has gone cold. Claude Code's status-line input carries `prompt_cache` (main session only, not subagents):
cold = `caching_observed` is true AND (`warm` is false OR `expires_at` has passed). No `prompt_cache`, or a value of
the wrong type, means "not cold" (no data is never shown as cold). On a subscription the main session's cache lives
1 hour; once usage goes past the plan it is 5 minutes, so blue appears more often. The group of tokens spent in the
session (`Σ`, subagents included) is NOT coloured: the cache state is known for the main session only.
TRUEGAUGE_COLD_COLOR: `#rrggbb` (default #b1b9f9), `ansi` (bold bright blue of your theme) or `off`. The status line also
keeps the last `prompt_cache` it saw in the state file; `--show` prints it (`cache: ...`).
Mark without colour (0.3, for people who do not tell colours apart; OFF by default): TRUEGAUGE_MARK = a comma-separated list of
`underline`, `braces` (`{T548K 55%}`) and `bold`, or `all` (all three), or `off` / unset = no mark. One unknown item
refuses the whole setting (off; `--show` names the item). It is put on the same groups under the same conditions as
the colours (red window groups, cold context group) and does not depend on colour: NO_COLOR does not remove it.

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
  --install       make truegauge the status line in settings.json; the previous line is saved first.
  --uninstall     put the previous line back (or remove the key if there was none).
  --status        is truegauge the active line in that settings.json, is a previous line saved.
                  (all three take [--settings PATH]; default ~/.claude/settings.json or
                  $CLAUDE_CONFIG_DIR/settings.json. Every write is preceded by a backup copy.)
Config (environment): TRUEGAUGE_HOME (state dir, default ~/.truegauge), TRUEGAUGE_LANG (en|ru),
  TRUEGAUGE_FABLE (1|0), TRUEGAUGE_PROBE_S, TRUEGAUGE_MAX_AGE_S, TRUEGAUGE_RED_5H, TRUEGAUGE_RED_7D,
  TRUEGAUGE_RED_COLOR, TRUEGAUGE_COLD_COLOR, TRUEGAUGE_MARK, NO_COLOR.
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
from datetime import datetime

__version__ = "0.3.0"

HOME = os.environ.get("TRUEGAUGE_HOME") or os.path.join(os.path.expanduser("~"), ".truegauge")
STATE = os.path.join(HOME, "state.json")
ERRLOG = os.path.join(HOME, "errors.log")
SESS_DIR = os.path.join(HOME, "sessions")
WINDOWS = ("five_hour", "seven_day", "spend_limit")
MAX_AHEAD = {"five_hour": 6 * 3600, "seven_day": 8 * 86400, "spend_limit": 400 * 86400}
EPOCH_MIN, EPOCH_MAX = 1e9, 1e11
ERRLOG_CAP = 64 * 1024
# prompt_cache.expires_at is read as epoch SECONDS (an assumption from the documentation, no live input seen):
# 2017-07-14 .. 2100-01-01. A separate pair: the plan-window check above keeps its own bounds.
CACHE_EPOCH_MIN, CACHE_EPOCH_MAX = 1_500_000_000, 4_102_444_800
RUN_EVERY_S = 60  # how often the status line's own last-call time is refreshed in the state file
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


def update(payload, now=None, response_at=None):
    """Merge one status-line payload into the state file. Returns the state after the merge.
    response_at: epoch of the last API reply in the main transcript -- the plan numbers in the payload come from a
    reply, so they are confirmed current as of that moment even when they did not change."""
    if not isinstance(payload, dict):
        raise ValueError("status-line payload is not a JSON object")
    now = time.time() if now is None else now
    with _Lock():
        return _update_locked(payload, now, response_at)


def _update_locked(payload, now, response_at=None):
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
    ra = _num(response_at)
    if got and problem != "corrupt" and ra is not None and ra <= now + 60 and ra > (_num(d.get("updated_at")) or 0):
        d["updated_at"] = int(min(ra, now))  # a later reply re-confirms the same numbers; an idle replay does not
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


def _epoch(ts):
    """ISO time of a transcript line ("2026-10-09T08:12:33.123Z") -> epoch seconds, None if absent/unreadable."""
    if not isinstance(ts, str) or not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


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
            obj = json.loads(line)
            m = obj.get("message")
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
        ts = _epoch(obj.get("timestamp"))
        if ts is not None and ts > (st.get("last_reply_at") or 0):
            st["last_reply_at"] = ts
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
    main = rec.get(_file_key(transcript_path)) or {}
    return {"new": new, "cache_read": cr, "files": len(rec), "complete": complete, "bad_lines": bad,
            "last_reply_at": main.get("last_reply_at")}


# ---------------------------------------------------------------- rendering

WORDS = {
    "en": {"d": "d", "h": "h", "m": "m", "T": "T", "K": "K", "M": "M"},
    "ru": {"d": "d", "h": "h", "m": "m", "T": "Т", "K": "К", "M": "М"},
}
GE = "≥"
LE = "≤"  # the context group drawn from an old reading: after /compact the context only gets smaller
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


def fmt_tokens(n, w, down=False, up=False):
    """Unit glued to the number: <1000 -> 812; <999500 -> 548K; else 2.6M (half up, `.` separator).
    down=True for a lower bound: rounded DOWN, never above the count (`Σ≥2.6M` for 2.65M).
    up=True for an upper bound: rounded UP, never below the count (`≤T549K` for 548 200; 999 001 -> 1.0M)."""
    n = max(0, int(n))
    if n < 1000:
        return str(n)
    if up:
        if -(-n // 1000) < 1000:
            return "%d%s" % (-(-n // 1000), w["K"])
        tenths = -(-n // 100_000)
        return "%d.%d%s" % (tenths // 10, tenths % 10, w["M"])
    if down:
        if n < 1_000_000:
            return "%d%s" % (n // 1000, w["K"])
        tenths = n // 100_000
        return "%d.%d%s" % (tenths // 10, tenths % 10, w["M"])
    if n < 999_500:
        return "%d%s" % ((n + 500) // 1000, w["K"])
    tenths = (n + 50_000) // 100_000
    return "%d.%d%s" % (tenths // 10, tenths % 10, w["M"])


def _pct_int(x, floor=False):
    """The ONE place that turns a share into the whole percent: half up (the number shown for a fresh reading, and
    the number the colour test uses) or rounded down (the lower bound shown for an old reading)."""
    return math.floor(x) if floor else _half_up(x)


def _used(v):
    """Used share: fresh -> the rounded number; a stale reading can only have grown, so its old value is a
    LOWER bound: `>=` and the number rounded DOWN (never above the reading). Expired -> 0 (nothing used yet)."""
    stale = v.get("status") != "fresh"  # render passes nothing but fresh and stale_reading
    return "%s%d%%" % (GE if stale else "", _pct_int(v["used_percentage"], floor=stale))


# ---------------------------------------------------------------- colour (0.2)

RED_VARS = {"five_hour": ("TRUEGAUGE_RED_5H", 98), "seven_day": ("TRUEGAUGE_RED_7D", 99)}
RED_RESET = "\033[22;39m"  # bold off + default foreground only: any other style the terminal had stays
RED_ANSI = "\033[1;31m"
RED_DEFAULT_RGB = "#ff0000"
COLD_DEFAULT_RGB = "#b1b9f9"  # blue of Claude Code's own "/clear to save" hint (theme key suggestion, dark theme): the context group when the main session's prompt cache is cold (0.3)
COLD_ANSI = "\033[1;94m"
MARK_ITEMS = ("underline", "braces", "bold")  # TRUEGAUGE_MARK (0.3): marks that do not need colour; canonical order
UL_ON, UL_OFF = "\033[4m", "\033[24m"  # underline on / off (off touches nothing else)
BOLD_ON, BOLD_OFF = "\033[1m", "\033[22m"  # bold on / off (off touches nothing else)


def _threshold(raw, default):
    """-> (threshold | None for off, refusal | None). Unset or empty = default, silently (an empty value is how Claude
    Code cancels a variable); spaces at the edges are cut."""
    s = (raw or "").strip()
    if not s:
        return default, None
    if s.lower() == "off" or s == "0":
        return None, None
    if re.fullmatch(r"[0-9]{1,3}", s) and 1 <= int(s) <= 100:
        return int(s), None
    return default, "%r (an integer 1..100, or off / 0)" % raw


def _rgb(hexcolor):
    return "\033[1;38;2;%d;%d;%dm" % tuple(int(hexcolor[i:i + 2], 16) for i in (1, 3, 5))


def _sgr(raw):
    """-> (escape sequence, refusal | None) for TRUEGAUGE_RED_COLOR."""
    s = (raw or "").strip()
    if not s:
        return _rgb(RED_DEFAULT_RGB), None
    if s.lower() == "ansi":
        return RED_ANSI, None
    if re.fullmatch(r"#[0-9a-fA-F]{6}", s):
        return _rgb(s), None
    return _rgb(RED_DEFAULT_RGB), "%r (#rrggbb or ansi)" % raw


def _cold_sgr(raw):
    """-> (escape sequence | None for off, refusal | None) for TRUEGAUGE_COLD_COLOR. Unset or empty = default, silently."""
    s = (raw or "").strip()
    if not s:
        return _rgb(COLD_DEFAULT_RGB), None
    if s.lower() in ("off", "0"):
        return None, None
    if s.lower() == "ansi":
        return COLD_ANSI, None
    if re.fullmatch(r"#[0-9a-fA-F]{6}", s):
        return _rgb(s), None
    return _rgb(COLD_DEFAULT_RGB), "%r (#rrggbb, ansi or off)" % raw


def _mark(raw):
    """-> (mark, refusal | None) for TRUEGAUGE_MARK. mark = "off" or the chosen items in canonical order joined by
    commas (`underline,braces,bold`). The setting is a comma-separated list of underline, braces, bold, or `all`, or
    `off`; case and spaces around items do not matter, repeats are fine. Unset or empty = off, silently. ONE unknown
    item refuses the whole setting (off + the item's name): a part of it is never switched on quietly."""
    s = (raw or "").strip().lower()
    if not s:
        return "off", None
    if s == "off":
        return "off", None
    items = [x.strip() for x in s.split(",")]
    bad = [x for x in items if x != "all" and x not in MARK_ITEMS]
    if bad:
        return "off", "%r: unknown item %s (underline, braces, bold, all, or off)" % (raw, ", ".join(repr(x) for x in bad))
    chosen = set(MARK_ITEMS) if "all" in items else set(items)
    return ",".join(x for x in MARK_ITEMS if x in chosen), None


def _mark_valid(m):
    """A stored/offered mark is "off" or a canonical list of MARK_ITEMS."""
    return isinstance(m, str) and (m == "off" or (bool(m) and all(x in MARK_ITEMS for x in m.split(","))))


def color_config(environ=None):
    """The colour settings as THIS process sees them: {'no_color': bool, 'th': {window: n | None}, 'sgr': str,
    'cold_sgr': str | None, 'mark': str, 'refused': {variable: why}}."""
    env = os.environ if environ is None else environ
    refused, th = {}, {}
    for w, (var, default) in RED_VARS.items():
        th[w], bad = _threshold(env.get(var), default)
        if bad:
            refused[var] = bad
    sgr, bad = _sgr(env.get("TRUEGAUGE_RED_COLOR"))
    if bad:
        refused["TRUEGAUGE_RED_COLOR"] = bad
    cold_sgr, bad = _cold_sgr(env.get("TRUEGAUGE_COLD_COLOR"))
    if bad:
        refused["TRUEGAUGE_COLD_COLOR"] = bad
    mark, bad = _mark(env.get("TRUEGAUGE_MARK"))
    if bad:
        refused["TRUEGAUGE_MARK"] = bad
    return {"no_color": bool(env.get("NO_COLOR")), "th": th, "sgr": sgr, "cold_sgr": cold_sgr, "mark": mark,
            "refused": refused}


def _reached(v, threshold):
    """The window's RAW reading has reached the threshold. Same answer for a fresh and an old reading of the same
    numbers: ageing does not change the colour."""
    return threshold is not None and _pct_int(v["used_percentage"]) >= threshold


def _cache_cold(pc, now):
    """The ONE decision "the main session's prompt cache has gone cold" (the line and --show both use it).
    Cold = `prompt_cache` is an object, caching_observed is True, and (warm is False OR expires_at is a number
    that has passed). No data, a wrong type, or caching not observed = not cold: a guess is not shown as a reading.
    warm that is not a bool is unknown, so it decides nothing; expires_at that is not a number of epoch seconds (see _expiry) is not judged."""
    if not isinstance(pc, dict) or pc.get("caching_observed") is not True:
        return False
    if pc.get("warm") is False:
        return True
    exp = _expiry(pc.get("expires_at"))[0]
    return exp is not None and now >= exp


def _expiry(v):
    """-> (epoch seconds | None, why | None). Only a finite number inside CACHE_EPOCH_MIN..CACHE_EPOCH_MAX counts as
    a time; milliseconds (or anything else) are "unknown" with the reason, never compared with `now`."""
    n = _num(v)
    if n is None:
        return None, "expires_at: type %s" % type(v).__name__
    if not CACHE_EPOCH_MIN <= n <= CACHE_EPOCH_MAX:
        return None, "expires_at %r is outside the range of epoch seconds" % (v,)
    return n, None


def _cache_verdict(pc, now):
    """-> ("yes" | "no" | "unknown", why | None) for --show. "yes" is exactly _cache_cold; "unknown" is a cache whose
    state the input does not let us tell (no object, caching_observed not a bool, no usable expires_at)."""
    if _cache_cold(pc, now):
        return "yes", None
    if not isinstance(pc, dict):
        return "unknown", "no prompt_cache object"
    obs = pc.get("caching_observed")
    if obs is False:
        return "no", "caching not observed"
    if obs is not True:
        return "unknown", "caching_observed: " + ("absent" if obs is None else "type %s" % type(obs).__name__)
    exp, why = _expiry(pc.get("expires_at"))
    if exp is None:
        return "unknown", why
    return "no", None


def _decorate(group, paint, mark):
    """Colour and/or mark around one group. Order: colour, bold (only when there is no colour: the colour sequence
    already carries bold), underline, `{`, text, `}`, underline off, then colour reset (it also ends bold) or, without
    colour, bold off. paint: the colour sequence or None; mark: "off" or a canonical list (see _mark). The group is
    joined by `+`, never used as a format template: its text may hold braces or percent signs."""
    items = () if mark == "off" else mark.split(",")
    bold = "bold" in items
    if "braces" in items:
        group = "{" + group + "}"
    if "underline" in items:
        group = UL_ON + group + UL_OFF
    if paint:
        return paint + group + RED_RESET
    if bold:
        return BOLD_ON + group + BOLD_OFF
    return group


def render(st, tokens, ctx_pct, lang="en", ctx_tokens=None, cold=False, ctx_last=None):
    """`4h 38% 3d 54% F4% T548K 55% Σ2.6M`: 5-hour window, week, Fable, tokens in the window now, context fill,
    session total. A group whose data is unknown is left out, never shown as 0. cold: the main session's cache has
    gone cold (see _cache_cold) -- the context group is drawn blue. ctx_last: the saved reading of THIS session (see
    _stale_context); used only when Claude Code sends no reading now, drawn with `<=` (an upper bound)."""
    w = WORDS.get(lang, WORDS["en"])
    try:
        cc = color_config()
    except Exception as e:  # SF4 config: a colour fault costs the colour, never the line
        _record_error("colour config: %s: %s" % (type(e).__name__, e))
        cc = None
    parts = []
    for key in ("five_hour", "seven_day"):
        v = st.get(key)
        # an expired window is left out: after the reset nothing is known until a new reading (usage from
        # the Claude web app or another session counts too), so "0%" would be a guess shown as a number
        if not v or v["status"] not in ("fresh", "stale_reading"):
            continue
        group = "%s %s" % (fmt_left(v["resets_in_s"], w, days_only=(key == "seven_day")), _used(v))
        if cc is not None:
            try:
                if _reached(v, cc["th"][key]):
                    group = _decorate(group, None if cc["no_color"] else cc["sgr"], cc["mark"])
            except Exception as e:  # SF4 paint: the group stays without colour
                _record_error("colour paint: %s: %s" % (type(e).__name__, e))
        parts.append(group)
    fb = st.get("fable")
    if fb and fb.get("status") in ("fresh", "stale_reading"):  # expired Fable: left out until the probe reads again
        parts.append("F" + _used(fb))
    ct = _num(ctx_tokens)
    no_reading = ct is not None and ct <= 0  # Claude Code sends 0 before the first reply and after /compact: no reading, not an empty window
    ctx = []
    if ct is not None and not no_reading:
        ctx.append(w["T"] + fmt_tokens(ct, w))
    if ctx_pct is not None and not no_reading:  # a fill with 0 tokens is not from a real reading either
        ctx.append("%d%%" % _half_up(ctx_pct))
    if no_reading and _context_valid(ctx_last):  # the old number is an upper bound: the context only shrank since
        ctx.append(LE + w["T"] + fmt_tokens(ctx_last["tokens"], w, up=True))
        if ctx_last["pct"] is not None:
            ctx.append("%s%d%%" % (LE, math.ceil(ctx_last["pct"])))
    group = " ".join(ctx)
    if group and cold and cc is not None:  # nothing to paint when there is no reading
        try:
            group = _decorate(group, None if cc["no_color"] else cc["cold_sgr"], cc["mark"])
        except Exception as e:  # SF4 cold paint: the group stays without colour
            _record_error("cold paint: %s: %s" % (type(e).__name__, e))
    if group:
        parts.append(group)
    if tokens is not None:
        parts.append("%s%s%s" % (SIGMA, "" if tokens.get("complete", True) else GE, fmt_tokens(tokens["new"], w, down=not tokens.get("complete", True))))
    return " ".join(parts)


def _same(a, b):
    """Equal as JSON text: `True == 1` and `1 == 1.0` in Python, but a type change is a change of the record."""
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def _cfg_apply(d, cc, now):
    """Put the colour settings the status line ACTUALLY used into the state dict `d` (its `env` block in settings.json
    is invisible to a shell, so --show cannot recompute them). Only when they differ from the saved ones: the line runs
    often. -> (changed, error text | None); the text is set for a NEW non-empty refusal, once per change."""
    cfg = {"th": dict(cc["th"]), "sgr": cc["sgr"], "cold_sgr": cc["cold_sgr"], "mark": cc["mark"],
           "no_color": bool(cc["no_color"]), "refused": dict(cc["refused"])}
    old = d.get("render_cfg")
    if _same(old, cfg):
        return False, None
    d["render_cfg"] = cfg
    d["render_cfg_at"] = int(now)
    new_refusal = bool(cfg["refused"]) and (not isinstance(old, dict) or old.get("refused") != cfg["refused"])
    if not new_refusal:
        return True, None
    return True, ("colour setting refused (default used): %s"
                  % "; ".join("%s=%s" % kv for kv in sorted(cfg["refused"].items())))


def _remember_render_cfg(cc, now):
    """One colour-settings record under its own lock (the status line itself uses _remember: one lock for both)."""
    with _Lock():
        d, problem = _read()
        if problem not in (None, "missing"):
            return
        changed, err = _cfg_apply(d, cc, now)
        if changed:
            _save(d)
    if err:
        _record_error(err)


def _pc_value(v):
    """One field of prompt_cache for the record: simple values as they are (the type is the evidence), anything else
    as its type name, long text cut."""
    if v is None or isinstance(v, (bool, int)):
        return v
    if isinstance(v, float):
        return v if _num(v) is not None else "<float nan/inf>"
    if isinstance(v, str):
        return v[:32]
    return "<%s>" % type(v).__name__


PC_FIELDS = ("warm", "caching_observed", "ttl", "expires_at", "recache_tokens_if_cold")


def _sid8(payload):
    """The first 8 characters of the payload's session_id (a short tag to tell sessions apart), else None."""
    sid = payload.get("session_id") if isinstance(payload, dict) else None
    return re.sub(r"[^A-Za-z0-9_-]", "_", sid)[:8] if isinstance(sid, str) and sid else None


def _cache_apply(d, payload, now):
    """Instrument for the first-hand check of the input: keep the LAST `prompt_cache` the status line saw, compactly
    (`present` False when there is no object). `prompt_cache_seen` is rewritten only when it differs from the saved one
    (not counting seen_at, the time of that CHANGE): the line runs often, and expires_at moves only with a new reply.
    `prompt_cache_run` = the status line's own last call {at, sid, changed_sid}, refreshed at most once per RUN_EVERY_S
    (or with a change of the record): `at` is accurate to that step; sid = the session that made the call, changed_sid =
    the session that made the last change of the record (the two can differ: the state file is shared by all sessions).
    -> True when `d` was modified."""
    pc = payload.get("prompt_cache") if isinstance(payload, dict) else None
    rec = {"present": isinstance(pc, dict)}
    for k in PC_FIELDS:
        rec[k] = _pc_value(pc.get(k)) if isinstance(pc, dict) else None
    sid = _sid8(payload)
    old = d.get("prompt_cache_seen")
    run = d.get("prompt_cache_run")
    run_ok = isinstance(run, dict) and _num(run.get("at")) is not None
    rec_changed = not (isinstance(old, dict) and _same({k: v for k, v in old.items() if k != "seen_at"}, rec))
    if rec_changed:
        d["prompt_cache_seen"] = dict(rec, seen_at=int(now))
    if rec_changed or not run_ok or not 0 <= now - run["at"] < RUN_EVERY_S:
        d["prompt_cache_run"] = {"at": int(now), "sid": sid,
                                 "changed_sid": sid if rec_changed else (run.get("changed_sid") if run_ok else None)}
        return True
    return False


def _remember_cache(payload, now):
    """One cache record under its own lock (the status line itself uses _remember: one lock for both)."""
    with _Lock():
        d, problem = _read()
        if problem not in (None, "missing"):
            return
        if _cache_apply(d, payload, now):
            _save(d)


def _context_valid(rec):
    """The saved context reading has the expected shape: tokens a number > 0, pct present and a number or None, sid a
    text, at a number, stale_since absent / None / a number. Anything else is "no reading". pct must be PRESENT: render,
    _context_apply and --show read rec["pct"] directly (the line itself always writes the key)."""
    return (isinstance(rec, dict) and _num(rec.get("tokens")) is not None and rec["tokens"] > 0
            and "pct" in rec and (rec["pct"] is None or _num(rec["pct"]) is not None)
            and isinstance(rec.get("sid"), str) and bool(rec["sid"]) and _num(rec.get("at")) is not None
            and (rec.get("stale_since") is None or _num(rec.get("stale_since")) is not None))


def _context_apply(d, payload, now):
    """Keep the LAST real context reading (tokens > 0) of a session: {tokens, pct, sid, at[, stale_since]}. Written only
    when the reading CHANGED (tokens, pct or session; `at` is the time of that change). A call with no reading (0 tokens,
    before the first reply and after /compact) of the SAME session sets `stale_since` once, when the line starts to draw
    the old reading as an upper bound. A payload without a usable session tag is not kept: it could never be matched.
    -> True when `d` was modified."""
    cw = payload.get("context_window") if isinstance(payload, dict) else None
    if not isinstance(cw, dict):
        return False
    sid = _sid8(payload)
    tok = _num(cw.get("total_input_tokens"))
    if sid is None or tok is None:
        return False
    old = d.get("context_seen")
    if tok > 0:
        rec = {"tokens": tok, "pct": _num(cw.get("used_percentage")), "sid": sid}
        if _context_valid(old) and old.get("stale_since") is None and _same({k: old[k] for k in rec}, rec):
            return False
        d["context_seen"] = dict(rec, at=int(now))
        return True
    if _context_valid(old) and old["sid"] == sid and old.get("stale_since") is None:
        d["context_seen"] = dict(old, stale_since=int(now))
        return True
    return False


def _stale_context(sid):
    """The saved reading when it is this session's and well-formed, else None. A damaged record is "no reading" and is
    recorded as an error once (the same text is not written again while it stands: the line runs often)."""
    d, problem = _read()
    if problem not in (None, "missing"):
        return None
    rec = d.get("context_seen")
    if rec is None:
        return None
    if not _context_valid(rec):
        msg = "context record: damaged, treated as no reading"
        le = d.get("last_error")
        if not (isinstance(le, dict) and le.get("msg") == msg):
            _record_error(msg)
        return None
    return rec if sid is not None and rec["sid"] == sid else None


def _remember(payload, cc, now):
    """What the status line keeps in the state file on EVERY call: its colour settings and the last prompt_cache.
    ONE lock, one read, one write (a new writer on every reply must not triple the lock traffic). The two parts are
    isolated: a fault in one costs that record, the other is still written; the faults are recorded after the lock."""
    errs = []
    with _Lock():
        d, problem = _read()
        if problem not in (None, "missing"):
            return
        changed = False
        if cc is not None:
            try:
                c, err = _cfg_apply(d, cc, now)
                changed = changed or c
                if err:
                    errs.append(err)
            except Exception as e:  # an extra; its failure must not cost the main line
                errs.append("colour settings: %s: %s" % (type(e).__name__, e))
        try:
            changed = _cache_apply(d, payload, now) or changed
        except Exception as e:  # an extra (the measuring instrument); its failure must not cost the main line
            errs.append("cache record: %s: %s" % (type(e).__name__, e))
        try:
            changed = _context_apply(d, payload, now) or changed
        except Exception as e:  # an extra; its failure costs the old context reading, never the line
            errs.append("context record: %s: %s" % (type(e).__name__, e))
        if changed:
            _save(d)
    for e in errs:
        _record_error(e)


def _saved_render_cfg(d):
    """-> (cfg, at) from the state dict, or (None, None) when absent or not of the expected shape."""
    c = d.get("render_cfg")
    try:
        ok = (isinstance(c, dict) and isinstance(c["sgr"], str) and isinstance(c["no_color"], bool)
              and isinstance(c["refused"], dict) and isinstance(c["th"], dict)
              and all(c["th"].get(k) is None or isinstance(c["th"][k], int) for k in RED_VARS)
              and all(k in c["th"] for k in RED_VARS)
              # 0.3 keys: absent in a record written by 0.2 (then --show says nothing about them), wrong shape = damaged
              and (c.get("cold_sgr") is None or isinstance(c["cold_sgr"], str)) and _mark_valid(c.get("mark", "off")))
    except (KeyError, TypeError):
        ok = False
    return (c, _num(d.get("render_cfg_at"))) if ok else (None, None)


def statusline(payload, now=None):
    now = time.time() if now is None else now
    tokens = None
    try:
        tokens = session_tokens(payload.get("transcript_path"), payload.get("session_id"))
    except Exception as e:
        _record_error("tokens: %s: %s" % (type(e).__name__, e))
    update(payload, now, (tokens or {}).get("last_reply_at"))
    try:
        maybe_start_probe(now)
    except Exception as e:  # the probe is an extra; its failure must not cost the main line
        _record_error("probe start: %s: %s" % (type(e).__name__, e))
    try:
        cc = color_config()
    except Exception as e:  # an extra; its failure must not cost the main line
        _record_error("colour settings: %s: %s" % (type(e).__name__, e))
        cc = None
    try:
        _remember(payload, cc, now)
    except Exception as e:  # the state file could not be read or written: the main line stays
        _record_error("state record: %s: %s" % (type(e).__name__, e))
    try:
        cold = _cache_cold(payload.get("prompt_cache"), now)
    except Exception as e:  # SF4 cold test: no colour, the line stays
        _record_error("cold test: %s: %s" % (type(e).__name__, e))
        cold = False
    st = read_state(now)
    cw = payload.get("context_window")
    ctx = _num(cw.get("used_percentage")) if isinstance(cw, dict) else None
    cti = _num(cw.get("total_input_tokens")) if isinstance(cw, dict) else None
    last = None
    if cti is not None and cti <= 0:  # no reading now: the same session's saved one may be drawn as an upper bound
        try:
            last = _stale_context(_sid8(payload))
        except Exception as e:  # SF4 context record: no group, the line stays
            _record_error("context record: %s: %s" % (type(e).__name__, e))
    return render(st, tokens, ctx, os.environ.get("TRUEGAUGE_LANG", "en"), cti, cold, last)


def _th_name(n):
    return "off" if n is None else str(n)


def _fmt_time(t):
    """A local date and time for an epoch value; a value that is not a time (huge, negative, odd) is shown as it is."""
    try:
        return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError, TypeError):
        return "? (%r is not a time)" % (t,)


def _show_cache(d, now=None):
    """The `cache:` line: what the status line last saw as prompt_cache, and whether that reads as cold now
    (yes / no / unknown with the reason). The record's time is the time of its last CHANGE; the status line's last
    call is the next line (accurate to RUN_EVERY_S; each carries the tag of the session that made it)."""
    now = time.time() if now is None else now
    rec = d.get("prompt_cache_seen")
    if rec is None:
        print("cache: no record yet")
        return
    if not isinstance(rec, dict) or _num(rec.get("seen_at")) is None or not isinstance(rec.get("present"), bool):
        print("cache: the record is damaged; the status line rewrites it on its next call")
        return
    run = d.get("prompt_cache_run")
    run = run if isinstance(run, dict) and _num(run.get("at")) is not None else None
    at = _fmt_time(rec["seen_at"])
    sid = "" if run is None or not run.get("changed_sid") else " (session %s)" % run["changed_sid"]
    if not rec["present"]:
        print("cache: as the status line saw it at %s%s: present=False (no prompt_cache in its input), cold now: unknown "
              "(no prompt_cache object)" % (at, sid))
    else:
        exp, why = _expiry(rec.get("expires_at"))
        if exp is None:
            when = "expires_at=%r" % (rec.get("expires_at"),)
        elif now >= exp:
            when = "expired %d s ago" % int(now - exp)
        else:
            when = "expires in %d s" % int(exp - now)
        verdict, vwhy = _cache_verdict(rec, now)
        print("cache: as the status line saw it at %s%s: present=True, caching_observed=%r, warm=%r, ttl=%r, %s, "
              "recache_if_cold=%r, cold now: %s"
              % (at, sid, rec.get("caching_observed"), rec.get("warm"), rec.get("ttl"), when,
                 rec.get("recache_tokens_if_cold"), verdict + (" (%s)" % vwhy if vwhy else "")))
    if run is None:
        print("status line last call: not recorded yet (the record above changed at %s)" % at)
    else:
        print("status line last call: %s (accurate to %d s; session %s); record changed: %s"
              % (_fmt_time(run["at"]), RUN_EVERY_S, run.get("sid") or "?", at))


def _fmt_ctx(rec):
    """`T548K 55%` (the percent left out when it was not sent), numbers as the line draws them."""
    w = WORDS["en"]
    return " ".join([w["T"] + fmt_tokens(rec["tokens"], w)] + (["%d%%" % _half_up(rec["pct"])] if rec["pct"] is not None else []))


def _show_context(d):
    """The `context:` line: whether the status line draws the context group from a live reading or from an old one
    (stale_since is set by the line itself at the first call without a reading)."""
    rec = d.get("context_seen")
    if rec is None:
        print("context: no reading")
    elif not _context_valid(rec):
        print("context: no reading (the saved record is damaged; the status line rewrites it with its next real reading)")
    elif rec.get("stale_since") is None:
        print("context: live")
    else:
        print("context: last reading %s (session %s) at %s; stale since %s"
              % (_fmt_ctx(rec), rec["sid"], _fmt_time(rec["at"]), _fmt_time(rec["stale_since"])))


def show():
    st = read_state()
    t = st["_tap"]
    print("truegauge %s  state: %s" % (__version__, STATE))
    print("tap: file=%s last_call_age_s=%s had_rate_limits=%s orphan_tmp=%d"
          % (t["file"], t["last_call_age_s"], t["last_call_had_rate_limits"], t["orphan_tmp"]))
    for k in ("unresolved_error", "last_rejected", "recovered_from_corrupt_at", "probe_error"):
        if t.get(k):
            print("tap: %s: %s" % (k, t[k]))
    d, _ = _read()
    rc, rc_at = _saved_render_cfg(d)
    cc = color_config()
    print("colour: thresholds 5h=%s week=%s, colour %s%s%s"
          % (_th_name(cc["th"]["five_hour"]), _th_name(cc["th"]["seven_day"]), cc["sgr"].replace("\033", "ESC"),
             "; NO_COLOR is set: no colour" if cc["no_color"] else "",
             "; REFUSED (default used): %s" % "; ".join("%s=%s" % kv for kv in sorted(cc["refused"].items()))
             if cc["refused"] else ""))
    print("cold colour: %s; mark: %s" % ("off" if cc["cold_sgr"] is None else cc["cold_sgr"].replace("\033", "ESC"), cc["mark"]))
    print("colour: values of THIS shell; an `env` block in settings.json is seen only by the status line itself")
    if rc is None:
        print("status line colour: unknown (the status line has not recorded its colour settings yet)")
    else:
        print("status line colour: thresholds 5h=%s week=%s, colour %s%s%s -- as the status line saw it at %s"
              % (_th_name(rc["th"]["five_hour"]), _th_name(rc["th"]["seven_day"]), rc["sgr"].replace("\033", "ESC"),
                 "; NO_COLOR is set: no colour" if rc["no_color"] else "",
                 "; REFUSED (default used): %s" % "; ".join("%s=%s" % kv for kv in sorted(rc["refused"].items()))
                 if rc["refused"] else "",
                 "?" if rc_at is None else datetime.fromtimestamp(rc_at).strftime("%Y-%m-%d %H:%M:%S")))
    if rc is not None and "mark" in rc:
        print("status line cold colour: %s; mark: %s"
              % ("off" if rc.get("cold_sgr") is None else rc["cold_sgr"].replace("\033", "ESC"), rc["mark"]))
    _show_cache(d)
    _show_context(d)
    judge = rc  # the status line's own settings: the shell's differ whenever `env` in settings.json is used
    for key in WINDOWS + ("fable",):
        v = st.get(key)
        if not v:
            continue
        if key in RED_VARS and v.get("status") in ("fresh", "stale_reading"):
            if judge is None:
                verdict = "unknown (the status line has not recorded its colour settings yet)"
            else:
                verdict = "yes" if _reached(v, judge["th"][key]) else "no"
                if verdict == "yes" and judge["no_color"]:
                    verdict += " (not drawn: NO_COLOR)"
            print("%s: past the red threshold: %s" % (key, verdict))
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


# ---- install / uninstall: switch the status line in settings.json on and off, with a way back
PREV_NAME = "previous_statusline.json"


class Refuse(Exception):
    """The operation is refused and nothing was changed."""


def _settings_path(argv):
    for i, a in enumerate(argv):
        if a == "--settings":
            if i + 1 >= len(argv) or argv[i + 1].startswith("--"):
                raise Refuse("--settings needs a path")
            return argv[i + 1]
        if a.startswith("--settings="):
            return a[len("--settings="):]
    cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    return os.path.join(cfg or os.path.join(os.path.expanduser("~"), ".claude"), "settings.json")


def _read_settings(path):
    """The settings object, or {} for a missing/empty file. Anything else that is not a JSON object: refuse."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        return {}
    except OSError as e:
        raise Refuse("cannot read %s: %s" % (path, e))
    try:
        text = raw.decode("utf-8-sig")  # strict: a byte that is not UTF-8 must not be turned into U+FFFD and written back
    except UnicodeDecodeError as e:
        raise Refuse("%s is not valid UTF-8 (%s); nothing changed" % (path, e))
    if not text.strip():
        return {}
    try:
        d = json.loads(text)
    except ValueError as e:
        raise Refuse("%s is not valid JSON (%s); nothing changed" % (path, e))
    if not isinstance(d, dict):
        raise Refuse("%s is not a JSON object; nothing changed" % path)
    return d


def _is_ours(line):
    cmd = line.get("command") if isinstance(line, dict) else None
    return isinstance(cmd, str) and re.search(r"truegauge\.py", cmd) is not None


def _prev_file():
    return os.path.join(HOME, PREV_NAME)


def _prev_load():
    """{settings abspath: {"had_line": bool, "statusLine": value, "saved_at": iso}}; missing file -> {}."""
    try:
        with open(_prev_file(), encoding="utf-8") as f:
            d = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        raise Refuse("saved previous line %s is unreadable (%s); nothing changed" % (_prev_file(), e))
    ent = d.get("entries") if isinstance(d, dict) else None
    if not isinstance(ent, dict):
        raise Refuse("saved previous line %s has an unknown shape; nothing changed" % _prev_file())
    return ent


def _write_atomic(path, text):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = "%s.tmp-%d" % (path, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _prev_store(entries):
    if entries:
        _write_atomic(_prev_file(), json.dumps({"entries": entries}, ensure_ascii=False, indent=2) + "\n")
    else:
        try:
            os.remove(_prev_file())
        except FileNotFoundError:
            pass


def _backup(path):
    """Copy of the settings file next to it, made before every write. None if there is no file yet."""
    if not os.path.exists(path):
        return None
    base = "%s.truegauge-backup-%s" % (path, time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
    dst, n = base, 1
    while os.path.exists(dst):
        n += 1
        dst = "%s-%d" % (base, n)
    try:
        shutil.copy2(path, dst)
    except OSError as e:
        raise Refuse("cannot make a backup of %s (%s); nothing changed" % (path, e))
    return dst


def _write_settings(path, d):
    bak = _backup(path)
    _write_atomic(path, json.dumps(d, ensure_ascii=False, indent=2) + "\n")
    return bak


def _quote(p):
    return '"%s"' % p if re.search(r"\s", p) else p


def _our_command():
    py = (sys.executable or "python").replace("\\", "/")
    return "%s %s" % (_quote(py), _quote(os.path.abspath(__file__).replace("\\", "/")))


def install(path):
    d = _read_settings(path)
    key = os.path.abspath(path)
    if "statusLine" in d and _is_ours(d["statusLine"]):
        print("truegauge is already the status line in %s. Nothing changed." % path)
        return 0
    entries = _prev_load()
    entries[key] = {"had_line": "statusLine" in d, "statusLine": d.get("statusLine"),
                    "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _prev_store(entries)
    d["statusLine"] = {"type": "command", "command": _our_command(), "refreshInterval": 60}
    bak = _write_settings(path, d)
    rec = entries[key]
    print("truegauge is now the status line in %s" % path)
    print("previous line: %s (saved in %s)" % (json.dumps(rec["statusLine"], ensure_ascii=False)
                                                if rec["had_line"] else "none", _prev_file()))
    if bak:
        print("backup of the settings file: %s" % bak)
    print("to undo: python %s --uninstall --settings %s" % (_quote(os.path.abspath(__file__).replace("\\", "/")),
                                                             _quote(path)))
    print("Open a new Claude Code session to see it.")
    return 0


def _latest_backup(path):
    found = glob.glob(glob.escape(path) + ".truegauge-backup-*")
    return max(found, key=lambda p: (os.path.getmtime(p), p)) if found else None


def uninstall(path, force=False):
    d = _read_settings(path)
    key = os.path.abspath(path)
    if "statusLine" not in d or not _is_ours(d["statusLine"]):
        print("truegauge is not the status line in %s. Nothing changed." % path)
        return 0
    entries = _prev_load()
    rec = entries.get(key)
    if rec is None and not force:
        bak = _latest_backup(path)
        raise Refuse("truegauge is the status line in %s but there is no saved previous line for it (the saved "
                     "file %s may be lost or from another TRUEGAUGE_HOME); removing the key would drop the line "
                     "silently. Newest backup of the settings file: %s. To remove the key anyway: add --force"
                     % (path, _prev_file(), bak or "none found next to the file"))
    if isinstance(rec, dict) and rec.get("had_line"):
        d["statusLine"] = rec.get("statusLine")
        what = "previous line restored: %s" % json.dumps(rec.get("statusLine"), ensure_ascii=False)
    else:
        del d["statusLine"]
        what = ("there was no status line before: key removed" if rec else
                "no saved previous line (truegauge was added by hand): key removed")
    bak = _write_settings(path, d)
    entries.pop(key, None)
    _prev_store(entries)  # only after the settings write succeeded
    print("truegauge is off in %s" % path)
    print(what)
    if bak:
        print("backup of the settings file: %s" % bak)
    return 0


def status(path):
    d = _read_settings(path)
    ours = "statusLine" in d and _is_ours(d["statusLine"])
    rec = _prev_load().get(os.path.abspath(path))
    print("settings: %s%s" % (path, "" if os.path.exists(path) else " (does not exist)"))
    print("truegauge is the active status line: %s" % ("yes" if ours else "no"))
    if not ours and "statusLine" in d:
        print("active line: %s" % json.dumps(d["statusLine"], ensure_ascii=False))
    if rec is None:
        print("previous line saved: no")
    elif rec.get("had_line"):
        print("previous line saved: %s" % json.dumps(rec.get("statusLine"), ensure_ascii=False))
    else:
        print("previous line saved: there was none (--uninstall will remove the key)")
    return 0


def settings_mode(argv):
    try:
        path = _settings_path(argv)
        if "--install" in argv:
            return install(path)
        if "--uninstall" in argv:
            return uninstall(path, force="--force" in argv)
        return status(path)
    except Refuse as e:
        print("truegauge: refused: %s" % e, file=sys.stderr)
        return 2
    except OSError as e:
        print("truegauge: failed: %s" % e, file=sys.stderr)
        return 2


def main(argv):
    if "--version" in argv:
        print(__version__)
        return 0
    if "--show" in argv:
        return show()
    if any(a in argv for a in ("--install", "--uninstall", "--status")):
        return settings_mode(argv)
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
