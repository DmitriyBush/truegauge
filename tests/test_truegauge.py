"""Question: does truegauge ever show an old, void or replayed number as a current one, count a token twice,
or print a wrong time left at the edges of its thresholds?
Every case runs in a NEW temporary state dir (the live state is never touched). Run: python tests/test_truegauge.py"""
import atexit
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

# The REAL user's places, computed (never opened) BEFORE the environment is replaced.
REAL_HOME = os.path.expanduser("~")
REAL_PROTECTED = [os.path.join(REAL_HOME, ".claude"), os.path.join(REAL_HOME, ".truegauge")]
ROOT = tempfile.mkdtemp(prefix="tg_test_")
_OWN_TEMP = [ROOT]  # every temporary folder THIS process makes outside ROOT; removed at exit (also after a crash), by saved path only


def _clean_own_temp():
    for _p in _OWN_TEMP:
        shutil.rmtree(_p, ignore_errors=True)


atexit.register(_clean_own_temp)
SANDBOX = os.path.join(ROOT, "sandbox_home")
os.makedirs(os.path.join(SANDBOX, ".claude"))
# The whole run lives in temporary places: whatever default truegauge computes lands inside ROOT.
for _k, _v in (("HOME", SANDBOX), ("USERPROFILE", SANDBOX), ("CLAUDE_CONFIG_DIR", os.path.join(SANDBOX, ".claude")),
               ("TRUEGAUGE_HOME", os.path.join(SANDBOX, ".truegauge"))):
    os.environ[_k] = _v
# No real `claude -p /usage` from the checks (measured 2026-10-10: one run started 12 claude.exe, 1.1 GB; the mutation
# run repeats the suite hundreds of times). The probe checks switch it on locally and inject their own spawn.
os.environ["TRUEGAUGE_FABLE"] = "0"
os.environ["PATH"] = os.pathsep.join(x for x in os.environ.get("PATH", "").split(os.pathsep)
                                     if "claude-code" not in x.lower() and not x.lower().rstrip("\\/").endswith("npm"))
# Colour settings of the person running the tests must not reach the checks (a TRUEGAUGE_RED_5H=50 in their shell
# would turn every 0.1 check at >= 50% into a false FAIL); the colour checks set what they need themselves.
for _k in ("TRUEGAUGE_RED_5H", "TRUEGAUGE_RED_7D", "TRUEGAUGE_RED_COLOR", "TRUEGAUGE_COLD_COLOR", "TRUEGAUGE_MARK", "NO_COLOR",
           "TRUEGAUGE_FRESH_COLOR", "TRUEGAUGE_TIME", "TRUEGAUGE_DIM_COLOR"):
    os.environ.pop(_k, None)
# 0.4: the checks written before the dim colour run with it OFF -- the line then keeps the pre-0.4 signs (`>=`, `<=`,
# `Σ>=`), so every one of them still pins the rounding, the bounds and the red invariant. The dim block (search
# "0.4 dim") switches it on itself.
os.environ["TRUEGAUGE_DIM_COLOR"] = "off"
import truegauge as tg  # noqa: E402


def _norm(p):
    return os.path.normcase(os.path.realpath(os.path.abspath(p)))


def _inside(p, d):
    p, d = _norm(p), _norm(d)
    return p == d or p.startswith(d + os.sep)


def guard_path(p):
    """Fuse: a path a call is about to read or write must not be the real user's, and must lie inside ROOT.
    Raises BEFORE anything is written."""
    for real in REAL_PROTECTED:
        if _inside(p, real):
            raise RuntimeError("TEST FUSE: %s is inside the REAL user directory %s; refusing before any write" % (p, real))
    if not _inside(p, ROOT):
        raise RuntimeError("TEST FUSE: %s is outside the temporary test root %s; refusing before any write" % (p, ROOT))

fails = []
NOW = 1_800_000_000
FRESH_G = "\033[1;38;2;78;186;101m5h\033[22;39m"  # 0.4: the 5-hour group right after a reset (default green, no percent)


def check(name, cond, got=None):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else "  got=%r" % (got,)))
    if not cond:
        fails.append(name)


def fresh():
    d = tempfile.mkdtemp(dir=ROOT)
    tg.HOME = d
    tg.STATE = os.path.join(d, "state.json")
    tg.ERRLOG = os.path.join(d, "errors.log")
    tg.SESS_DIR = os.path.join(d, "sessions")
    return d


def rl(five=None, seven=None, **top):
    r = {}
    for k, v in (("five_hour", five), ("seven_day", seven)):
        if v is not None:
            r[k] = {"used_percentage": v[0], "resets_at": v[1]}
    top["rate_limits"] = r
    return top


W = tg.WORDS["en"]

# ---- time left formatting at the edges
for sec, want in ((0, "0m"), (59, "0m"), (60, "1m"), (3599, "59m"), (3600, "1h"), (3661, "1h"),
                  (86399, "23h"), (86400, "1d"), (86400 + 3599, "1d"), (86400 + 86399, "1d"), (2 * 86400, "2d"), (-5, "0m")):
    check("fmt_left(%d) == %s" % (sec, want), tg.fmt_left(sec, W) == want, tg.fmt_left(sec, W))
for n, want in ((0, "0"), (999, "999"), (1000, "1K"), (1499, "1K"), (1500, "2K"), (999_499, "999K"), (999_500, "1.0M"),
                (1_000_000, "1.0M"), (1_250_000, "1.3M"), (13_800_000, "13.8M")):
    check("fmt_tokens(%d) == %s" % (n, want), tg.fmt_tokens(n, W) == want, tg.fmt_tokens(n, W))

# ---- merge: replay of an older reading never wins
fresh()
tg.update(rl(five=(60, NOW + 3600)), NOW)
tg.update(rl(five=(30, NOW + 3600)), NOW + 10)  # idle session replays an older, lower reading
s = tg.read_state(NOW + 20)
check("replay with same reset and lower % is ignored", s["five_hour"]["used_percentage"] == 60, s["five_hour"])
tg.update(rl(five=(5, NOW + 3600 + 18000)), NOW + 30)  # a NEW window (later reset) wins even with lower %
s = tg.read_state(NOW + 40)
check("later reset wins even with lower %", s["five_hour"]["used_percentage"] == 5, s["five_hour"])

# ---- staleness boundary: 900 s fresh, 901 s stale
fresh()
tg.update(rl(five=(40, NOW + 7200)), NOW)
check("age 900 s -> fresh", tg.read_state(NOW + 900, 900)["five_hour"]["status"] == "fresh")
s = tg.read_state(NOW + 901, 900)["five_hour"]
check("age 901 s -> stale, no point value", s["status"] == "stale_reading" and s["remaining_percentage"] is None
      and s["remaining_at_most"] == 60, s)
check("stale renders as >= the old used value", "≥40%" in tg.render({"five_hour": s}, None, None), tg.render({"five_hour": s}, None, None))
tg.update(rl(five=(40, NOW + 7200)), NOW + 800)  # the same numbers again: updated_at must NOT move
check("unchanged numbers do not refresh the age", tg.read_state(NOW + 901, 900)["five_hour"]["status"] == "stale_reading")

# ---- reset boundary: resets_at - 1 open, resets_at expired
fresh()
tg.update(rl(five=(70, NOW + 100)), NOW)
check("one second before reset -> open", tg.read_state(NOW + 99)["five_hour"]["status"] == "fresh")
s = tg.read_state(NOW + 100)["five_hour"]
check("at reset -> expired, no remainder", s["status"] == "expired" and s["remaining_percentage"] is None, s)
check("expired 5h -> green `5h` with NO percent: not the old number, not a guessed 0%",
      tg.render({"five_hour": s}, None, None) == FRESH_G, tg.render({"five_hour": s}, None, None))

# ---- bad input never stored
fresh()
for bad, why in (((101, NOW + 60), ">100"), ((-1, NOW + 60), "<0"), (("50", NOW + 60), "string"),
                 ((50, (NOW + 60) * 1000), "ms epoch"), ((50, NOW + 6 * 3600 + 1), "too far ahead"), ((True, NOW + 60), "bool")):
    tg.update(rl(five=bad), NOW)
    check("rejected: %s" % why, "five_hour" not in tg.read_state(NOW), tg.read_state(NOW).get("five_hour"))
tg.update(rl(five=(50, NOW + 6 * 3600)), NOW)
check("exactly 6 h ahead accepted", tg.read_state(NOW)["five_hour"]["used_percentage"] == 50)

# ---- corrupt state recovers, says so
d = fresh()
open(tg.STATE, "w").write("{broken")
tg.update(rl(five=(10, NOW + 60)), NOW)
check("corrupt file recovered and marked", tg.read_state(NOW)["_tap"]["recovered_from_corrupt_at"] == NOW)

# ---- Fable parse
TXT = ("Current session: 23% used · resets Oct 9, 5:30am (Asia/Qyzylorda)\n"
       "Current week (all models): 19% used · resets Oct 14, 12pm (Asia/Qyzylorda)\n"
       "Current week (Fable): 4% used · resets Oct 14, 12pm (Asia/Qyzylorda)\n")
p = tg.parse_usage(TXT)
check("parse Fable 4%", p["fable"] == (4.0, "Oct 14, 12pm (Asia/Qyzylorda)"), p)
check("parse week 19%", p["week"][0] == 19.0, p)
check("no Fable line -> None", tg.parse_usage("Current week (all models): 19% used")["fable"] is None)
check("decimal 12.5%", tg.parse_usage("Current week (Fable): 12.5% used")["fable"][0] == 12.5)

# ---- Fable probe: stored, shares weekly reset, stale after 2x max age, absent stated
fresh()
tg.update(rl(seven=(19, NOW + 5 * 86400)), NOW)
tg.probe_usage(NOW, runner=lambda: TXT)
s = tg.read_state(NOW + 10, 900)
check("Fable fresh 96% left", s["fable"]["status"] == "fresh" and s["fable"]["remaining_percentage"] == 96, s["fable"])
check("Fable shares the weekly reset", s["fable"]["resets_at"] == NOW + 5 * 86400, s["fable"])
check("Fable at 1800 s -> fresh", tg.read_state(NOW + 1800, 900)["fable"]["status"] == "fresh")
check("Fable at 1801 s -> stale", tg.read_state(NOW + 1801, 900)["fable"]["status"] == "stale_reading")
check("Fable expires with the week", tg.read_state(NOW + 5 * 86400)["fable"]["status"] == "expired")
line = tg.render(tg.read_state(NOW + 10), None, None)
check("render has 'F4%'", line.endswith("F4%"), line)
tg.probe_usage(NOW + 20, runner=lambda: "Current week (all models): 19% used")
s = tg.read_state(NOW + 30)
check("Fable line gone -> absent, old number dropped", s["fable"] == {"status": "absent"}, s.get("fable"))
check("absent Fable not rendered", "F" not in tg.render(s, None, None))
tg.probe_usage(NOW + 40, runner=lambda: "Error: not logged in")
s = tg.read_state(NOW + 50)
check("garbage /usage -> probe_error recorded", "no usage lines" in (s["_tap"].get("probe_error") or ""), s["_tap"])


def boom():
    raise RuntimeError("claude not found")


tg.probe_usage(NOW + 60, runner=boom)
check("probe exception recorded, not raised", "claude not found" in (tg.read_state(NOW + 61)["_tap"].get("probe_error") or ""))
fresh()
tg.probe_usage(NOW, runner=lambda: TXT.replace("4% used", "140% used"))
check("Fable 140% rejected", "fable" not in tg.read_state(NOW) or tg.read_state(NOW)["fable"].get("status") != "fresh",
      tg.read_state(NOW).get("fable"))

# ---- probe interval: 599 s no, 600 s yes; off switch
fresh()
os.environ["TRUEGAUGE_FABLE"] = "1"  # on for these checks only; the spawn is injected, nothing real starts
calls = []
tg.maybe_start_probe(NOW, spawn=lambda: calls.append(1))
tg.maybe_start_probe(NOW + 599, spawn=lambda: calls.append(1))
check("second probe within 599 s not started", len(calls) == 1, calls)
tg.maybe_start_probe(NOW + 600, spawn=lambda: calls.append(1))
check("probe at 600 s started", len(calls) == 2, calls)
os.environ["TRUEGAUGE_FABLE"] = "0"
check("TRUEGAUGE_FABLE=0 -> never", tg.maybe_start_probe(NOW + 99999, spawn=lambda: calls.append(1)) is False)
os.environ["TRUEGAUGE_FABLE"] = "0"  # back to the run-wide setting


# ---- session tokens: split messages counted once, partial line waits, subagents added, truncation restarts
def msg(mid, i, cc, o, cr):
    return json.dumps({"type": "assistant", "message": {"id": mid, "usage": {
        "input_tokens": i, "cache_creation_input_tokens": cc, "output_tokens": o, "cache_read_input_tokens": cr}}})


d = fresh()
tp = os.path.join(d, "sess.jsonl")
with open(tp, "w") as f:
    f.write(msg("m1", 10, 100, 5, 1000) + "\n" + msg("m1", 10, 100, 7, 1000) + "\n")  # one message, two lines
    f.write(json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n")
t = tg.session_tokens(tp, "s1")
check("split message counted once (max of fields)", t["new"] == 117 and t["cache_read"] == 1000, t)
with open(tp, "a") as f:
    f.write(msg("m2", 1, 0, 2, 0) + "\n" + msg("m3", 1, 0, 3, 0)[:20])  # second half not yet written
t = tg.session_tokens(tp, "s1")
check("partial line not read", t["new"] == 117 + 3, t)
with open(tp, "a") as f:
    f.write(msg("m3", 1, 0, 3, 0)[20:] + "\n")
t = tg.session_tokens(tp, "s1")
check("completed line read once", t["new"] == 117 + 3 + 4, t)
t = tg.session_tokens(tp, "s1")
check("re-reading adds nothing", t["new"] == 124, t)
check("no timestamps -> no reply time", t.get("last_reply_at") is None, t)
with open(tp, "a") as f:
    line = json.loads(msg("m4", 0, 0, 0, 0))
    line["timestamp"] = "2026-10-09T08:12:33.500Z"
    f.write(json.dumps(line) + "\n")
t = tg.session_tokens(tp, "s1")
check("last reply time of the main transcript reported", t["last_reply_at"] == 1791533553.5, t)
os.makedirs(os.path.join(d, "sess", "subagents"))
with open(os.path.join(d, "sess", "subagents", "agent-a.jsonl"), "w") as f:
    f.write(msg("s1", 50, 0, 50, 7) + "\n")
t = tg.session_tokens(tp, "s1")
check("subagent transcript added", t["new"] == 224 and t["files"] == 2, t)
with open(tp, "w") as f:  # transcript rewritten shorter
    f.write(msg("m9", 1, 1, 1, 0) + "\n")
t = tg.session_tokens(tp, "s1")
check("truncated file restarts its own count", t["new"] == 100 + 3, t)
check("no transcript -> None", tg.session_tokens(None, "x") is None)


# ---- lens 1 (2026-10-09) findings, each pinned
fresh()
tg.update(rl(seven=(90, NOW + 300)), NOW)            # week ends in 5 min
tg.probe_usage(NOW, runner=lambda: TXT.replace("4% used", "96% used"))
tg.update(rl(seven=(1, NOW + 300 + 7 * 86400)), NOW + 360)   # new week started; Fable reading is 6 min old
s = tg.read_state(NOW + 361)["fable"]
check("new week -> old Fable reading expired, not fresh", s["status"] == "expired", s)
check("expired Fable is left out", tg.render({"fable": s}, None, None) == "")
fresh()
tg.update(rl(seven=(50, NOW + 3 * 86400)), NOW)      # week known at probe time ends in 3 days
tg.probe_usage(NOW, runner=lambda: TXT.replace("4% used", "96% used"))
tg.update(rl(seven=(1, NOW + 7 * 86400)), NOW + 60)  # the plan reset EARLY: a new week with a later reset
s = tg.read_state(NOW + 61)["fable"]
check("early weekly reset -> old Fable reading expired", s["status"] == "expired", s)
fresh()
tg.probe_usage(NOW, runner=lambda: TXT.replace("Fable): 4% used \u00b7 resets Oct 14", "Fable): 4% used \u00b7 resets Oct 15"))
check("different reset text -> no reset time stored", tg.read_state(NOW + 10)["fable"]["resets_at"] is None)
s = tg.read_state(NOW + 1801, 900)["fable"]
check("old Fable reading with unknown reset -> unknown, not a bound", s["status"] == "unknown", s)
check("unknown Fable not rendered", "F" not in tg.render({"fable": s}, None, None))
fresh()
tg.probe_usage(NOW, runner=lambda: "Current week (all models): 19% used \u00b7 resets Oct 14")
check("Fable line missing but week present -> named in probe_error",
      "no 'Current week (Fable)' line" in (tg.read_state(NOW)["_tap"].get("probe_error") or ""))
check("stale used value rounds DOWN (21.9 -> >=21%)",
      tg._used({"status": "stale_reading", "used_percentage": 21.9}) == "≥21%")
fresh()
os.environ["TRUEGAUGE_PROBE_S"] = "120"
os.environ["TRUEGAUGE_FABLE"] = "1"  # on for these checks only; the spawn is injected
calls = []
tg.maybe_start_probe(NOW, spawn=lambda: calls.append(1))
tg.maybe_start_probe(NOW + 200, spawn=lambda: calls.append(1))
check("unfinished probe blocks a new one for 300 s", len(calls) == 1, calls)
tg.maybe_start_probe(NOW + 300, spawn=lambda: calls.append(1))
check("after 300 s a hung probe no longer blocks", len(calls) == 2, calls)
del os.environ["TRUEGAUGE_PROBE_S"]
os.environ["TRUEGAUGE_FABLE"] = "0"

d = fresh()
tp = os.path.join(d, "s.jsonl")
with open(tp, "w") as f:
    f.write(msg("A", 1, 0, 1, 0) + "\n" + msg("B", 1, 0, 1, 0) + "\n" + msg("A", 1, 0, 9, 0) + "\n")
t = tg.session_tokens(tp, "x")
check("non-adjacent repeat keeps the max (A,B,A)", t["new"] == (1 + 9) + (1 + 1), t)
with open(tp, "a") as f:
    f.write(json.dumps({"message": {"usage": {"input_tokens": 5, "output_tokens": 5}}}) + "\n")
    f.write(json.dumps({"message": {"usage": {"input_tokens": 5, "output_tokens": 5}}}) + "\n")
t = tg.session_tokens(tp, "x")
check("two messages without id counted as two", t["new"] == 12 + 20, t)
d = fresh()
br = os.path.join(d, "we[ird]")
os.makedirs(os.path.join(br, "subagents"))
tp = br + ".jsonl"
open(tp, "w").write(msg("m", 1, 0, 1, 0) + "\n")
open(os.path.join(br, "subagents", "a.jsonl"), "w").write(msg("s", 10, 0, 10, 0) + "\n")
t = tg.session_tokens(tp, "y")
check("brackets in path: subagents still found", t["new"] == 22 and t["files"] == 2, t)
d = fresh()
tp = os.path.join(d, "big.jsonl")
with open(tp, "w") as f:
    for i in range(40):
        f.write(msg("m%d" % i, 1, 0, 1, 0) + "\n")
cap = tg.READ_CAP
tg.READ_CAP = 600
t = tg.session_tokens(tp, "z")
check("big transcript: partial read flagged incomplete", t["complete"] is False and 0 < t["new"] < 80, t)
check("incomplete renders with >=", "Σ≥" in tg.render({}, t, None), tg.render({}, t, None))
for _ in range(40):
    t = tg.session_tokens(tp, "z")
check("big transcript finished over later calls", t["complete"] is True and t["new"] == 80, t)
tg.READ_CAP = cap


# ---- lens 2 (2026-10-09) findings, each pinned
fresh()
tg.update(rl(five=(40, NOW + 7200), seven=(10, NOW + 5 * 86400)), NOW)
for k in range(1, 5):   # five_hour stops being sent; seven_day keeps changing
    tg.update(rl(seven=(10 + k, NOW + 5 * 86400)), NOW + 300 * k)
s = tg.read_state(NOW + 1201, 900)
check("window no longer sent -> stale even while another window moves", s["five_hour"]["status"] == "stale_reading", s["five_hour"])
check("window still sent -> fresh", s["seven_day"]["status"] == "fresh", s["seven_day"])
d = fresh()
open(tg.STATE, "w").write("{broken")
tg.update(rl(five=(10, NOW + 3600)), NOW)
check("after a damaged state the first reading is not a fresh point", tg.read_state(NOW + 1)["five_hour"]["status"] == "stale_reading")
tg.update(rl(five=(11, NOW + 3600)), NOW + 60)
check("...and becomes fresh once a number moves", tg.read_state(NOW + 61)["five_hour"]["status"] == "fresh")
fresh()
tg.update(rl(seven=(19, NOW + 5 * 86400)), NOW)
tg.probe_usage(NOW, runner=lambda: TXT)
open(tg.STATE, "a").close()
real_read = tg._read
tg._read = lambda path=None: ({}, "unreadable") if (path or tg.STATE) == tg.STATE else real_read(path)
tg.probe_usage(NOW + 10, runner=lambda: TXT)
tg._read = real_read
check("probe does not wipe an unreadable state", tg.read_state(NOW + 11).get("seven_day", {}).get("used_percentage") == 19)
fresh()
tg.probe_usage(NOW, runner=lambda: TXT.replace("Fable): 4% used \u00b7 resets Oct 14", "Fable): 4% used \u00b7 resets Oct 15"))
check("Fable without reset: fresh at 900 s", tg.read_state(NOW + 900, 900)["fable"]["status"] == "fresh")
check("Fable without reset: unknown at 901 s", tg.read_state(NOW + 901, 900)["fable"]["status"] == "unknown")
d = fresh()
tp = os.path.join(d, "t.jsonl")
with open(tp, "w") as f:
    for i in range(30):
        f.write(msg("m%d" % i, 1, 0, 1, 0) + "\n")
cap = tg.READ_CAP
tg.READ_CAP = os.path.getsize(tp) - 50   # less than the file, more than half: the old test said "complete"
t = tg.session_tokens(tp, "c")
check("partial read within one cap of the end is NOT complete", t["complete"] is False, t)
t = tg.session_tokens(tp, "c")
check("second read reaches the end -> complete", t["complete"] is True and t["new"] == 60, t)
tg.READ_CAP = cap
d = fresh()
tp = os.path.join(d, "bad.jsonl")
open(tp, "w").write(msg("a", 1, 0, 1, 0) + "\n" + '{"message": {"usage": "oops"}}\n' + '{"usage" broken\n')
t = tg.session_tokens(tp, "b")
check("bad lines make the count a lower bound (>=)", t["complete"] is False and t["bad_lines"] == 2, t)
check("missing transcript -> None, not 0", tg.session_tokens(os.path.join(d, "nope.jsonl"), "n") is None)

# ---- double count (2026-10-09): one file under two spellings of its path was counted twice
def spellings(p):
    """Other spellings of the same file: a dot segment (every OS) and flipped slashes (Windows)."""
    out = [os.path.join(os.path.dirname(p), ".", os.path.basename(p))]
    if os.name == "nt":
        out += [p.replace("\\", "/"), p.replace("/", "\\"), p[0].swapcase() + p[1:]]
    return out


d = fresh()
tp = os.path.join(d, "dbl.jsonl")
os.makedirs(os.path.join(d, "dbl", "subagents"))
with open(tp, "w") as f:
    f.write(msg("a", 10, 0, 10, 0) + "\n")
with open(os.path.join(d, "dbl", "subagents", "agent-x.jsonl"), "w") as f:
    f.write(msg("b", 5, 0, 5, 0) + "\n")
one = tg.session_tokens(tp, "dbl")["new"]
check("baseline count of main + 1 subagent", one == 30, one)
for sp in spellings(tp):
    t = tg.session_tokens(sp, "dbl")
    check("same transcript as %r -> total unchanged" % sp[-22:], t["new"] == one and t["files"] == 2, t)
fresh_rec = tg._read(tg._sess_file("dbl"))[0]
check("record keeps one key per file", len(fresh_rec) == 2, sorted(fresh_rec))

# an EXISTING record that already holds two spellings of one file is merged on load (largest offset kept)
d = fresh()
tp = os.path.join(d, "old.jsonl")
with open(tp, "w") as f:
    f.write(msg("a", 10, 0, 10, 0) + "\n")
tg.session_tokens(tp, "old")
sf = tg._sess_file("old")
rec = tg._read(sf)[0]
(only_key, st), = rec.items()
spell = spellings(tp)[0]
lag = json.loads(json.dumps(st))
lag["offset"] = 0
lag["ids"] = {}
rec2 = {spell: lag, os.path.abspath(tp): st}      # two spellings: one lagging, one fully read
json.dump(rec2, open(sf, "w"))
t = tg.session_tokens(tp, "old")
check("existing record with two spellings merged on load", t["new"] == 20 and t["files"] == 1, t)
check("merged record is saved back", len(tg._read(sf)[0]) == 1, tg._read(sf)[0])
far, near = {"offset": 90, "ids": {"a": [1, 0, 1, 0]}, "noid": 0}, {"offset": 10, "ids": {}, "noid": 0}
for order in ((spell, os.path.abspath(tp)), (os.path.abspath(tp), spell)):
    got, ch = tg._merge_spellings({order[0]: dict(far), order[1]: dict(near)})
    got2, _ = tg._merge_spellings({order[0]: dict(near), order[1]: dict(far)})
    check("merge keeps the record that read furthest (either order)",
          len(got) == 1 and len(got2) == 1 and list(got.values())[0]["offset"] == 90
          and list(got2.values())[0]["offset"] == 90 and ch, (got, got2))
rec3 = {spell: json.loads(json.dumps(st)), os.path.abspath(tp): json.loads(json.dumps(st))}
json.dump(rec3, open(sf, "w"))
t = tg.session_tokens(tp, "old")
check("two fully-read spellings are not summed", t["new"] == 20 and t["files"] == 1, t)

# ---- rendering: the sample format, groups, units glued to numbers
def win(status, used=None, rin=None):
    v = {"status": status, "resets_in_s": rin, "used_percentage": used, "remaining_percentage": None}
    if status == "fresh":
        v["remaining_percentage"] = 100 - used
    return v


def R(st, tokens=None, ctx=None, lang="en", cti=None):
    return tg.render(st, tokens, ctx, lang, cti)


# time left, 5-hour window: >= 1 h -> floor hours, < 1 h -> floor minutes
for rin, want in ((18000, "5h"), (14399, "3h"), (3600, "1h"), (3599, "59m"), (2700, "45m"), (59, "0m"), (0, "0m")):
    got = R({"five_hour": win("fresh", 38, rin)})
    check("5h window %d s left -> %s 38%%" % (rin, want), got == want + " 38%", got)
# time left, week: always floor days (0d in the last day), so it never looks like the 5-hour group
for rin, want in ((5 * 86400 + 7 * 3600, "5d"), (86400, "1d"), (86399, "0d"), (3600, "0d"), (60, "0d"), (0, "0d")):
    got = R({"seven_day": win("fresh", 54, rin)})
    check("week %d s left -> %s 54%%" % (rin, want), got == want + " 54%", got)
# fresh / stale / expired for each window
got = R({"five_hour": win("stale_reading", 38.9, 14400)})
check("stale 5h: floor with >=, never above the reading", got == "4h ≥38%", got)
got = R({"five_hour": win("fresh", 38.5, 14400)})
check("fresh rounds half up", got == "4h 39%", got)
got = R({"five_hour": win("expired", 90, -5), "seven_day": win("expired", 80, -5)})
check("expired 5h -> green `5h`, expired week left out", got == FRESH_G, got)
got = R({"five_hour": win("expired", 90, -5), "seven_day": win("fresh", 54, 3600)})
check("week in its last day next to the fresh `5h`: `5h` and `0d 54%` stay apart", got == FRESH_G + " 0d 54%", got)
got = R({"seven_day": win("stale_reading", 54.7, 3 * 86400)})
check("stale week", got == "3d ≥54%", got)
got = R({"fable": win("fresh", 4)})
check("Fable fresh", got == "F4%", got)
got = R({"fable": win("stale_reading", 4.9)})
check("Fable stale", got == "F≥4%", got)
got = R({"fable": {"status": "expired", "last_seen_used_percentage": 96, "remaining_percentage": None}})
check("Fable expired -> left out", got == "", got)
for status in ("unknown", "absent"):
    got = R({"fable": {"status": status}})
    check("Fable %s -> group omitted, not 0" % status, got == "", got)
check("no data at all -> empty line (no 0 groups)", R({}) == "" and R({}, None, None, "en", None) == "")
# tokens in the window now (Т) and session total (Σ), boundaries
for n, want in ((0, ""), (1, "T1"), (812, "T812"), (999, "T999"), (1000, "T1K"), (1499, "T1K"), (1500, "T2K"),
                (548_200, "T548K"), (999_499, "T999K"), (999_500, "T1.0M"), (1_000_000, "T1.0M"),
                (2_640_000, "T2.6M"), (2_650_000, "T2.7M"), (13_800_000, "T13.8M")):
    got = R({}, None, None, "en", n)
    check("window tokens %d -> %s" % (n, want), got == want, got)
    sw = "Σ" + (want[1:] if n else "0")  # a session total of 0 is a real count (transcript read, nothing spent yet)
    got = R({}, {"new": n, "complete": True})
    check("session total %d -> %s" % (n, sw), got == sw, got)
check("ru letters", R({}, {"new": 2_640_000, "complete": True}, None, "ru", 548_200) == "Т548К Σ2.6М",
      R({}, {"new": 2_640_000, "complete": True}, None, "ru", 548_200))
check("incomplete total -> Σ≥", R({}, {"new": 2_640_000, "complete": False}) == "Σ≥2.6M",
      R({}, {"new": 2_640_000, "complete": False}))
check("zero window tokens (no reading yet / after compact) -> no T and no 0% group", R({}, None, 0.0, cti=0) == "", R({}, None, 0.0, cti=0))
# freshness follows the last API reply, not the last change of the number
tg.update(rl(five=(7, NOW + 3 * 3600)), NOW - 2000)                 # first sighting 2000 s ago
tg.update(rl(five=(7, NOW + 3 * 3600)), NOW, response_at=NOW - 30)    # same 7%, but a reply 30 s ago carried it
check("unchanged number confirmed by a recent reply is fresh", tg.read_state(NOW)["five_hour"]["status"] == "fresh",
      tg.read_state(NOW)["five_hour"])
tg.update(rl(five=(7, NOW + 3 * 3600)), NOW + 1000, response_at=NOW - 30)  # idle: no new reply for 1030 s
check("no new reply for longer than the limit -> stale", tg.read_state(NOW + 1000)["five_hour"]["status"] == "stale_reading",
      tg.read_state(NOW + 1000)["five_hour"])
tg.update(rl(five=(7, NOW + 3 * 3600)), NOW + 1001, response_at=NOW + 5000)  # reply time from the future: ignored
check("reply time from the future does not make a reading fresh",
      tg.read_state(NOW + 1001)["five_hour"]["status"] == "stale_reading", tg.read_state(NOW + 1001)["five_hour"])
check("transcript time parsed", tg._epoch("2026-10-09T08:12:33.500Z") == 1791533553.5, tg._epoch("2026-10-09T08:12:33.500Z"))
check("bad transcript time -> None", tg._epoch("yesterday") is None and tg._epoch(None) is None)
fresh()
check("zero window tokens with a fill -> both left out (no real reading)", R({}, None, 12.0, cti=0) == "", R({}, None, 12.0, cti=0))
for n, want in ((2_650_000, "Σ≥2.6M"), (2_699_999, "Σ≥2.6M"), (999_999, "Σ≥999K"), (1_500, "Σ≥1K"), (812, "Σ≥812")):
    got = R({}, {"new": n, "complete": False})
    check("incomplete total %d is a lower bound, rounded down -> %s" % (n, want), got == want, got)
check("missing total_input_tokens -> no T group", R({}, None, 55.0) == "55%", R({}, None, 55.0))
check("missing used_percentage -> no % group", R({}, None, None, "en", 548_200) == "T548K")
check("the sample line", R({"five_hour": win("fresh", 38, 4 * 3600 + 5), "seven_day": win("fresh", 54, 3 * 86400 + 9),
                            "fable": win("fresh", 4)}, {"new": 2_640_000, "complete": True}, 54.6, "en", 548_200)
      == "4h 38% 3d 54% F4% T548K 55% Σ2.6M",
      R({"five_hour": win("fresh", 38, 4 * 3600 + 5), "seven_day": win("fresh", 54, 3 * 86400 + 9),
         "fable": win("fresh", 4)}, {"new": 2_640_000, "complete": True}, 54.6, "en", 548_200))
# the payload path: total_input_tokens and used_percentage come from context_window
fresh()
out = tg.statusline({"rate_limits": {"five_hour": {"used_percentage": 38, "resets_at": NOW + 4 * 3600 + 30}},
                     "context_window": {"used_percentage": 55.2, "total_input_tokens": 548_200}}, NOW)
check("statusline: payload to line", out == "4h 38% T548K 55%", out)
fresh()
out = tg.statusline({"context_window": {"used_percentage": None, "total_input_tokens": None}}, NOW)
check("statusline: null context fields -> nothing shown", out == "", out)

# ---- whole status line: never raises, records the failure
fresh()
out = tg.statusline({"rate_limits": {"five_hour": {"used_percentage": 50, "resets_at": 10 ** 10}},
                     "context_window": {"used_percentage": None}, "transcript_path": 5}, NOW)
check("odd payload -> no crash, partial line", isinstance(out, str), out)
line = tg.render({}, {"new": 240000, "cache_read": 0, "files": 1}, 23.4, "ru")
check("ru render", line == "23% Σ240К", line)

# ---- --install / --uninstall / --status on TEMP settings files only (the real settings are never touched)
import contextlib  # noqa: E402
import glob  # noqa: E402
import io  # noqa: E402


def cli(*args, cfg=None):
    """Run main() in-process; returns (exit code, stdout, stderr). cfg sets CLAUDE_CONFIG_DIR for the call."""
    out, err = io.StringIO(), io.StringIO()
    old = os.environ.get("CLAUDE_CONFIG_DIR")
    os.environ["CLAUDE_CONFIG_DIR"] = cfg or os.path.join(SANDBOX, ".claude")
    guard_path(tg.HOME)
    try:
        guard_path(tg._settings_path(list(args)))  # where truegauge WILL write, asked before it writes
    except tg.Refuse:
        pass  # e.g. "--settings" without a path: main() refuses it itself
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = tg.main(list(args))
    finally:
        os.environ["CLAUDE_CONFIG_DIR"] = old
    return code, out.getvalue(), err.getvalue()


def put(path, text):
    with open(path, "wb") as f:
        f.write(text.encode("utf-8") if isinstance(text, str) else text)


def get(path):
    with open(path, "rb") as f:
        return f.read()


def backups(path):
    return sorted(glob.glob(path + ".truegauge-backup-*"))


def prev_file():
    return os.path.join(tg.HOME, "previous_statusline.json")


OTHER = {"type": "command", "command": "python ~/mine/line.py", "padding": 2}

# missing file: created, no backup (nothing to copy), previous = none
d = fresh()
sp = os.path.join(d, "cfg", "settings.json")
code, out, err = cli("--install", "--settings", sp)
s = json.loads(get(sp))
check("install on a missing file: exit 0", code == 0, (code, err))
check("install on a missing file: truegauge line written",
      s["statusLine"]["type"] == "command" and s["statusLine"]["refreshInterval"] == 60
      and "truegauge.py" in s["statusLine"]["command"], s)
check("install on a missing file: command points at an existing script",
      os.path.exists(s["statusLine"]["command"].split(" ")[-1].strip('"')), s["statusLine"]["command"])
check("install on a missing file: no backup (nothing to copy)", backups(sp) == [], backups(sp))
check("install on a missing file: saved 'there was none'",
      json.loads(get(prev_file()))["entries"][os.path.abspath(sp)]["had_line"] is False)
code, out, err = cli("--uninstall", "--settings", sp)
check("uninstall after 'none': key removed, rest kept", code == 0 and json.loads(get(sp)) == {}, get(sp))
check("uninstall: saved file removed after a successful write", not os.path.exists(prev_file()))

# empty file (0 bytes and whitespace) is treated as {}
for blank in (b"", b"  \n"):
    d = fresh()
    sp = os.path.join(d, "settings.json")
    put(sp, blank)
    code, out, err = cli("--install", "--settings", sp)
    check("install on an empty file %r" % blank, code == 0 and "statusLine" in json.loads(get(sp)), (code, err))
    check("install on an empty file: backup made", len(backups(sp)) == 1 and get(backups(sp)[0]) == blank)

# other line + other keys: keys and their ORDER kept; install -> idempotent -> uninstall restores exactly
d = fresh()
sp = os.path.join(d, "settings.json")
orig = {"model": "opus", "statusLine": OTHER, "env": {"A": "1", "B": "я"}, "permissions": {"allow": ["x", "y"]}}
put(sp, json.dumps(orig, ensure_ascii=False, indent=4))
before = get(sp)
code, out, err = cli("--install", "--settings", sp)
s = json.loads(get(sp))
check("install with another line: exit 0", code == 0, (code, err))
check("install: key order kept (statusLine stays in place; 0.4 appends `hooks` last)", list(s) == ["model", "statusLine", "env", "permissions", "hooks"], list(s))
check("install: other keys kept byte-for-value", all(s[k] == orig[k] for k in ("model", "env", "permissions")), s)
check("install: backup holds the exact previous bytes", len(backups(sp)) == 1 and get(backups(sp)[0]) == before)
check("install: the other line is saved",
      json.loads(get(prev_file()))["entries"][os.path.abspath(sp)]["statusLine"] == OTHER)
check("install output says how to undo", "--uninstall" in out, out)
code, out2, _ = cli("--status", "--settings", sp)
check("status: active + previous saved", code == 0 and "active status line: yes" in out2 and "line.py" in out2, out2)
written = get(sp)
code, out3, _ = cli("--install", "--settings", sp)
check("install twice: exit 0, says so, file untouched", code == 0 and "already" in out3 and get(sp) == written, out3)
check("install twice: no new backup", len(backups(sp)) == 1, backups(sp))
check("install twice: saved previous not clobbered by a truegauge value",
      json.loads(get(prev_file()))["entries"][os.path.abspath(sp)]["statusLine"] == OTHER)
code, out4, err = cli("--uninstall", "--settings", sp)
s = json.loads(get(sp))
check("uninstall restores the exact previous line", code == 0 and s["statusLine"] == OTHER, s)
check("uninstall: other keys and order kept", list(s) == ["model", "statusLine", "env", "permissions"]
      and s["env"] == orig["env"] and s["permissions"] == orig["permissions"], list(s))
check("uninstall: a backup of the truegauge-state file was made first", len(backups(sp)) == 2
      and any("truegauge.py" in json.loads(get(x))["statusLine"].get("command", "") for x in backups(sp)), backups(sp))
check("uninstall: saved file removed", not os.path.exists(prev_file()))
code, out5, _ = cli("--uninstall", "--settings", sp)
check("uninstall twice: nothing to do, file untouched", code == 0 and "not the status line" in out5
      and json.loads(get(sp))["statusLine"] == OTHER)

# uninstall while ANOTHER line is active (the user changed it later): no change, no backup
d = fresh()
sp = os.path.join(d, "settings.json")
put(sp, json.dumps({"statusLine": OTHER}))
cli("--install", "--settings", sp)
put(sp, json.dumps({"statusLine": {"type": "command", "command": "python third.py"}}))
before, nb = get(sp), len(backups(sp))
code, out, _ = cli("--uninstall", "--settings", sp)
check("uninstall on a foreign line: exit 0, file byte-identical", code == 0 and get(sp) == before, out)
check("uninstall on a foreign line: no backup made", len(backups(sp)) == nb)
check("uninstall on a foreign line: saved previous kept", os.path.exists(prev_file()))
d2 = fresh()
sp2 = os.path.join(d2, "settings.json")
put(sp2, json.dumps({"model": "x"}))
code, out, _ = cli("--uninstall", "--settings", sp2)
check("uninstall with no statusLine at all: no change", code == 0 and json.loads(get(sp2)) == {"model": "x"}
      and backups(sp2) == [])

# unreadable / invalid: refuse, exit 2, file byte-identical, no backup, no saved file
for label, content in (("invalid JSON", "{ not json"), ("a JSON list", "[1, 2]"), ("a JSON string", '"x"'),
                       ("truncated", '{"statusLine": {"type": "comm')):
    for flag in ("--install", "--uninstall", "--status"):
        d = fresh()
        sp = os.path.join(d, "settings.json")
        put(sp, content)
        code, out, err = cli(flag, "--settings", sp)
        check("%s on %s: refused with exit 2" % (flag, label), code == 2 and "refused" in err, (code, err))
        check("%s on %s: file byte-identical, no backup, nothing saved" % (flag, label),
              get(sp) == content.encode() and backups(sp) == [] and not os.path.exists(prev_file()))
d = fresh()
code, out, err = cli("--install", "--settings", d)  # a directory, not a file
check("install on a directory: refused, exit 2", code == 2, (code, err))
code, out, err = cli("--install", "--settings")
check("--settings without a path: refused, exit 2", code == 2, (code, err))
d = fresh()
sp = os.path.join(d, "settings.json")
put(sp, json.dumps({"model": "x"}))
put(prev_file(), "{ broken")
code, out, err = cli("--install", "--settings", sp)
check("damaged saved-previous file: install refused, settings untouched",
      code == 2 and json.loads(get(sp)) == {"model": "x"} and backups(sp) == [], (code, err))

# a backup exists for EVERY write, each under its own name
d = fresh()
sp = os.path.join(d, "settings.json")
put(sp, json.dumps({"statusLine": OTHER}))
cli("--install", "--settings", sp)
cli("--uninstall", "--settings", sp)
cli("--install", "--settings", sp)
cli("--uninstall", "--settings", sp)
check("backup before every write: 4 writes -> 4 distinct backups", len(set(backups(sp))) == 4, backups(sp))

# CLAUDE_CONFIG_DIR is the default location; --settings=PATH form
d = fresh()
cfg = os.path.join(d, "claude_cfg")
os.makedirs(cfg)
put(os.path.join(cfg, "settings.json"), json.dumps({"model": "m"}))
code, out, err = cli("--install", cfg=cfg)
check("CLAUDE_CONFIG_DIR honoured by install", code == 0 and "statusLine" in json.loads(get(os.path.join(cfg, "settings.json"))), (out, err))
code, out, _ = cli("--status", cfg=cfg)
check("CLAUDE_CONFIG_DIR honoured by status", "active status line: yes" in out, out)
code, out, err = cli("--uninstall", cfg=cfg)
check("CLAUDE_CONFIG_DIR honoured by uninstall", code == 0 and json.loads(get(os.path.join(cfg, "settings.json"))) == {"model": "m"})
sp = os.path.join(d, "eq.json")
code, out, err = cli("--install", "--settings=" + sp)
check("--settings=PATH form", code == 0 and os.path.exists(sp), (out, err))

# two settings files, one state dir: each gets back ITS OWN previous line
d = fresh()
a, b = os.path.join(d, "a.json"), os.path.join(d, "b.json")
put(a, json.dumps({"statusLine": {"type": "command", "command": "A"}}))
put(b, json.dumps({"statusLine": {"type": "command", "command": "B"}}))
cli("--install", "--settings", a)
cli("--install", "--settings", b)
cli("--uninstall", "--settings", a)
cli("--uninstall", "--settings", b)
check("two settings files: each restores its own line",
      json.loads(get(a))["statusLine"]["command"] == "A" and json.loads(get(b))["statusLine"]["command"] == "B")

# truegauge added by hand / record lost (N3): uninstall REFUSES (exit 2), names the newest backup and --force;
# with --force the key is removed
d = fresh()
sp = os.path.join(d, "settings.json")
hand = json.dumps({"statusLine": {"type": "command", "command": "python C:/x/truegauge.py"}, "k": 1})
put(sp, hand)
put(sp + ".truegauge-backup-20200101T000000Z", "{}")
put(sp + ".truegauge-backup-20200202T000000Z", "{}")
os.utime(sp + ".truegauge-backup-20200101T000000Z", (1_000_000_000, 1_000_000_000))
os.utime(sp + ".truegauge-backup-20200202T000000Z", (1_100_000_000, 1_100_000_000))
code, out, err = cli("--uninstall", "--settings", sp)
check("uninstall without a saved record: refused, exit 2", code == 2 and "refused" in err, (code, err))
check("uninstall without a saved record: file byte-identical, no new backup",
      get(sp) == hand.encode() and len(backups(sp)) == 2)
check("uninstall without a saved record: message names the NEWEST backup and --force",
      "20200202T000000Z" in err and "20200101T000000Z" not in err and "--force" in err, err)
code, out, err = cli("--uninstall", "--force", "--settings", sp)
check("uninstall --force without a record: key removed, others kept, says no saved line",
      code == 0 and json.loads(get(sp)) == {"k": 1} and "no saved previous" in out, (out, err))
d = fresh()
sp = os.path.join(d, "settings.json")
put(sp, hand)
code, out, err = cli("--uninstall", "--settings", sp)
check("uninstall without a record and without any backup: refused, says none found",
      code == 2 and "none found" in err and "--force" in err, err)
# the record lost the way it really gets lost: another TRUEGAUGE_HOME
d = fresh()
sp = os.path.join(d, "settings.json")
put(sp, json.dumps({"statusLine": OTHER}))
cli("--install", "--settings", sp)
fresh()  # state dir replaced: the record is not found
code, out, err = cli("--uninstall", "--settings", sp)
check("uninstall after the state dir was lost: refused, line kept",
      code == 2 and "truegauge.py" in json.loads(get(sp))["statusLine"]["command"], (code, err))

# UTF-8 only on read (N4): a cp1251 byte in a value is refused, nothing written; a BOM is accepted
for flag in ("--install", "--uninstall", "--status"):
    d = fresh()
    sp = os.path.join(d, "settings.json")
    bad = b'{"model": "\xcf\xf0\xe8\xe2\xe5\xf2", "statusLine": {"type": "command", "command": "truegauge.py"}}'
    put(sp, bad)
    code, out, err = cli(flag, "--settings", sp)
    check("%s on a cp1251 file: refused, says UTF-8" % flag, code == 2 and "UTF-8" in err, (code, err))
    check("%s on a cp1251 file: bytes intact, no backup, nothing saved" % flag,
          get(sp) == bad and backups(sp) == [] and not os.path.exists(prev_file()))
d = fresh()
sp = os.path.join(d, "settings.json")
PRIVET = "\u041f\u0440\u0438\u0432\u0435\u0442"
put(sp, b"\xef\xbb\xbf" + json.dumps({"model": PRIVET}, ensure_ascii=False).encode("utf-8"))
code, out, err = cli("--install", "--settings", sp)
check("BOM + UTF-8 Cyrillic read fine, value kept", code == 0 and json.loads(get(sp))["model"] == PRIVET, (code, err))

# the fuse itself (N1): it must refuse the real user's settings and anything outside the test root, and it must
# let a temporary path through. Only path STRINGS are used; the real files are not opened.
fused = []
for label, p in (("real settings.json", os.path.join(REAL_HOME, ".claude", "settings.json")),
                 ("real .claude dir", os.path.join(REAL_HOME, ".claude")),
                 ("real .truegauge", os.path.join(REAL_HOME, ".truegauge", "previous_statusline.json")),
                 ("outside ROOT", os.path.join(os.path.dirname(ROOT), "tg_elsewhere", "settings.json"))):
    try:
        guard_path(p)
        fused.append(label)
    except RuntimeError as e:
        check("fuse refuses %s with an explicit message" % label, "TEST FUSE" in str(e), str(e))
check("fuse refuses every non-temporary path", fused == [], fused)
try:
    guard_path(os.path.join(ROOT, "x", "settings.json"))
    check("fuse lets a temporary path through", True)
except RuntimeError as e:
    check("fuse lets a temporary path through", False, str(e))
# the fuse is WIRED into cli(): a call aimed outside ROOT dies before truegauge runs. Only --status on paths that
# do not exist is used, so a missing fuse still cannot write anything.
for label, setter in (("settings path", None), ("state dir", "home")):
    d = fresh()
    elsewhere = tempfile.mkdtemp(prefix="tg_outside_")
    _OWN_TEMP.append(elsewhere)
    home_kept = tg.HOME
    sp = os.path.join(d, "settings.json")
    if setter == "home":
        tg.HOME = os.path.join(elsewhere, "state")
    else:
        sp = os.path.join(elsewhere, "settings.json")
    try:
        cli("--status", "--settings", sp)
        wired = False
    except RuntimeError as e:
        wired = "TEST FUSE" in str(e)
    finally:
        tg.HOME = home_kept
    check("fuse is wired into cli(): a %s outside the test root stops the call" % label, wired)
# a path inside a (fake) 'real' user directory is refused by the real-directory branch, with its own message
fake_real = tempfile.mkdtemp(prefix="tg_fakereal_")
_OWN_TEMP.append(fake_real)
REAL_PROTECTED.append(fake_real)
try:
    guard_path(os.path.join(fake_real, "settings.json"))
    check("fuse: a protected directory is refused (message names the REAL directory)", False)
except RuntimeError as e:
    check("fuse: a protected directory is refused (message names the REAL directory)", "REAL user directory" in str(e), str(e))
finally:
    REAL_PROTECTED.pop()

# defaults land in the sandbox: a call WITHOUT --settings resolves inside ROOT, not in the real home
check("default settings path (no --settings) lies in the sandbox, not in the real home",
      _inside(tg._settings_path([]), SANDBOX) and not _inside(tg._settings_path([]), os.path.join(REAL_HOME, ".claude")),
      tg._settings_path([]))

# a path with a space is quoted in the command
d = fresh()
sp = os.path.join(d, "settings.json")
cli("--install", "--settings", sp)
cmd = json.loads(get(sp))["statusLine"]["command"]
check("command has no backslashes (Git Bash would eat them)", "\\" not in cmd, cmd)
check("_quote wraps a path with a space", tg._quote("C:/a b/x.py") == '"C:/a b/x.py"' and tg._quote("C:/ab/x.py") == "C:/ab/x.py")

# the status-line mode (no args) still runs on a payload and does not touch settings
import subprocess  # noqa: E402
d = fresh()
env = dict(os.environ, TRUEGAUGE_HOME=d, TRUEGAUGE_FABLE="0")
r = subprocess.run([sys.executable, os.path.join(os.path.dirname(HERE), "truegauge.py")],
                   input=json.dumps({"context_window": {"used_percentage": 55.2, "total_input_tokens": 548200}}).encode(),
                   capture_output=True, env=env)
check("no-arg mode unchanged: payload -> line, exit 0", r.returncode == 0 and r.stdout.decode("utf-8") == "T548K 55%", r)

# the settings write fails during uninstall: the saved previous line must survive, the settings stay as they were
d = fresh()
sp = os.path.join(d, "settings.json")
put(sp, json.dumps({"statusLine": OTHER}))
cli("--install", "--settings", sp)
before = get(sp)
real_write = tg._write_atomic


def failing_write(path, text):
    if os.path.abspath(path) == os.path.abspath(sp):
        raise OSError("disk full (simulated)")
    return real_write(path, text)


tg._write_atomic = failing_write
try:
    code, out, err = cli("--uninstall", "--settings", sp)
finally:
    tg._write_atomic = real_write
check("failed settings write on uninstall: exit 2, settings unchanged", code == 2 and get(sp) == before, (code, err))
check("failed settings write on uninstall: saved previous line kept", os.path.exists(prev_file())
      and json.loads(get(prev_file()))["entries"][os.path.abspath(sp)]["statusLine"] == OTHER)
code, out, err = cli("--uninstall", "--settings", sp)
check("uninstall retried after the failure: previous line restored", code == 0 and json.loads(get(sp))["statusLine"] == OTHER)

# ======================================================================== 0.2: red group at the limit threshold
import contextlib as _ctx  # noqa: E402
import importlib.util  # noqa: E402
import re  # noqa: E402
import time  # noqa: E402

COLOR_VARS = ("NO_COLOR", "TRUEGAUGE_RED_5H", "TRUEGAUGE_RED_7D", "TRUEGAUGE_RED_COLOR", "TRUEGAUGE_COLD_COLOR", "TRUEGAUGE_MARK",
              "TRUEGAUGE_FRESH_COLOR", "TRUEGAUGE_TIME")
RED = "\033[1;38;2;255;0;0m"
RS = "\033[22;39m"
SGR_RX = re.compile(r"\x1b\[[0-9;]*m")


@_ctx.contextmanager
def envset(**kw):
    """Colour variables for one block: every colour variable is first UNSET, then the given ones are set."""
    old = {k: os.environ.get(k) for k in COLOR_VARS}
    for k in COLOR_VARS:
        os.environ.pop(k, None)
    for k, v in kw.items():
        os.environ[k] = v
    try:
        yield
    finally:
        for k in COLOR_VARS:
            os.environ.pop(k, None)
        for k, v in old.items():
            if v is not None:
                os.environ[k] = v


def g5(used, status="fresh", rin=7200):
    return R({"five_hour": win(status, used, rin)})


def g7(used, status="fresh", rin=3 * 86400):
    return R({"seven_day": win(status, used, rin)})


def red_of(text):
    return RED + text + RS


def shown(env):
    """--show output with the colour variables `env`, on a state with a 97.6 % five-hour window and a 10 % week."""
    fresh()
    n = time.time()
    tg.update(rl(five=(97.6, int(n) + 7200), seven=(10, int(n) + 3 * 86400)), n)
    with envset(**env):
        return cli("--show")[1]


# ---- table of boundaries (defaults: 5 h = 98, week = 99)
with envset():
    check("red 5h fresh 97.49 -> 97%, not red", g5(97.49) == "2h 97%", g5(97.49))
    check("red 5h fresh 97.5 -> 98%, red", g5(97.5) == red_of("2h 98%"), g5(97.5))
    check("red 5h fresh 100 -> 100%, red", g5(100) == red_of("2h 100%"), g5(100))
    check("red 5h old 97.6 -> >=97%, red (half_up 98)", g5(97.6, "stale_reading") == red_of("2h ≥97%"), g5(97.6, "stale_reading"))
    check("red 5h old 97.49 -> >=97%, not red", g5(97.49, "stale_reading") == "2h ≥97%", g5(97.49, "stale_reading"))
    check("red 5h old 100 -> >=100%, red", g5(100, "stale_reading") == red_of("2h ≥100%"), g5(100, "stale_reading"))
    check("red week fresh 98.49 -> 98%, not red", g7(98.49) == "3d 98%", g7(98.49))
    check("red week fresh 98.5 -> 99%, red", g7(98.5) == red_of("3d 99%"), g7(98.5))
    check("red week old 98.5 -> >=98%, red", g7(98.5, "stale_reading") == red_of("3d ≥98%"), g7(98.5, "stale_reading"))
    check("red: expired 5h -> green `5h`, never red; expired week -> no group",
          g5(99.9, "expired", -5) == FRESH_G and g7(99.9, "expired", -5) == "", (g5(99.9, "expired", -5), g7(99.9, "expired", -5)))
    check("red: a reading far below the threshold is not red", g5(10) == "2h 10%" and g7(10) == "3d 10%")

    # the invariant: the SAME reading, fresh then 901 s later old -- the colour does not change
    fresh()
    tg.update(rl(five=(97.6, NOW + 7200), seven=(98.5, NOW + 3 * 86400)), NOW)
    a = R(tg.read_state(NOW + 10, 900))
    b = R(tg.read_state(NOW + 10 + 901, 900))
    check("invariant: fresh reading 97.6 / 98.5 is shown 98% / 99% and red",
          a.startswith(RED + "1h 98%" + RS) and RED + "2d 99%" + RS in a, a)
    check("invariant: the same reading 901 s old is shown >=97% / >=98% and STILL red",
          b.startswith(RED + "1h ≥97%" + RS) and RED + "2d ≥98%" + RS in b, b)
    check("invariant: ageing changes the text, not the red", a.count(RED) == b.count(RED) == 2 and a != b, (a, b))
    flips = [x / 4.0 for x in range(0, 401) if ((RED in g5(x / 4.0)) != (RED in g5(x / 4.0, "stale_reading")))
             or ((RED in g7(x / 4.0)) != (RED in g7(x / 4.0, "stale_reading")))]
    check("invariant: for every reading 0..100 step 0.25 fresh and old have the same colour", flips == [], flips[:5])

# ---- thresholds from the environment
with envset(TRUEGAUGE_RED_5H="100"):
    check("threshold 100: 99.49 -> 99%, not red", g5(99.49) == "2h 99%", g5(99.49))
    check("threshold 100: 99.5 -> 100%, red", g5(99.5) == red_of("2h 100%"), g5(99.5))
with envset(TRUEGAUGE_RED_5H="1"):
    check("threshold 1: 0.49 -> 0%, not red", g5(0.49) == "2h 0%", g5(0.49))
    check("threshold 1: 0.5 -> 1%, red", g5(0.5) == red_of("2h 1%"), g5(0.5))
for off in ("off", "OFF", "Off", "0"):
    with envset(TRUEGAUGE_RED_5H=off, TRUEGAUGE_RED_7D=off):
        check("threshold %r: never red (100 %% fresh and old, both windows)" % off,
              g5(100) == "2h 100%" and g5(100, "stale_reading") == "2h ≥100%" and g7(100) == "3d 100%"
              and g7(100, "stale_reading") == "3d ≥100%", (g5(100), g7(100)))
    out = shown(dict(TRUEGAUGE_RED_5H=off, TRUEGAUGE_RED_7D=off))
    check("threshold %r: --show says off and does not call it a refusal" % off,
          "5h=off week=off" in out and "REFUSED" not in out, out)
with envset(TRUEGAUGE_RED_5H="50"):
    check("5h threshold does not move the week", g7(60) == "3d 60%" and g5(50) == red_of("2h 50%"), (g5(50), g7(60)))
with envset(TRUEGAUGE_RED_7D="50"):
    check("week threshold does not move the 5h", g5(60) == "2h 60%" and g7(50) == red_of("3d 50%"), (g5(60), g7(50)))
for var in ("TRUEGAUGE_RED_5H", "TRUEGAUGE_RED_7D"):
    out = shown({var: ""})
    with envset(**{var: ""}):
        check("%s empty -> default, --show silent (empty cancels the variable)" % var,
              g5(97.5) == red_of("2h 98%") and g5(97.49) == "2h 97%" and g7(98.5) == red_of("3d 99%")
              and g7(98.49) == "3d 98%" and "REFUSED" not in out and "5h=98 week=99" in out, out)
    for bad in ("101", "-1", "97.5", "abc", "1e2", "٩٨", "0x62", "00", "1000", "9" * 5000, "off now"):
        out = shown({var: bad})
        with envset(**{var: bad}):
            ok = (g5(97.5) == red_of("2h 98%") and g5(97.49) == "2h 97%" and g7(98.5) == red_of("3d 99%")
                  and g7(98.49) == "3d 98%")
        check("%s=%r -> default used" % (var, bad[:12]), ok)
        check("%s=%r -> --show names the refusal" % (var, bad[:12]), "REFUSED" in out and var in out, out)
    with envset(**{var: " 98 "}):
        ok = (g5(97.5) == red_of("2h 98%") and g5(97.49) == "2h 97%") if var.endswith("5H") else \
            (g7(97.5) == red_of("3d 98%") and g7(97.49) == "3d 97%")
    check("%s with spaces ' 98 ' -> accepted" % var, ok)
    check("%s with spaces ' 98 ' -> --show silent" % var, "REFUSED" not in shown({var: " 98 "}))
with envset(TRUEGAUGE_RED_5H="97.5", TRUEGAUGE_RED_7D="50"):
    check("one refused threshold does not spoil the other", g7(50) == red_of("3d 50%"))

# ---- colour
for val, want in (("#FF0000", RED), ("#ff0000", RED), ("ansi", "\033[1;31m"), ("ANSI", "\033[1;31m"),
                  ("#00ff7f", "\033[1;38;2;0;255;127m"), (" #ff0000 ", RED), ("", RED)):
    with envset(TRUEGAUGE_RED_COLOR=val):
        check("colour %r accepted -> %r" % (val, want), g5(100) == want + "2h 100%" + RS, g5(100))
    check("colour %r: --show silent" % val, "REFUSED" not in shown({"TRUEGAUGE_RED_COLOR": val}))
for val in ("#f00", "red", "#gg0000", "#ff00000", "ff0000", "31"):
    with envset(TRUEGAUGE_RED_COLOR=val):
        check("colour %r refused -> default" % val, g5(100) == red_of("2h 100%"), g5(100))
    out = shown({"TRUEGAUGE_RED_COLOR": val})
    check("colour %r refused -> --show names it" % val, "REFUSED" in out and "TRUEGAUGE_RED_COLOR" in out, out)
with envset():
    check("reset sequence is bold-off + default-foreground, not a full reset",
          RS == "\033[22;39m" and g5(100).endswith("\033[22;39m") and "\033[0m" not in g5(100))

# ---- NO_COLOR
with envset(NO_COLOR="1"):
    check("NO_COLOR=1 -> no escape at all", "\x1b" not in g5(100) and g5(100) == "2h 100%" and g7(100) == "3d 100%", g5(100))
with envset(NO_COLOR="yes please"):
    check("NO_COLOR=any non-empty text -> no colour", g5(100) == "2h 100%")
with envset(NO_COLOR=" "):
    check("NO_COLOR=' ' (non-empty) -> no colour", g5(100) == "2h 100%")
with envset(NO_COLOR=""):
    check("NO_COLOR='' (empty) -> colour is there", g5(100) == red_of("2h 100%"), g5(100))
    check("NO_COLOR='' -> --show does not claim NO_COLOR", "NO_COLOR is set" not in shown({"NO_COLOR": ""}))
check("NO_COLOR=1 -> --show says so", "NO_COLOR is set" in shown({"NO_COLOR": "1"}))

# ---- both groups red, the rest never
with envset():
    full = R({"five_hour": win("fresh", 98.2, 7200), "seven_day": win("fresh", 99.9, 3 * 86400),
              "fable": win("fresh", 100, None)}, {"new": 2600000, "complete": True}, 100, "en", 548200)
    want = red_of("2h 98%") + " " + red_of("3d 100%") + " F100% T548K 100% Σ2.6M"
    check("both groups red, a plain space between, Fable / T / % / Σ without colour", full == want, full)
    check("red wraps exactly the two groups (2 starts, 2 resets)", full.count(RED) == 2 and full.count(RS) == 2, full)
    check("Fable alone at 100 % is not red", R({"fable": win("fresh", 100, None)}) == "F100%")
    check("context fill alone at 100 % is not red", R({}, None, 100, "en", 548200) == "T548K 100%")
    check("ru words with a red group", R({"five_hour": win("fresh", 99, 7200)}, {"new": 2600000, "complete": True}, 55, "ru", 548200)
          == red_of("2h 99%") + " Т548К 55% Σ2.6М")

# ---- --show: thresholds line, the shell note, per-window verdict
out = shown({})
check("--show: with no recorded status-line settings the verdict is unknown, not a guess from the shell",
      "five_hour: past the red threshold: unknown (the status line has not recorded" in out, out)
fresh()
_n = time.time()
tg.update(rl(five=(97.6, int(_n) + 7200), seven=(10, int(_n) + 3 * 86400)), _n)
with envset():
    tg._remember_render_cfg(tg.color_config(), _n)
    out = cli("--show")[1]
check("--show: thresholds line", "colour: thresholds 5h=98 week=99, colour ESC[1;38;2;255;0;0m" in out, out)
check("--show: says the values are those of this shell, settings.json env seen only by the status line",
      "values of THIS shell" in out and "settings.json" in out and "status line itself" in out, out)
check("--show: per-window verdict (5h past, week not)", "five_hour: past the red threshold: yes" in out
      and "seven_day: past the red threshold: no" in out, out)
check("--show: with refusals the line lists them", "REFUSED (default used): TRUEGAUGE_RED_5H='abc'" in shown({"TRUEGAUGE_RED_5H": "abc"}))
check("version is 0.2.0 or later (0.3.0 is pinned in the 0.3 block)", tg.__version__ in ("0.2.0", "0.3.0", "0.4.0"))

# ---- equivalence with v0.1.0 (kept byte for byte as tests/ref_v0_1_0.py)
_spec = importlib.util.spec_from_file_location("ref_v0_1_0", os.path.join(HERE, "ref_v0_1_0.py"))
ref = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ref)
check("reference module is v0.1.0", ref.__version__ == "0.1.0", ref.__version__)
FIVES = [None, win("fresh", 0, 18000), win("fresh", 38.5, 14400), win("fresh", 97.49, 7200), win("fresh", 97.5, 7200),
         win("fresh", 97.6, 3000), win("fresh", 98, 7200), win("fresh", 100, 60), win("stale_reading", 97.6, 7200),
         win("stale_reading", 100, 7200), win("stale_reading", 38.9, 14400), win("expired", 99, -5)]
SEVENS = [None, win("fresh", 54, 5 * 86400), win("fresh", 98.49, 3 * 86400), win("fresh", 98.5, 3 * 86400),
          win("fresh", 99, 3600), win("fresh", 100, 86400), win("stale_reading", 98.5, 86400 * 2),
          win("stale_reading", 99.9, 7200), win("expired", 80, -5)]
FABLES = [None, win("fresh", 4, None), win("fresh", 100, None), win("stale_reading", 99, 86400), win("expired", 50, -5),
          {"status": "absent"}]
TOKS = [None, {"new": 2600000, "complete": True}, {"new": 2650000, "complete": False}]
CTXS = [(None, None), (55.2, 548200), (100, 999999), (0, 0), (98, None)]
states = []
for i5, f5 in enumerate(FIVES):
    for i7, f7 in enumerate(SEVENS):
        for ifb, fb in enumerate(FABLES):
            k = i5 * 31 + i7 * 7 + ifb
            st = {n: v for n, v in (("five_hour", f5), ("seven_day", f7), ("fable", fb)) if v is not None}
            states.append((st, TOKS[k % 3], CTXS[k % 5], "ru" if k % 4 == 0 else "en"))
states.append(({}, None, (None, None), "en"))  # empty input


FRESH_RX = re.compile(r"^(\x1b\[[0-9;]*m)?5h(\x1b\[22;39m)?(?! ≥?\d+%)( |$)")  # `5h` NOT followed by a percent: a live 5:00:00 is `5h 0%`


def _expired5(s):
    return (s[0].get("five_hour") or {}).get("status") == "expired"


def unfresh(s, out):
    """0.4's one intended difference from earlier versions: an expired 5-hour window is a leading `5h` group. Cut it
    (only where the state has an expired 5h, and only if it is there) so the rest is compared byte for byte."""
    if not _expired5(s):
        return out
    m = FRESH_RX.match(out)
    return out[m.end():] if m else "<NO FRESH GROUP>" + out


def both(s):
    return (unfresh(s, tg.render(s[0], s[1], s[2][0], s[3], s[2][1])), ref.render(s[0], s[1], s[2][0], s[3], s[2][1]))


check("equivalence set has at least 30 states and includes the empty one", len(states) >= 30 and states[-1][0] == {}, len(states))
with envset(NO_COLOR="1"):
    diff = [s for s in states if both(s)[0] != both(s)[1]]
check("equivalence: NO_COLOR=1 -> 0.2 output == v0.1.0 output byte for byte on all %d states" % len(states), diff == [], diff[:1])
with envset():
    outs = [both(s) for s in states]
check("equivalence: without NO_COLOR, SGR stripped -> 0.2 output == v0.1.0 output on all states",
      all(SGR_RX.sub("", a) == b for a, b in outs), [o for o in outs if SGR_RX.sub("", o[0]) != o[1]][:1])
check("equivalence is not vacuous: v0.1.0 never emits an escape, 0.2 does on many states",
      not any("\x1b" in b for a, b in outs) and sum("\x1b" in a for a, b in outs) >= 100, sum("\x1b" in a for a, b in outs))

# end to end: the real scripts on a few payloads, NO_COLOR=1 byte for byte, and stripped without NO_COLOR
_now = int(time.time())
PAYLOADS = [
    {"rate_limits": {"five_hour": {"used_percentage": 97.6, "resets_at": _now + 12000},
                     "seven_day": {"used_percentage": 99.5, "resets_at": _now + 3 * 86400 + 1800}},
     "context_window": {"used_percentage": 55.2, "total_input_tokens": 548200}},
    {"rate_limits": {"five_hour": {"used_percentage": 100, "resets_at": _now + 12000}}},
    {"rate_limits": {"five_hour": {"used_percentage": 12, "resets_at": _now + 12000},
                     "seven_day": {"used_percentage": 98.49, "resets_at": _now + 3 * 86400 + 1800}}, "context_window": {}},
    {},
]


def run_script(script, payload, extra):
    d = tempfile.mkdtemp(dir=ROOT)
    guard_path(d)
    env = {k: v for k, v in os.environ.items() if k not in COLOR_VARS}
    env.update(TRUEGAUGE_HOME=d, TRUEGAUGE_FABLE="0", **extra)
    r = subprocess.run([sys.executable, script], input=json.dumps(payload).encode(), capture_output=True, env=env)
    return r.returncode, r.stdout


ok_nc = ok_st = True
for pl in PAYLOADS:
    c1, o1 = run_script(os.path.join(HERE, "ref_v0_1_0.py"), pl, {})
    c2, o2 = run_script(os.path.join(os.path.dirname(HERE), "truegauge.py"), pl, {"NO_COLOR": "1"})
    c3, o3 = run_script(os.path.join(os.path.dirname(HERE), "truegauge.py"), pl, {})
    ok_nc = ok_nc and c1 == c2 == c3 == 0 and o1 == o2
    ok_st = ok_st and SGR_RX.sub("", o3.decode("utf-8")).encode("utf-8") == o1
check("end to end (real scripts, %d payloads): NO_COLOR=1 output == v0.1.0 byte for byte" % len(PAYLOADS), ok_nc)
check("end to end: without NO_COLOR the stripped output == v0.1.0", ok_st)
_c, _o = run_script(os.path.join(os.path.dirname(HERE), "truegauge.py"), PAYLOADS[0], {})
check("end to end: payload 97.6 / 99.5 really prints two red groups", _o.decode("utf-8").count(RED) == 2, _o)

# ---- review round 3 (lenses SF1-SF7, CR2, CR3, CR5): the status line keeps its own colour settings
def sl(used, env, now, week=10):
    """One real status-line call under colour variables `env`; returns its output."""
    pl = rl(five=(used, int(now) + 7200), seven=(week, int(now) + 3 * 86400 + 1800))
    with envset(**env):
        return tg.statusline(pl, now)


def state_now():
    return tg._read()[0]


def count_errors(fn):
    """Run fn() with _record_error counted (the original still runs)."""
    orig, calls = tg._record_error, []

    def spy(msg, **kw):
        calls.append(msg)
        return orig(msg, **kw)
    tg._record_error = spy
    try:
        fn()
    finally:
        tg._record_error = orig
    return calls


# SF1: the setting the line used is saved, only on change; a new refusal is an error once
fresh()
_n = time.time()
errs = count_errors(lambda: sl(60, {"TRUEGAUGE_RED_5H": "95%"}, _n))
rc = state_now().get("render_cfg")
check("SF1: refused value -> default used and the refusal is saved in render_cfg",
      isinstance(rc, dict) and "TRUEGAUGE_RED_5H" in rc["refused"] and rc["th"]["five_hour"] == 98, rc)
check("SF1: new refusal -> exactly one error, naming the variable",
      len(errs) == 1 and "colour setting refused" in errs[0] and "TRUEGAUGE_RED_5H" in errs[0], errs)
check("SF1: the error reaches the state as a fault of an extra (last_extra_error)", "colour setting refused" in state_now().get("last_extra_error", {}).get("msg", ""))
check("SF1: ... and NOT as last_error (a refused setting says nothing about the readings)", "last_error" not in state_now(), state_now().get("last_error"))
at1 = state_now().get("render_cfg_at")
errs = count_errors(lambda: sl(60, {"TRUEGAUGE_RED_5H": "95%"}, _n + 100))
check("SF1: same setting again -> no second error", errs == [], errs)
check("SF1: same setting again -> render_cfg_at not rewritten (written only on change)",
      state_now().get("render_cfg_at") == at1 == int(_n), (state_now().get("render_cfg_at"), at1))
errs = count_errors(lambda: sl(60, {"TRUEGAUGE_RED_5H": "50"}, _n + 200))
rc = state_now().get("render_cfg")
check("SF1: valid new setting -> saved, new time, no error",
      rc["th"]["five_hour"] == 50 and rc["refused"] == {} and state_now().get("render_cfg_at") == int(_n + 200) and errs == [], (rc, errs))
errs = count_errors(lambda: sl(60, {"TRUEGAUGE_RED_5H": "abc"}, _n + 300))
check("SF1: a different refusal after a good setting -> one more error", len(errs) == 1, errs)
errs = count_errors(lambda: sl(60, {"TRUEGAUGE_RED_5H": "abc"}, _n + 400))
check("SF1: and again the same -> none", errs == [], errs)
fresh()
errs = count_errors(lambda: sl(60, {"NO_COLOR": "1", "TRUEGAUGE_RED_5H": "off"}, _n))
rc = state_now().get("render_cfg")
check("SF1: NO_COLOR and off are saved too, and are not errors",
      rc["no_color"] is True and rc["th"]["five_hour"] is None and errs == [], (rc, errs))
fresh()
sl(60, {}, _n)
check("SF1: default settings are saved (th 98 / 99, no refusals)",
      state_now()["render_cfg"]["th"] == {"five_hour": 98, "seven_day": 99} and state_now()["render_cfg"]["refused"] == {},
      state_now().get("render_cfg"))
sl(61, {}, _n + 10)
check("SF1: the next update of the state keeps render_cfg", "render_cfg" in state_now() and state_now()["render_cfg_at"] == int(_n))
# a corrupt state is not overwritten by the saving of the settings
fresh()
with open(tg.STATE, "w", encoding="utf-8") as f:
    f.write("{not json")
tg._remember_render_cfg(tg.color_config(), _n)
check("SF1: corrupt state -> render_cfg not written over it", open(tg.STATE, encoding="utf-8").read() == "{not json")

# SF2 + CR3: --show judges by what the status line saw
fresh()
_n = time.time()
sl(60, {"TRUEGAUGE_RED_5H": "50"}, _n)  # the status line (its env) has 50; this shell has nothing
with envset():
    out = cli("--show")[1]
check("SF2: --show verdict follows the status line's threshold (50), not the shell's (98)",
      "five_hour: past the red threshold: yes" in out and "unknown (the status line" not in out, out)  # "cold now: unknown" is a different line
check("SF2: --show labels the status-line settings with the time it saw them",
      "status line colour: thresholds 5h=50 week=99" in out and "as the status line saw it at" in out, out)
check("SF2: the shell's own values stay on their own line", "colour: thresholds 5h=98 week=99" in out and "values of THIS shell" in out, out)
fresh()
sl(60, {"TRUEGAUGE_RED_5H": "99"}, _n)
with envset(TRUEGAUGE_RED_5H="50"):  # the shell says 50, the status line 99: 60 % is not past 99
    out = cli("--show")[1]
check("SF2: the shell's threshold does not decide the verdict", "five_hour: past the red threshold: no" in out, out)
fresh()
sl(97.6, {"NO_COLOR": "1"}, _n)
with envset():
    out = cli("--show")[1]
check("CR3: NO_COLOR on the status line -> verdict 'yes (not drawn: NO_COLOR)'",
      "five_hour: past the red threshold: yes (not drawn: NO_COLOR)" in out, out)
fresh()
sl(97.6, {"TRUEGAUGE_RED_5H": "95%"}, _n)
with envset():
    out = cli("--show")[1]
check("SF2: a refusal on the status line is listed in --show though the shell is clean",
      "REFUSED (default used): TRUEGAUGE_RED_5H='95%'" in out, out)
fresh()
tg.update(rl(five=(97.6, int(_n) + 7200)), _n)
d = state_now()
d["render_cfg"] = {"th": "broken"}
tg._save(d)
with envset():
    out = cli("--show")[1]
check("SF2: a damaged render_cfg reads as unknown, --show does not crash", "status line colour: unknown" in out, out)

# SF4: a fault in the colour code costs the colour, not the line
NUMS = ({"five_hour": win("fresh", 100, 7200)}, {"new": 2600000, "complete": True}, 55, "en", 548200)
for what in ("_sgr", "color_config", "_reached"):
    fresh()
    orig = getattr(tg, what)

    def boom(*a, **k):
        raise RuntimeError("probe")
    setattr(tg, what, boom)
    try:
        with envset():
            errs = count_errors(lambda: R(*NUMS))
            line = R(*NUMS)
            _t = time.time()
            via_main = tg.statusline(rl(five=(100, int(_t) + 7200)), _t)
    finally:
        setattr(tg, what, orig)
    check("SF4: %s raises -> the line still has its numbers" % what, line == "2h 100% T548K 55% Σ2.6M", line)
    check("SF4: %s raises -> no escape in the line" % what, "\x1b" not in line, line)
    check("SF4: %s raises -> the error is recorded" % what, any("probe" in e for e in errs), errs)
    check("SF4: %s raises -> the whole status line still answers" % what, "100%" in via_main and "\033" not in via_main, via_main)

# CR2: a threshold different from the default, with spaces; a value of only spaces = not set, silently
for var, g in (("TRUEGAUGE_RED_5H", g5), ("TRUEGAUGE_RED_7D", g7)):
    with envset(**{var: " 97 "}):
        check("CR2: %s=' 97 ' (not the default) -> 96.49 plain, 96.5 red" % var, RED not in g(96.49) and RED in g(96.5), (g(96.49), g(96.5)))
    out = shown({var: " 97 "})
    check("CR2: %s=' 97 ' -> --show silent and shows 97" % var,
          "REFUSED" not in out and ("5h=97" in out if var.endswith("5H") else "week=97" in out), out)
for var in ("TRUEGAUGE_RED_5H", "TRUEGAUGE_RED_7D", "TRUEGAUGE_RED_COLOR"):
    for blank in ("  ", " ", "\t"):
        out = shown({var: blank})
        with envset(**{var: blank}):
            ok = g5(97.5) == red_of("2h 98%") and g7(98.5) == red_of("3d 99%") and g5(97.49) == "2h 97%"
        check("CR2: %s=%r (spaces only) = not set: default, --show silent" % (var, blank), ok and "REFUSED" not in out, out)

# ======================================================================== 0.3: blue context group when the main session's cache is cold
# (table of boundaries: 01_PROEKT.md section 2; now = 1000000)
AMB = "\033[1;38;2;177;185;249m"
UL, ULX = "\033[4m", "\033[24m"
CNOW = 1_800_000_000  # a plausible epoch: expires_at outside CACHE_EPOCH_MIN..MAX is "unknown" (item 6)
CTX = "T548K 55%"


def blue(text):
    return AMB + text + RS


def cold_of(pc, now=CNOW):
    return tg._cache_cold(pc, now)


def ctxline(pc, now=CNOW, st=None, tokens=None, pct=55.2, cti=548200):
    """The line the renderer gives for a prompt_cache value: the same decision function the status line uses."""
    return tg.render(st or {}, tokens, pct, "en", cti, cold=cold_of(pc, now))


def PC(observed=True, warm=True, exp=CNOW + 100, **more):
    d = {"caching_observed": observed, "warm": warm, "expires_at": exp, "ttl": "1h"}
    d.update(more)
    return {k: v for k, v in d.items() if v is not ABSENT}


ABSENT = object()

with envset():
    # ---- the decision (rule 2) -- one check per row of the table
    check("cold: prompt_cache absent -> plain", ctxline(None) == CTX, ctxline(None))
    for what, pc in (("null", None), ("a list", []), ("a string", "cold"), ("a number", 5), ("true", True)):
        check("cold: prompt_cache = %s -> plain" % what, ctxline(pc) == CTX and cold_of(pc) is False, ctxline(pc))
    check("cold: observed, warm, expires_at = now+1 -> plain", ctxline(PC(exp=CNOW + 1)) == CTX)
    check("cold: observed, warm, expires_at = now -> BLUE (the moment it cools)", ctxline(PC(exp=CNOW)) == blue(CTX), ctxline(PC(exp=CNOW)))
    check("cold: observed, warm, expires_at = now-1 -> BLUE", ctxline(PC(exp=CNOW - 1)) == blue(CTX), ctxline(PC(exp=CNOW - 1)))
    check("cold: observed, warm=false, expires_at=null -> BLUE", ctxline(PC(warm=False, exp=None)) == blue(CTX))
    check("cold: observed, warm=false, expires_at=now+3600 -> BLUE (warm wins over a future time)",
          ctxline(PC(warm=False, exp=CNOW + 3600)) == blue(CTX))
    check("cold: observed=false, warm=false -> plain (nothing was cached, nothing cooled)", ctxline(PC(observed=False, warm=False)) == CTX)
    check("cold: observed missing, warm=false -> plain", ctxline(PC(observed=ABSENT, warm=False)) == CTX)
    check("cold: observed='true' (a string), warm=false -> plain", ctxline(PC(observed="true", warm=False)) == CTX)
    check("cold: observed=1 (a number), warm=false -> plain", ctxline(PC(observed=1, warm=False)) == CTX)
    check("cold: observed=true, warm='false' (a string), expires_at=now+10 -> plain", ctxline(PC(warm="false", exp=CNOW + 10)) == CTX)
    check("cold: observed=true, warm='false', expires_at=now-10 -> BLUE (by the time)", ctxline(PC(warm="false", exp=CNOW - 10)) == blue(CTX))
    check("cold: warm=0 (a number, not False) with a past time -> by the time only", ctxline(PC(warm=0, exp=CNOW - 10)) == blue(CTX)
          and ctxline(PC(warm=0, exp=CNOW + 10)) == CTX)
    check("cold: observed, warm=true, expires_at=null -> plain", ctxline(PC(exp=None)) == CTX)
    check("cold: warm absent, expires_at in the past -> BLUE", ctxline(PC(warm=ABSENT, exp=CNOW - 1)) == blue(CTX))
    check("cold: warm absent, expires_at absent -> plain", ctxline(PC(warm=ABSENT, exp=ABSENT)) == CTX)
    for bad in ("1000", "999999", True, False, [1], {"a": 1}, float("nan"), float("inf"), float("-inf")):
        check("cold: observed, warm=true, expires_at=%r -> plain (not a number, not judged)" % (bad,), ctxline(PC(exp=bad)) == CTX)
    check("cold: expires_at as a float in the past -> BLUE", ctxline(PC(exp=CNOW - 0.5)) == blue(CTX))

    # ---- what is painted (rule 1)
    check("cold, no T and no percent (0 tokens) -> nothing painted, line empty",
          tg.render({}, None, None, "en", 0, cold=True) == "")
    check("cold, 0 tokens with a percent of 0 -> nothing painted, no reading",
          tg.render({}, None, 0, "en", 0, cold=True) == "")
    base = tg.render({"five_hour": win("fresh", 38, 14400)}, {"new": 2600000, "complete": True}, None, "en", 0, cold=False)
    check("cold, no reading: the line equals the line with a warm cache",
          tg.render({"five_hour": win("fresh", 38, 14400)}, {"new": 2600000, "complete": True}, None, "en", 0, cold=True) == base, base)
    check("cold, only a percent (no T) -> only the percent is painted", tg.render({}, None, 55.2, "en", None, cold=True) == blue("55%"))
    check("cold, only T (no percent) -> only T is painted", tg.render({}, None, None, "en", 548200, cold=True) == blue("T548K"))
    check("cold, both -> one sequence around both with the space inside",
          tg.render({}, None, 55.2, "en", 548200, cold=True) == AMB + "T548K 55%" + RS)
    full = tg.render({"five_hour": win("fresh", 98.2, 7200), "fable": win("fresh", 4, None)},
                     {"new": 2600000, "complete": True}, 55.2, "en", 548200, cold=True)
    check("cold + 5h past the red threshold: 5h red, context blue, plain spaces between, F and Σ without colour",
          full == red_of("2h 98%") + " F4% " + blue(CTX) + " Σ2.6M", full)
    check("cold: Σ is never painted (2 sequences starts, 2 resets)", full.count("\x1b[1;38") == 2 and full.count(RS) == 2, full)
    full_ru = tg.render({}, {"new": 2600000, "complete": True}, 55.2, "ru", 548200, cold=True)
    check("cold, ru words: blue around Т548К 55%, Σ outside", full_ru == blue("Т548К 55%") + " Σ2.6М", full_ru)
    check("cold group sits between Fable and Σ (order of groups unchanged)",
          full.index("F4%") < full.index(AMB) < full.index("Σ"))
    check("a warm cache: the full line has no blue at all",
          AMB not in tg.render({"five_hour": win("fresh", 98.2, 7200)}, {"new": 2600000, "complete": True}, 55.2, "en", 548200, cold=False))

with envset(NO_COLOR="1"):
    check("NO_COLOR=1 + cold: no escape at all, line equals the warm one",
          tg.render({"five_hour": win("fresh", 100, 7200)}, {"new": 2600000, "complete": True}, 55.2, "en", 548200, cold=True)
          == "2h 100% T548K 55% Σ2.6M")

# ---- COLD colour setting (rule 3)
for off in ("off", "OFF", "Off", "0", " off "):
    with envset(TRUEGAUGE_COLD_COLOR=off):
        got = tg.render({}, None, 55.2, "en", 548200, cold=True)
        check("TRUEGAUGE_COLD_COLOR=%r -> no colour" % off, got == CTX, got)
    out = shown({"TRUEGAUGE_COLD_COLOR": off})
    check("TRUEGAUGE_COLD_COLOR=%r -> --show says off, not a refusal" % off, "cold colour: off" in out and "REFUSED" not in out, out)
with envset(TRUEGAUGE_COLD_COLOR="ansi"):
    check("TRUEGAUGE_COLD_COLOR=ansi -> bold bright blue", tg.render({}, None, 55.2, "en", 548200, cold=True) == "\033[1;94m" + CTX + RS)
with envset(TRUEGAUGE_COLD_COLOR="ANSI"):
    check("TRUEGAUGE_COLD_COLOR=ANSI -> bold bright blue", tg.render({}, None, 55.2, "en", 548200, cold=True) == "\033[1;94m" + CTX + RS)
for val in ("#B1B9F9", "#b1b9f9", " #b1b9f9 "):
    with envset(TRUEGAUGE_COLD_COLOR=val):
        check("TRUEGAUGE_COLD_COLOR=%r -> accepted (blue)" % val, tg.render({}, None, 55.2, "en", 548200, cold=True) == blue(CTX))
    check("TRUEGAUGE_COLD_COLOR=%r -> --show silent" % val, "REFUSED" not in shown({"TRUEGAUGE_COLD_COLOR": val}))
with envset(TRUEGAUGE_COLD_COLOR="#00ff7f"):
    check("TRUEGAUGE_COLD_COLOR=#00ff7f -> that colour, bold",
          tg.render({}, None, 55.2, "en", 548200, cold=True) == "\033[1;38;2;0;255;127m" + CTX + RS)
for val in ("yellow", "#ffa", "#gg0000", "#ffaf000", "ffaf00", "33", "of"):
    with envset(TRUEGAUGE_COLD_COLOR=val):
        got = tg.render({}, None, 55.2, "en", 548200, cold=True)
    out = shown({"TRUEGAUGE_COLD_COLOR": val})
    check("TRUEGAUGE_COLD_COLOR=%r -> default blue used" % val, got == blue(CTX), got)
    check("TRUEGAUGE_COLD_COLOR=%r -> --show names the refusal" % val, "REFUSED" in out and "TRUEGAUGE_COLD_COLOR" in out, out)
for blank in ("", "  ", "\t"):
    with envset(TRUEGAUGE_COLD_COLOR=blank):
        got = tg.render({}, None, 55.2, "en", 548200, cold=True)
    check("TRUEGAUGE_COLD_COLOR=%r -> default blue, --show silent" % blank,
          got == blue(CTX) and "REFUSED" not in shown({"TRUEGAUGE_COLD_COLOR": blank}), got)
with envset(TRUEGAUGE_RED_5H="off"):
    got = tg.render({"five_hour": win("fresh", 100, 7200)}, None, 55.2, "en", 548200, cold=True)
    check("red threshold off + cold: no red, blue present (settings are independent)", got == "2h 100% " + blue(CTX), got)
with envset(TRUEGAUGE_COLD_COLOR="off"):
    got = tg.render({"five_hour": win("fresh", 100, 7200)}, None, 55.2, "en", 548200, cold=True)
    check("cold colour off + red window: red stays (settings are independent)", got == red_of("2h 100%") + " " + CTX, got)
with envset(TRUEGAUGE_RED_COLOR="ansi"):
    got = tg.render({"five_hour": win("fresh", 100, 7200)}, None, 55.2, "en", 548200, cold=True)
    check("red colour setting does not change the blue one", got == "\033[1;31m2h 100%" + RS + " " + blue(CTX), got)

# ---- the mark without colour (rule 8a): TRUEGAUGE_MARK = list of underline, braces, bold | all | off
BO, BOX = "\033[1m", "\033[22m"  # bold on / bold off (no colour in the line)
with envset():
    five = {"five_hour": win("fresh", 98.2, 7200)}
    both_w = {"five_hour": win("fresh", 98.2, 7200), "seven_day": win("fresh", 99.9, 3 * 86400), "fable": win("fresh", 100, None)}
    tk = {"new": 2600000, "complete": True}
    plain_all = tg.render(both_w, tk, 55.2, "en", 548200, cold=True)
    check("mark: unset -> no mark anywhere, line is the colour-only line",
          plain_all == red_of("2h 98%") + " " + red_of("3d 100%") + " F100% " + blue(CTX) + " Σ2.6M" and "{" not in plain_all and UL not in plain_all and BO not in plain_all, plain_all)
with envset(TRUEGAUGE_MARK=""):
    check("mark: empty -> off, silently", tg.render(both_w, tk, 55.2, "en", 548200, cold=True) == plain_all
          and "REFUSED" not in shown({"TRUEGAUGE_MARK": ""}))
# each element alone (with colour)
with envset(TRUEGAUGE_MARK="underline"):
    got = tg.render(five, None, None, "en", None)
    check("mark=underline, 5h past the threshold: colour, underline on, text, underline off, colour reset",
          got == RED + UL + "2h 98%" + ULX + RS, got)
    got = tg.render({}, None, 55.2, "en", 548200, cold=True)
    check("mark=underline, cold: blue, underline around the whole context group",
          got == AMB + UL + CTX + ULX + RS, got)
    check("mark=underline: a group not past the threshold has no mark", tg.render({"five_hour": win("fresh", 38, 7200)}, None, None, "en", None) == "2h 38%")
with envset(TRUEGAUGE_MARK="braces"):
    got = tg.render({}, None, 55.2, "en", 548200, cold=True)
    check("mark=braces, cold: {T548K 55%} inside the blue", got == AMB + "{" + CTX + "}" + RS, got)
    got = tg.render(five, None, None, "en", None)
    check("mark=braces, 5h red: {2h 98%} inside the red, no underline, no square bracket",
          got == RED + "{2h 98%}" + RS and UL not in got and "[" not in SGR_RX.sub("", got) and "]" not in got, got)
with envset(TRUEGAUGE_MARK="bold"):
    got = tg.render(five, None, None, "en", None)
    check("mark=bold + colour: the colour already carries bold -> line equals the colour-only line (bold not doubled)",
          got == RED + "2h 98%" + RS and BO not in got and BOX not in got, got)
    got = tg.render({}, None, 55.2, "en", 548200, cold=True)
    check("mark=bold + colour, cold: blue only, bold not doubled", got == AMB + CTX + RS and BO not in got, got)
# pairs (with colour); order of the list does not matter
with envset(TRUEGAUGE_MARK="underline,braces"):
    check("mark=underline,braces + colour: colour, underline, {text}, underline off, reset",
          tg.render(five, None, None, "en", None) == RED + UL + "{2h 98%}" + ULX + RS)
with envset(TRUEGAUGE_MARK="underline,bold"):
    check("mark=underline,bold + colour: bold not doubled", tg.render(five, None, None, "en", None) == RED + UL + "2h 98%" + ULX + RS)
with envset(TRUEGAUGE_MARK="braces,bold"):
    check("mark=braces,bold + colour: bold not doubled", tg.render(five, None, None, "en", None) == RED + "{2h 98%}" + RS)
with envset(TRUEGAUGE_MARK="bold,underline"):
    check("mark=bold,underline == underline,bold (list order does not change the line)",
          tg.render(five, None, None, "en", None) == RED + UL + "2h 98%" + ULX + RS)
# all
with envset(TRUEGAUGE_MARK="all"):
    got = tg.render(both_w, tk, 55.2, "en", 548200, cold=True)
    want = (RED + UL + "{2h 98%}" + ULX + RS + " " + RED + UL + "{3d 100%}" + ULX + RS + " F100% "
            + AMB + UL + "{" + CTX + "}" + ULX + RS + " Σ2.6M")
    check("mark=all, both windows past and cold: three groups in braces, underlined, bold carried by the colour; F and Σ without mark", got == want, got)
    check("mark=all: Σ and Fable carry no brace", "{Σ" not in got and "{F" not in got and got.count("{") == 3 and got.count("}") == 3, got)
    check("mark=all: every underline-on has its underline-off; no square bracket", got.count(UL) == got.count(ULX) == 3 and "[" not in SGR_RX.sub("", got), got)
    check("mark=all, nothing past the threshold, cache warm -> line equals the unmarked line byte for byte",
          tg.render({"five_hour": win("fresh", 38, 7200)}, tk, 55.2, "en", 548200, cold=False) == "2h 38% T548K 55% Σ2.6M")
    nothing = {"five_hour": win("fresh", 38, 7200), "seven_day": win("fresh", 10, 3 * 86400)}
    with_all = tg.render(nothing, tk, 55.2, "en", 548200, cold=False)
    os.environ["TRUEGAUGE_MARK"] = ""
    without = tg.render(nothing, tk, 55.2, "en", 548200, cold=False)
    check("mark=all, nothing past any threshold and cache warm -> output == no mark, byte for byte",
          with_all == without and with_all.encode("utf-8") == without.encode("utf-8"), (with_all, without))
    os.environ["TRUEGAUGE_MARK"] = "all"
    check("mark=all: the session total alone (Σ) is never marked", tg.render({}, tk, None, "en", None, cold=True) == "Σ2.6M")
for val in ("ALL", " all ", "All", " underline , bold ", "Bold, BRACES", "bold,bold", " UNDERLINE"):
    with envset(TRUEGAUGE_MARK=val):
        got = tg.render({}, None, 55.2, "en", 548200, cold=True)
        cfgm = tg.color_config()["mark"]
    check("mark=%r -> accepted, canonical %r" % (val, cfgm), cfgm != "off" and "REFUSED" not in shown({"TRUEGAUGE_MARK": val}), (cfgm, got))
for val, canon in (("ALL", "underline,braces,bold"), (" underline , bold ", "underline,bold"), ("Bold, BRACES", "braces,bold"),
                   ("bold,bold", "bold"), ("bold,underline,braces", "underline,braces,bold"), ("all,bold", "underline,braces,bold"),
                   ("UNDERLINE", "underline"), ("off", "off"), ("  OFF ", "off")):
    check("mark %r -> %r" % (val, canon), tg._mark(val) == (canon, None), tg._mark(val))
# no colour: bold has its own on/off
with envset(TRUEGAUGE_MARK="bold", NO_COLOR="1"):
    got = tg.render(five, None, None, "en", None)
    check("mark=bold + NO_COLOR=1: ESC[1m ... ESC[22m around the group", got == BO + "2h 98%" + BOX, got)
    got = tg.render({}, None, 55.2, "en", 548200, cold=True)
    check("mark=bold + NO_COLOR=1, cold: bold context group", got == BO + CTX + BOX, got)
with envset(TRUEGAUGE_MARK="all", NO_COLOR="1"):
    got = tg.render({}, None, 55.2, "en", 548200, cold=True)
    check("mark=all + NO_COLOR=1, cold: bold, underline, {text}, underline off, bold off; no colour",
          got == BO + UL + "{" + CTX + "}" + ULX + BOX, got)
    got = tg.render(five, None, None, "en", None)
    check("mark=all + NO_COLOR=1, 5h red: mark stays, no colour", got == BO + UL + "{2h 98%}" + ULX + BOX, got)
    check("mark=all + NO_COLOR=1: no colour sequence in the line", "38;2" not in got and RS not in got, got)
with envset(TRUEGAUGE_MARK="all", TRUEGAUGE_COLD_COLOR="off", TRUEGAUGE_RED_COLOR="ansi"):
    got = tg.render({}, None, 55.2, "en", 548200, cold=True)
    check("mark=all + cold colour off: the mark still stands, bold on its own sequence", got == BO + UL + "{" + CTX + "}" + ULX + BOX, got)
with envset(TRUEGAUGE_MARK="underline,braces", NO_COLOR="1"):
    check("mark=underline,braces + NO_COLOR=1: no bold sequence at all",
          tg.render({}, None, 55.2, "en", 548200, cold=True) == UL + "{" + CTX + "}" + ULX)
# refusals: the WHOLE setting goes off, the unknown element is named
for val in ("brackets", "both", "box", "1", "yes", "frame", "underline,brackets", "both!", "bold,box", "all,box", "bold,", ",bold", "off,bold", "underline;bold"):
    with envset(TRUEGAUGE_MARK=val):
        got = tg.render(both_w, tk, 55.2, "en", 548200, cold=True)
        refusal = tg.color_config()["refused"].get("TRUEGAUGE_MARK", "")
    out = shown({"TRUEGAUGE_MARK": val})
    check("mark=%r -> off as a whole (no part switched on)" % val, got == plain_all and tg.color_config({"TRUEGAUGE_MARK": val})["mark"] == "off", got)
    check("mark=%r -> --show names the refusal" % val, "REFUSED" in out and "TRUEGAUGE_MARK" in out, out)
for val, name in (("bold,box", "'box'"), ("brackets", "'brackets'"), ("both", "'both'"), ("underline,brackets", "'brackets'"), ("all,box", "'box'")):
    out = shown({"TRUEGAUGE_MARK": val})
    check("mark=%r -> --show names the item %s" % (val, name), name in out, out)
with envset(TRUEGAUGE_MARK="bold,box", NO_COLOR="1"):
    check("mark=bold,box + NO_COLOR=1: bold is not switched on quietly", tg.render(five, None, None, "en", None) == "2h 98%")
with envset(TRUEGAUGE_MARK="off"):
    check("mark=off -> no mark, silent", tg.render(both_w, tk, 55.2, "en", 548200, cold=True) == plain_all
          and "REFUSED" not in shown({"TRUEGAUGE_MARK": "off"}))
with envset(TRUEGAUGE_MARK="braces", TRUEGAUGE_RED_5H="off"):
    got = tg.render({"five_hour": win("fresh", 100, 7200)}, None, None, "en", None)
    check("mark=braces, 5h threshold off, 5h at 100% -> no mark (no condition, no mark)", got == "2h 100%", got)
with envset(TRUEGAUGE_MARK="underline"):
    check("mark=underline: the fresh `5h` after a reset is not marked (it has no percent to mark)",
          tg.render({"five_hour": win("expired", 99.9, -5)}, None, None, "en", None) == FRESH_G,
          tg.render({"five_hour": win("expired", 99.9, -5)}, None, None, "en", None))
    check("mark=underline, cold but no reading: nothing to mark", tg.render({}, None, None, "en", 0, cold=True) == "")
    check("mark=underline, old (>=) reading past the threshold: marked like a fresh one",
          tg.render({"five_hour": win("stale_reading", 97.6, 7200)}, None, None, "en", None) == RED + UL + "2h ≥97%" + ULX + RS)
# braces on a group with ≥ and Cyrillic: printed as is (the group is concatenated, never a format template)
with envset(TRUEGAUGE_MARK="braces"):
    old_ru = tg.render({"five_hour": win("stale_reading", 97.6, 7200)}, None, None, "ru", None)
    plain_ru = SGR_RX.sub("", old_ru)
    check("mark=braces on a '≥' group: {…≥97%} printed as is", plain_ru.startswith("{") and plain_ru.endswith("%}") and "≥97%" in plain_ru, old_ru)
    ctx_ru = tg.render({}, None, 55.2, "ru", 548200, cold=True)
    stripped = SGR_RX.sub("", ctx_ru)
    check("mark=braces, Russian line: the context group {…} with its Russian letters intact",
          stripped.startswith("{") and stripped.endswith("}") and any("а" <= c.lower() <= "я" for c in stripped) and "55%" in stripped, ctx_ru)
for hostile in ("a{0}b%s{x}%d", "{}", "%(x)s {y!r} {{", "T548K ≥55% Ж"):
    for mk in ("braces", "underline,braces,bold", "all"):
        for paint in (None, AMB):
            got = tg._decorate(hostile, paint, tg._mark(mk)[0])
            inner = SGR_RX.sub("", got)
            check("_decorate(%r, paint=%s, %s) keeps the text verbatim inside {…}" % (hostile, bool(paint), mk),
                  inner == "{" + hostile + "}", got)
check("_decorate with mark off and no colour returns the group unchanged", tg._decorate("a{0}%s", None, "off") == "a{0}%s")
check("_mark_valid: canonical lists and off pass; 'brackets' / 'both' / '' / non-str do not",
      all(tg._mark_valid(x) for x in ("off", "underline", "underline,braces,bold", "bold")) and not any(tg._mark_valid(x) for x in ("brackets", "both", "", "box", None, 5, ["bold"], "bold,")))

# ---- --show: cold colour, mark, cache record
out = shown({"TRUEGAUGE_MARK": "all", "TRUEGAUGE_COLD_COLOR": "ansi"})
check("--show: the mark line (this shell)", "mark: underline,braces,bold" in out and "cold colour: ESC[1;94m" in out, out)
out = shown({"TRUEGAUGE_MARK": " Bold, underline "})
check("--show: the mark line shows the canonical list", "mark: underline,bold" in out, out)
out = shown({})
check("--show: defaults are blue and mark off", "cold colour: ESC[1;38;2;177;185;249m; mark: off" in out, out)
fresh()
_n = time.time()
tg.update(rl(five=(97.6, int(_n) + 7200)), _n)
check("--show: no record of prompt_cache -> 'cache: no record yet'", "cache: no record yet" in cli("--show")[1], cli("--show")[1])
with envset(TRUEGAUGE_MARK="underline"):
    tg._remember_render_cfg(tg.color_config(), _n)
    out = cli("--show")[1]
check("--show: the status line's own mark and cold colour are shown with its settings",
      "status line cold colour: ESC[1;38;2;177;185;249m; mark: underline" in out, out)
_cfg0 = {"th": {"five_hour": 98, "seven_day": 99}, "sgr": RED, "no_color": False, "refused": {}, "cold_sgr": AMB}
for _m, _ok in (("off", True), ("underline,bold", True), ("underline,braces,bold", True), ("brackets", False), ("both", False), ("bold,box", False), ("", False), (5, False)):
    _got = tg._saved_render_cfg({"render_cfg": dict(_cfg0, mark=_m), "render_cfg_at": 5})[0]
    check("saved settings with mark %r -> %s" % (_m, "read" if _ok else "damaged (the old words brackets / both are not valid)"), (_got is not None) == _ok, _got)

# ---- the measuring instrument: the last prompt_cache the status line saw
def cache_rec():
    return state_now().get("prompt_cache_seen")


def sl_pc(pc, now, key="prompt_cache", **extra):
    pl = rl(five=(10, int(now) + 7200), context_window={"used_percentage": 55.2, "total_input_tokens": 548200}, **extra)
    if pc is not ABSENT:
        pl[key] = pc
    old_fable = os.environ.get("TRUEGAUGE_FABLE")
    os.environ["TRUEGAUGE_FABLE"] = "0"  # no background `claude -p /usage` from these checks
    try:
        with envset():
            return tg.statusline(pl, now)
    finally:
        if old_fable is None:
            os.environ.pop("TRUEGAUGE_FABLE", None)
        else:
            os.environ["TRUEGAUGE_FABLE"] = old_fable


fresh()
_n = time.time()
sl_pc(ABSENT, _n)
r = cache_rec()
check("cache record: no prompt_cache -> present=False, every field empty",
      isinstance(r, dict) and r["present"] is False and r["warm"] is None and r["expires_at"] is None and r["seen_at"] == int(_n), r)
check("cache record: --show says present=False and cold now: unknown (finding 4: no data is not 'no')",
      "present=False" in cli("--show")[1] and "cold now: unknown (no prompt_cache object)" in cli("--show")[1], cli("--show")[1])
sl_pc(ABSENT, _n + 50)
check("cache record: the same thing again -> not rewritten (seen_at unchanged)", cache_rec()["seen_at"] == int(_n), cache_rec())
pcx = {"warm": True, "caching_observed": True, "ttl": "1h", "expires_at": int(_n) + 3000, "recache_tokens_if_cold": 548200,
       "other": "ignored"}
sl_pc(pcx, _n + 100)
r = cache_rec()
check("cache record: an object -> the six fields kept as sent, seen_at is the new time",
      r == {"present": True, "warm": True, "caching_observed": True, "ttl": "1h", "expires_at": int(_n) + 3000,
            "recache_tokens_if_cold": 548200, "seen_at": int(_n) + 100}, r)
check("cache record: unknown fields are not stored", "other" not in r)
sl_pc(pcx, _n + 200)
check("cache record: the same object again -> not rewritten", cache_rec()["seen_at"] == int(_n) + 100, cache_rec())
sl_pc(dict(pcx, expires_at=int(_n) + 3300), _n + 300)
check("cache record: a changed expires_at -> rewritten with the new time", cache_rec()["seen_at"] == int(_n) + 300
      and cache_rec()["expires_at"] == int(_n) + 3300, cache_rec())
sl_pc(dict(pcx, warm="false", ttl={"a": 1}, expires_at="soon", recache_tokens_if_cold=[1]), _n + 400)
r = cache_rec()
check("cache record: a value of the wrong type is kept as seen (strings) or by its type name (containers), never lost",
      r["warm"] == "false" and r["ttl"] == "<dict>" and r["expires_at"] == "soon" and r["recache_tokens_if_cold"] == "<list>", r)
sl_pc("not an object", _n + 500)
check("cache record: prompt_cache that is not an object -> present=False", cache_rec()["present"] is False, cache_rec())
sl_pc(dict(pcx, warm="x" * 500), _n + 600)
check("cache record: long text is cut", len(cache_rec()["warm"]) == 32, cache_rec())
sl_pc(dict(pcx, expires_at=float("nan")), _n + 700)
check("cache record: nan is stored as text, the state stays valid JSON",
      cache_rec()["expires_at"] == "<float nan/inf>" and tg._read()[1] is None, cache_rec())
check("cache record: the state still holds the plan numbers after the cache writes", "five_hour" in state_now() and "render_cfg" in state_now())
# --show reading of the record
for label, pc, want in (("expires in the future", dict(pcx, expires_at=int(time.time()) + 100), ("expires in", "cold now: no")),
                        ("expired", dict(pcx, expires_at=int(time.time()) - 100), ("expired", "cold now: yes")),
                        ("warm=false", dict(pcx, warm=False, expires_at=None), ("expires_at=None", "cold now: yes")),
                        ("not observed", dict(pcx, caching_observed=False, warm=False), ("caching_observed=False", "cold now: no"))):
    fresh()
    _t = time.time()
    sl_pc(pc, _t)
    out = cli("--show")[1]
    line = [x for x in out.splitlines() if x.startswith("cache:")]
    check("--show cache line, %s" % label, len(line) == 1 and all(w in line[0] for w in want) and "present=True" in line[0]
          and "ttl='1h'" in line[0] and "recache_if_cold=548200" in line[0] and "as the status line saw it at" in line[0], line)
fresh()
tg.update(rl(five=(10, int(_n) + 7200)), _n)
d = state_now()
d["prompt_cache_seen"] = {"present": "yes"}
tg._save(d)
check("--show: a damaged cache record is named, --show does not crash", "cache: the record is damaged" in cli("--show")[1])

# ---- the whole status line with a prompt_cache (the time passed in is the time used)
fresh()
_n = 1_700_000_000.0
out1 = sl_pc(dict(pcx, expires_at=int(_n) - 1), _n)
check("statusline: expires_at one second before `now` -> blue context group", AMB + CTX + RS in out1, out1)
fresh()
out2 = sl_pc(dict(pcx, expires_at=int(_n) + 1), _n)
check("statusline: expires_at one second after `now` -> no blue", AMB not in out2 and CTX in out2, out2)
fresh()
out3 = sl_pc(dict(pcx, warm=False), _n)
check("statusline: warm=false -> blue", AMB + CTX + RS in out3, out3)
fresh()
out4 = sl_pc(ABSENT, _n)
check("statusline: no prompt_cache -> no blue", AMB not in out4 and CTX in out4, out4)
check("statusline: blue is the only difference between cold and warm lines", SGR_RX.sub("", out1) == SGR_RX.sub("", out2) == SGR_RX.sub("", out4))

# SF4 for the new code: a fault costs the colour or the record, never the line
fresh()
_n = time.time()
for what in ("_cache_cold", "_decorate", "_cache_apply", "_cfg_apply", "_cold_sgr", "_mark"):
    fresh()
    orig = getattr(tg, what)

    def boom(*a, **k):
        raise RuntimeError("probe")
    setattr(tg, what, boom)
    try:
        errs = count_errors(lambda: sl_pc(dict(pcx, warm=False), _n))
        line = sl_pc(dict(pcx, warm=False), _n)
    finally:
        setattr(tg, what, orig)
    check("SF4 (0.3): %s raises -> the line still has its numbers" % what, "T548K 55%" in line and "10%" in line, line)
    check("SF4 (0.3): %s raises -> no half-written escape (every start has its reset)" % what,
          line.count("\x1b[1;") == line.count(RS) and line.count(UL) == line.count(ULX), line)
    check("SF4 (0.3): %s raises -> the error is recorded" % what, any("probe" in e for e in errs), errs)
fresh()
orig = tg._cache_cold
tg._cache_cold = boom
try:
    line = sl_pc(dict(pcx, warm=False), _n)
finally:
    tg._cache_cold = orig
check("SF4 (0.3): the cold test failing means not cold (no blue, no error escape)", AMB not in line and CTX in line, line)

# ---- 0.3 lens findings (order 20, 2026-10-10): every check carries its finding number
def sl_sid(pc, now, sid, env=None):
    with envset(**(env or {})):
        return sl_pc(pc, now, session_id=sid)


# finding 3: the status line's own last call is kept apart from the time the record CHANGED, refreshed once per 60 s
fresh()
_n = int(time.time())
sl_pc(pcx, _n)
check("finding 3: first call -> run record {at, sid, changed_sid} written", state_now().get("prompt_cache_run") == {"at": _n, "sid": None, "changed_sid": None}, state_now().get("prompt_cache_run"))
sl_pc(pcx, _n + 30)
check("finding 3: the same record 30 s later -> the last-call time is NOT rewritten", state_now()["prompt_cache_run"]["at"] == _n, state_now()["prompt_cache_run"])
sl_pc(pcx, _n + 59)
check("finding 3: 59 s later -> still not rewritten (boundary)", state_now()["prompt_cache_run"]["at"] == _n)
sl_pc(pcx, _n + 60)
check("finding 3: 60 s later -> the last-call time moves, the record's change time does not",
      state_now()["prompt_cache_run"]["at"] == _n + 60 and cache_rec()["seen_at"] == _n, (state_now()["prompt_cache_run"], cache_rec()))
sl_pc(dict(pcx, expires_at=int(_n) + 3300), _n + 70)
check("finding 3: a changed record -> both times move at once", state_now()["prompt_cache_run"]["at"] == _n + 70 and cache_rec()["seen_at"] == _n + 70)
sl_pc(dict(pcx, expires_at=int(_n) + 3300), _n + 10)
check("finding 3: the clock went back -> the last-call time follows (not frozen in the future)", state_now()["prompt_cache_run"]["at"] == _n + 10, state_now()["prompt_cache_run"])
_out = cli("--show")[1]
check("finding 3: --show names the last call, its accuracy and the record's change time on a separate line",
      "status line last call: " in _out and "accurate to 60 s" in _out and "record changed: " in _out, _out)
check("finding 3: the `cache:` line is still ONE line", len([x for x in _out.splitlines() if x.startswith("cache:")]) == 1)
d = state_now()
d.pop("prompt_cache_run")
tg._save(d)
check("finding 3: no run record (a state written before this version) -> --show says so, does not crash",
      "status line last call: not recorded yet" in cli("--show")[1])
d = state_now()
d["prompt_cache_run"] = {"at": "yesterday"}
tg._save(d)
sl_pc(dict(pcx, expires_at=int(_n) + 3300), _n + 200)
check("finding 3: a damaged run record is rewritten by the next call", state_now()["prompt_cache_run"]["at"] == _n + 200, state_now()["prompt_cache_run"])

# finding 5: which session made the call and which made the change (the state file is shared by all sessions)
fresh()
_n = int(time.time())
sl_sid(pcx, _n, "aaaaaaaa-1111-2222")
sl_sid(dict(pcx, expires_at=int(_n) + 3300), _n + 10, "bbbbbbbb-3333-4444")
check("finding 5: the session that CHANGED the record is stored (first 8 characters)",
      state_now()["prompt_cache_run"] == {"at": _n + 10, "sid": "bbbbbbbb", "changed_sid": "bbbbbbbb"}, state_now()["prompt_cache_run"])
sl_sid(dict(pcx, expires_at=int(_n) + 3300), _n + 80, "aaaaaaaa-1111-2222")
check("finding 5: the last CALL and the last CHANGE may belong to different sessions",
      state_now()["prompt_cache_run"] == {"at": _n + 80, "sid": "aaaaaaaa", "changed_sid": "bbbbbbbb"}, state_now()["prompt_cache_run"])
_out = cli("--show")[1]
check("finding 5: --show prints both session tags", "(session bbbbbbbb)" in _out and "session aaaaaaaa" in _out, _out)
check("finding 5: session_id is not part of the compared record (a new session alone does not rewrite it)", cache_rec()["seen_at"] == _n + 10, cache_rec())
check("finding 5: sid of odd shapes -> None / sanitised", (tg._sid8({"session_id": None}), tg._sid8({"session_id": 5}), tg._sid8({"session_id": ""}),
      tg._sid8({"session_id": "ab/../cdefgh"}), tg._sid8([])) == (None, None, None, "ab____cd", None))

# finding 6: the unit of expires_at (seconds is an assumption from the documentation; milliseconds must not pass for seconds)
check("finding 6: bounds (seconds, 2017-07-14 .. 2100-01-01)", (tg.CACHE_EPOCH_MIN, tg.CACHE_EPOCH_MAX) == (1_500_000_000, 4_102_444_800))
for _v, _ok in ((1_500_000_000, True), (1_499_999_999, False), (4_102_444_800, True), (4_102_444_801, False), (1_800_000_000.5, True),
                (1_791_600_000_000, False), ("2026-10-10T10:00:00Z", False), (True, False), (None, False), (float("nan"), False), ([1], False)):
    check("finding 6: _expiry(%r) -> %s" % (_v, "seconds" if _ok else "unknown with a reason"),
          (tg._expiry(_v)[0] is not None) == _ok and (_ok or bool(tg._expiry(_v)[1])), tg._expiry(_v))
check("finding 6: the reason for milliseconds says 'outside the range of epoch seconds'", "outside the range of epoch seconds" in tg._expiry(1_791_600_000_000)[1])
check("finding 6: the reason for a string names the type", "type str" in tg._expiry("2026-10-10")[1], tg._expiry("2026-10-10"))
_ms = 1_791_600_000_000  # 2026-10-10 in milliseconds
fresh()
with envset():
    check("finding 6: expires_at in MILLISECONDS (past) -> not cold, not blue", ctxline(PC(exp=_ms), now=1_800_000_000) == CTX)
    check("finding 6: warm=false still wins when expires_at is in milliseconds", ctxline(PC(warm=False, exp=_ms), now=1_800_000_000) == blue(CTX))
    check("finding 6: expires_at as an ISO string -> not judged, not blue", ctxline(PC(exp="2026-01-01T00:00:00Z"), now=1_800_000_000) == CTX)

# finding 4: cold now is yes / no / unknown (reason)
_N = 1_800_000_000
for label, pc, want in (("no prompt_cache", None, ("unknown", "no prompt_cache object")),
                        ("prompt_cache not an object", "cold", ("unknown", "no prompt_cache object")),
                        ("caching_observed false", {"caching_observed": False, "warm": True}, ("no", "caching not observed")),
                        ("caching_observed absent", {"warm": True, "expires_at": _N + 5}, ("unknown", "caching_observed: absent")),
                        ("caching_observed a string", {"caching_observed": "true"}, ("unknown", "caching_observed: type str")),
                        ("warm, expires_at absent", {"caching_observed": True, "warm": True}, ("unknown", "expires_at: type NoneType")),
                        ("warm, expires_at in ms", {"caching_observed": True, "warm": True, "expires_at": _ms}, ("unknown", "outside the range")),
                        ("warm, expires_at later", {"caching_observed": True, "warm": True, "expires_at": _N + 5}, ("no", None)),
                        ("warm, expires_at passed", {"caching_observed": True, "warm": True, "expires_at": _N - 5}, ("yes", None)),
                        ("warm=false, no time", {"caching_observed": True, "warm": False}, ("yes", None))):
    v, why = tg._cache_verdict(pc, _N)
    check("finding 4: verdict for %s -> %s" % (label, want[0]), v == want[0] and (want[1] is None or (why is not None and want[1] in why)), (v, why))
    check("finding 4: 'yes' agrees with _cache_cold for %s" % label, (v == "yes") == tg._cache_cold(pc, _N))
fresh()
_t = time.time()
sl_pc(dict(pcx, expires_at=_ms), _t)
_out = cli("--show")[1]
check("finding 4: --show for a millisecond expires_at prints 'cold now: unknown (...outside the range of epoch seconds)'",
      "cold now: unknown (expires_at " in _out and "outside the range of epoch seconds" in _out, _out)
sl_pc(dict(pcx, expires_at=int(_t) + 100, warm=True), _t + 1)
check("finding 4: a usable time -> plain 'cold now: no'", "cold now: no" in cli("--show")[1] and "cold now: no (" not in cli("--show")[1])

# finding 9: a huge or odd seen_at does not break --show
fresh()
sl_pc(pcx, time.time())
for _bad in (1e30, -1e30, 10 ** 20):
    d = state_now()
    d["prompt_cache_seen"]["seen_at"] = _bad
    tg._save(d)
    _c, _out, _e = cli("--show")
    check("finding 9: seen_at=%r -> --show still answers and says it is not a time" % (_bad,), _c == 0 and "is not a time" in _out, (_c, _out[-300:], _e))

# finding 11: a change of TYPE is a change (True == 1 in Python, not in the record)
fresh()
_n = int(time.time())
sl_pc(pcx, _n)
sl_pc(dict(pcx, warm=1), _n + 5)
check("finding 11: warm True -> 1 rewrites the record (type is evidence)", cache_rec()["warm"] == 1 and type(cache_rec()["warm"]) is int and cache_rec()["seen_at"] == _n + 5, cache_rec())
check("finding 11: _same tells True from 1 and 1 from 1.0, ignores key order",
      not tg._same({"a": True}, {"a": 1}) and not tg._same({"a": 1}, {"a": 1.0}) and tg._same({"a": 1, "b": 2}, {"b": 2, "a": 1}))
fresh()
with envset():
    tg._remember_render_cfg(tg.color_config(), _n)
_d = state_now()
_d["render_cfg"]["no_color"] = 0
tg._save(_d)
with envset():
    tg._remember_render_cfg(tg.color_config(), _n + 9)
check("finding 11: render_cfg no_color 0 (saved) vs False (now) -> rewritten", state_now()["render_cfg"]["no_color"] is False and state_now()["render_cfg_at"] == _n + 9, state_now().get("render_cfg"))

# finding 10: the status line writes its two records under ONE lock and one write; the parts are isolated
class _LockSpy(object):
    n = 0


_orig_enter = tg._Lock.__enter__


def _spy_enter(self):
    _LockSpy.n += 1
    return _orig_enter(self)


fresh()
_n = int(time.time())
tg.update(rl(five=(10, _n + 7200)), _n)
_LockSpy.n = 0
tg._Lock.__enter__ = _spy_enter
try:
    with envset(TRUEGAUGE_RED_5H="50"):
        tg._remember(dict(rl(five=(10, _n + 7200)), prompt_cache=pcx), tg.color_config(), _n + 1)
finally:
    tg._Lock.__enter__ = _orig_enter
check("finding 10: both records (settings AND cache), both changed -> ONE lock", _LockSpy.n == 1, _LockSpy.n)
check("finding 10: ... and both are in the state", state_now()["render_cfg"]["th"]["five_hour"] == 50 and cache_rec()["present"] is True, state_now())
_before = json.dumps(state_now(), sort_keys=True)
_LockSpy.n = 0
tg._Lock.__enter__ = _spy_enter
try:
    with envset(TRUEGAUGE_RED_5H="50"):
        tg._remember(dict(rl(five=(10, _n + 7200)), prompt_cache=pcx), tg.color_config(), _n + 2)
finally:
    tg._Lock.__enter__ = _orig_enter
check("finding 10: nothing changed (inside the 60 s step) -> one lock, nothing written", _LockSpy.n == 1 and json.dumps(state_now(), sort_keys=True) == _before, _LockSpy.n)
for _what, _kept in (("_cache_apply", "render_cfg"), ("_cfg_apply", "prompt_cache_seen")):
    fresh()
    orig = getattr(tg, _what)

    def _boom(*a, **k):
        raise RuntimeError("probe10")
    setattr(tg, _what, _boom)
    try:
        errs = count_errors(lambda: tg._remember(dict(rl(five=(10, _n + 7200)), prompt_cache=pcx), tg.color_config(), _n))
    finally:
        setattr(tg, _what, orig)
    check("finding 10: %s raises -> the OTHER record (%s) is still written" % (_what, _kept), _kept in state_now(), list(state_now()))
    check("finding 10: %s raises -> the error is recorded, not raised" % _what, any("probe10" in e for e in errs), errs)

# finding 10 (continued): each part alone is saved; an unchanged call writes nothing; statusline() uses ONE combined call
fresh()
_n = int(time.time())
with envset():
    tg._remember(dict(rl(five=(10, _n + 7200)), prompt_cache=pcx), tg.color_config(), _n)
_n2 = _n + 100
with envset():
    tg._remember(dict(rl(five=(10, _n + 7200)), prompt_cache=dict(pcx, expires_at=int(_n) + 3300)), tg.color_config(), _n2)
check("finding 10: only the CACHE changed -> it is saved", cache_rec()["expires_at"] == int(_n) + 3300 and cache_rec()["seen_at"] == _n2, cache_rec())
with envset(TRUEGAUGE_RED_5H="77"):
    tg._remember(dict(rl(five=(10, _n + 7200)), prompt_cache=dict(pcx, expires_at=int(_n) + 3300)), tg.color_config(), _n2 + 5)
check("finding 10: only the SETTINGS changed -> they are saved", state_now()["render_cfg"]["th"]["five_hour"] == 77 and cache_rec()["seen_at"] == _n2, state_now().get("render_cfg"))
_saves = []
_orig_save = tg._save
tg._save = lambda *a, **k: (_saves.append(1), _orig_save(*a, **k))[1]
try:
    with envset(TRUEGAUGE_RED_5H="77"):
        tg._remember(dict(rl(five=(10, _n + 7200)), prompt_cache=dict(pcx, expires_at=int(_n) + 3300)), tg.color_config(), _n2 + 10)
finally:
    tg._save = _orig_save
check("finding 10: nothing changed -> the state file is not written at all", _saves == [], _saves)
_calls = []
_o_rem, _o_cfg, _o_cache = tg._remember, tg._remember_render_cfg, tg._remember_cache
tg._remember = lambda *a, **k: (_calls.append("both"), _o_rem(*a, **k))[1]
tg._remember_render_cfg = lambda *a, **k: (_calls.append("cfg"), _o_cfg(*a, **k))[1]
tg._remember_cache = lambda *a, **k: (_calls.append("cache"), _o_cache(*a, **k))[1]
try:
    fresh()
    sl_pc(pcx, time.time())
finally:
    tg._remember, tg._remember_render_cfg, tg._remember_cache = _o_rem, _o_cfg, _o_cache
check("finding 10: one status-line call = ONE combined record call (not two locked passes)", _calls == ["both"], _calls)

# a damaged state file is not overwritten by the cache record, neither by the combined call nor by the cache-only one
for _what, _call in (("_remember (settings + cache)", lambda n: tg._remember(dict(rl(five=(10, n + 7200)), prompt_cache=pcx), tg.color_config(), n)),
                     ("_remember_cache", lambda n: tg._remember_cache(dict(rl(five=(10, n + 7200)), prompt_cache=pcx), n))):
    for _bad in ("{not json", "[1, 2]"):
        fresh()
        _n = int(time.time())
        with open(tg.STATE, "w", encoding="utf-8") as f:
            f.write(_bad)
        with envset():
            _call(_n)
        check("test_cache_record_damaged_state: %s leaves a damaged state (%r) byte for byte as it was" % (_what, _bad),
              open(tg.STATE, encoding="utf-8").read() == _bad, open(tg.STATE, encoding="utf-8").read())

# finding 2 / item F: a refused TRUEGAUGE_MARK reaches the error record, once per change
fresh()
_n = time.time()
errs = count_errors(lambda: sl(10, {"TRUEGAUGE_MARK": "brackets"}, _n))
check("finding 2: a refused TRUEGAUGE_MARK goes into the error record naming the variable and the value",
      any("TRUEGAUGE_MARK" in e and "brackets" in e for e in errs), errs)
check("finding 2: ... and it is in the state file (last_extra_error)", "TRUEGAUGE_MARK" in str((state_now().get("last_extra_error") or {}).get("msg")), state_now().get("last_extra_error"))
errs = count_errors(lambda: sl(10, {"TRUEGAUGE_MARK": "brackets"}, _n + 1))
check("finding 2: the same refusal again -> not recorded again", not any("TRUEGAUGE_MARK" in e for e in errs), errs)
check("finding 2: --show names the refusal in the status line's settings", "REFUSED (default used): TRUEGAUGE_MARK=" in cli("--show")[1])

# findings 1, 2, 7, 8, 9 (README text): the claims a reader is told must be there, in both languages
for _lang, _fn in (("en", "README.md"), ("ru", "README.ru.md")):
    _txt = open(os.path.join(os.path.dirname(HERE), _fn), encoding="utf-8").read()
    _i = _txt.find("## Cold cache" if _lang == "en" else "## Остывший кэш")
    _cold = _txt[_i:] if _i >= 0 else ""
    _j = _txt.find("## Mark for colour-blind" if _lang == "en" else "## Метка для тех")
    _mark = _txt[_j:_txt.find("\n## ", _j + 5)] if _j >= 0 else ""
    check("finding 1 / item A (%s): the cold-cache section says the arrival of prompt_cache is NOT measured, and points at present=True" % _lang,
          "2.1.293" in _cold[:1500] and "present=True" in _cold[:1500], _cold[:400])
    check("finding 2 (%s): the Mark section sends a reader whose mark is not visible to --show and names the REFUSED part" % _lang,
          "--show" in _mark and "status line cold colour" in _mark and "REFUSED (default used): TRUEGAUGE_MARK" in _mark, _mark[-300:])
    check("finding 7 (%s): the agent step checks success by the `status line ... mark:` line, and gives PowerShell" % _lang,
          "status line cold colour: ...; mark:" in _txt and "$env:TRUEGAUGE_MARK" in _txt and "AFTER" in _txt.replace("ПОСЛЕ", "AFTER"))
    check("finding 3-5 (%s): README tells how to read `status line last call` and the session tags" % _lang, "status line last call" in _cold and "accurate to 60 s" in _cold)
    check("finding 6 / item C (%s): README says expires_at is epoch SECONDS and the unit is an assumption" % _lang, "2100-01-01" in _cold)
    check("finding 9 (%s): README step 3 sends the agent to project/local settings too" % _lang, "settings.local.json" in _txt)
    check("finding 8 (%s): the by-hand fragment has no second `env` block, and says to add keys to an existing one" % _lang,
          '"env": { "TRUEGAUGE_LANG"' not in _txt.split("## Settings" if _lang == "en" else "## Настройки")[0], _lang)
    check("finding 8 (%s): uninstall of a hand-written line needs --force (stated)" % _lang, "--force" in _txt)
    check("finding 9 (%s): the 'repaint at expires_at' claim has its source" % _lang, "When the status line updates" in _txt, _lang)

# ---- T4: the context reading survives /compact and the time before the first reply
T4_SID, T4_OTHER = "abcd1234-0000-4000-8000-000000000001", "wxyz9876-0000-4000-8000-000000000002"
T4_STALE = "≤T549K ≤56%"  # what the line draws for a saved 548 200 tokens / 55.2%: an upper bound rounds UP


def t4_call(tok, pct, now, sid=T4_SID, lang="en", **extra):
    cw = {"used_percentage": pct, "total_input_tokens": tok}
    pl = rl(five=(10, int(now) + 7200), context_window=cw, **extra)
    if sid is not ABSENT:
        pl["session_id"] = sid
    old_lang = os.environ.get("TRUEGAUGE_LANG")  # envset restores the colour variables only
    os.environ["TRUEGAUGE_LANG"] = lang
    try:
        with envset():
            return tg.statusline(pl, now)
    finally:
        if old_lang is None:
            os.environ.pop("TRUEGAUGE_LANG", None)
        else:
            os.environ["TRUEGAUGE_LANG"] = old_lang


def test_state_survives_compaction_same_session():
    fresh()
    n = int(time.time())
    out = t4_call(548200, 55.2, n)
    check("T4 live reading: the group is drawn as before, without a bound sign", "T548K 55%" in out and tg.LE not in out, out)
    rec = state_now().get("context_seen")
    check("T4 live reading: tokens, percent, session tag (8 characters) and time are saved",
          rec == {"tokens": 548200, "pct": 55.2, "sid": "abcd1234", "at": n}, rec)
    out = t4_call(0, 0, n + 30)
    check("T4 compaction (0 tokens), same session: the old numbers with <= before each", T4_STALE in out, out)
    check("T4 compaction: the sign is U+2264 and 0 is never shown", tg.LE == chr(0x2264) and "T0" not in out and " 0%" not in out, out)
    check("T4 compaction: the saved record got stale_since once", state_now()["context_seen"].get("stale_since") == n + 30, state_now().get("context_seen"))
    out = t4_call(0, 0, n + 90)
    check("T4 a second call without a reading: the same group, stale_since not moved",
          T4_STALE in out and state_now()["context_seen"]["stale_since"] == n + 30, out)
    out = t4_call(0, None, n + 91)
    check("T4 before the first reply, percent not sent either: still the saved group", T4_STALE in out, out)
    out = t4_call(None, None, n + 92)
    check("T4 tokens absent (not 0): no group, the old reading is not drawn", "T548K" not in out and tg.LE not in out, out)
    out = t4_call(0, 0, n + 93, lang="ru")
    check("T4 Russian letters on the stale group", "≤Т549К ≤56%" in out, out)


def test_state_survives_compaction_other_session():
    fresh()
    n = int(time.time())
    t4_call(548200, 55.2, n)
    out = t4_call(0, 0, n + 30, sid=T4_OTHER)
    check("T4 other session: no context group, no bound sign", "T548K" not in out and tg.LE not in out and "55%" not in out, out)
    check("T4 other session: its call does not mark the saved reading stale", state_now()["context_seen"].get("stale_since") is None, state_now().get("context_seen"))
    out = t4_call(0, 0, n + 31, sid=ABSENT)
    check("T4 no session tag in the payload: no group", "T548K" not in out and tg.LE not in out, out)
    fresh()
    out = t4_call(0, 0, time.time())
    check("T4 no saved reading at all: no group", "T548K" not in out and tg.LE not in out and " 0%" not in out, out)
    t4_call(548200, 55.2, n, sid=ABSENT)
    check("T4 a live reading without a session tag is not kept (it could never be matched)", "context_seen" not in state_now(), state_now().get("context_seen"))
    t4_call(548200, None, n)
    out = t4_call(0, 0, n + 5)
    check("T4 a reading saved without a percent: the group has the tokens only", out.endswith(tg.LE + "T549K") and "56%" not in out, out)


def test_state_survives_compaction_damaged_record():
    for what, bad in (("a string", "T548K"), ("a list", [1]), ("tokens a text", {"tokens": "548200", "pct": 55, "sid": "abcd1234", "at": 5}),
                      ("tokens 0", {"tokens": 0, "pct": 55, "sid": "abcd1234", "at": 5}),
                      ("tokens true", {"tokens": True, "pct": 55, "sid": "abcd1234", "at": 5}),
                      ("tokens negative", {"tokens": -5, "pct": 55, "sid": "abcd1234", "at": 5}),
                      ("percent a text", {"tokens": 548200, "pct": "55", "sid": "abcd1234", "at": 5}),
                      ("session not a text", {"tokens": 548200, "pct": 55, "sid": 5, "at": 5}),
                      ("session empty", {"tokens": 548200, "pct": 55, "sid": "", "at": 5}),
                      ("no time", {"tokens": 548200, "pct": 55, "sid": "abcd1234"}),
                      ("no percent key", {"tokens": 548200, "sid": "abcd1234", "at": 5}),
                      ("stale_since a text", {"tokens": 548200, "pct": 55, "sid": "abcd1234", "at": 5, "stale_since": "x"})):
        fresh()
        n = time.time()
        d = state_now()
        d["context_seen"] = bad
        tg._save(d)
        errs = count_errors(lambda: t4_call(0, 0, n))
        out = t4_call(0, 0, n + 1)
        check("T4 damaged record (%s): no group and no 0, the line stays" % what, "T548K" not in out and tg.LE not in out and " 0%" not in out and "10%" in out, out)
        check("T4 damaged record (%s): one error line naming the context record" % what, any("context record" in e for e in errs), errs)
        errs2 = count_errors(lambda: t4_call(0, 0, n + 2))
        check("T4 damaged record (%s): the same error is not written again on the next call" % what, not any("context record" in e for e in errs2), errs2)
        out = t4_call(548200, 55.2, n + 3)
        check("T4 damaged record (%s): the next real reading rewrites it" % what, state_now()["context_seen"]["tokens"] == 548200 and "T548K 55%" in out, state_now().get("context_seen"))


def test_state_survives_compaction_not_rewritten():
    fresh()
    n = int(time.time())
    cw = {"used_percentage": 55.2, "total_input_tokens": 548200}
    pl = rl(five=(10, n + 7200), context_window=cw, session_id=T4_SID)
    with envset():
        tg._remember(pl, tg.color_config(), n)
    saves = []
    orig = tg._save
    tg._save = lambda *a, **k: (saves.append(1), orig(*a, **k))[1]
    try:
        with envset():
            tg._remember(pl, tg.color_config(), n + 5)
    finally:
        tg._save = orig
    check("T4 an unchanged reading: the state file is not written at all", saves == [], saves)
    t4_call(548200, 55.2, n + 50)
    check("T4 an unchanged reading through the status line: `at` stays the time of the change", state_now()["context_seen"]["at"] == n, state_now().get("context_seen"))
    t4_call(549000, 55.2, n + 100)
    check("T4 a changed token count: rewritten, `at` is the new time", state_now()["context_seen"]["at"] == n + 100 and state_now()["context_seen"]["tokens"] == 549000, state_now().get("context_seen"))
    t4_call(549000, 56.0, n + 200)
    check("T4 a changed percent alone: rewritten", state_now()["context_seen"]["at"] == n + 200 and state_now()["context_seen"]["pct"] == 56.0, state_now().get("context_seen"))
    t4_call(549000, 56.0, n + 300, sid=T4_OTHER)
    check("T4 the same numbers from another session: rewritten with its tag", state_now()["context_seen"]["sid"] == "wxyz9876" and state_now()["context_seen"]["at"] == n + 300, state_now().get("context_seen"))


def test_state_survives_compaction_show_and_cold():
    fresh()
    n = int(time.time())
    check("T4 --show with no reading: `context: no reading`", "context: no reading" in cli("--show")[1], cli("--show")[1])
    t4_call(548200, 55.2, n)
    check("T4 --show with a live reading: `context: live`", "context: live" in cli("--show")[1], cli("--show")[1])
    t4_call(0, 0, n + 40)
    shown = cli("--show")[1]
    check("T4 --show when the line draws the old reading: numbers, session, both times",
          "context: last reading T548K 55%% (session abcd1234) at %s; stale since %s" % (tg._fmt_time(n), tg._fmt_time(n + 40)) in shown, shown)
    t4_call(548200, 55.2, n + 80)
    check("T4 --show after a live reading again (the SAME numbers): back to `context: live`", "context: live" in cli("--show")[1] and "stale since" not in cli("--show")[1], cli("--show")[1])
    d = state_now()
    d["context_seen"] = {"tokens": "x"}
    tg._save(d)
    shown = cli("--show")[1]
    check("T4 --show with a damaged record: `context: no reading` and the command does not fail", "context: no reading" in shown and cli("--show")[0] == 0, shown)
    # cold cache: the group is painted the same way as a live one
    fresh()
    t4_call(548200, 55.2, n)
    out = t4_call(0, 0, n + 10, prompt_cache={"caching_observed": True, "warm": False})
    check("T4 the stale group is painted blue when the cache is cold", blue(T4_STALE) in out, out)
    out = t4_call(0, 0, n + 11, prompt_cache={"caching_observed": True, "warm": True, "expires_at": n + 3000})
    check("T4 the stale group is plain when the cache is warm", blue(T4_STALE) not in out and T4_STALE in out, out)
    with envset(TRUEGAUGE_MARK="braces"):
        pl = rl(five=(10, int(n) + 7200), context_window={"used_percentage": 0, "total_input_tokens": 0}, session_id=T4_SID,
                prompt_cache={"caching_observed": True, "warm": False})
        out = tg.statusline(pl, n + 12)
    check("T4 the mark (braces) goes around the cold stale group like around a cold live one", "{" + T4_STALE + "}" in SGR_RX.sub("", out), out)
    # render() on its own: a bad ctx_last never draws, and a live reading wins over a saved one
    good = {"tokens": 548200, "pct": 55.2, "sid": "abcd1234", "at": 5}
    check("T4 render: a saved reading is not used when there IS a reading now", tg.render({}, None, 55.2, "en", 548200, False, good) == "T548K 55%", tg.render({}, None, 55.2, "en", 548200, False, good))
    check("T4 render: no reading now + a saved one -> the bound group", tg.render({}, None, 0, "en", 0, False, good) == T4_STALE, tg.render({}, None, 0, "en", 0, False, good))
    check("T4 render: a saved reading with 0 tokens is not drawn", tg.render({}, None, 0, "en", 0, False, dict(good, tokens=0)) == "", tg.render({}, None, 0, "en", 0, False, dict(good, tokens=0)))
    # the bound sign is honest: the old numbers are rounded UP (never below the saved value); the live reading is unchanged
    for _tok, _want in ((1, "≤T1"), (999, "≤T999"), (1000, "≤T1K"), (1001, "≤T2K"), (548000, "≤T548K"), (548001, "≤T549K"), (548200, "≤T549K"),
                        (548999, "≤T549K"), (549000, "≤T549K"), (998999, "≤T999K"), (999000, "≤T999K"), (999001, "≤T1.0M"), (999999, "≤T1.0M"),
                        (1000000, "≤T1.0M"), (1000001, "≤T1.1M"), (2600000, "≤T2.6M"), (2600001, "≤T2.7M"), (2650000, "≤T2.7M")):
        _got = tg.render({}, None, 0, "en", 0, False, dict(good, tokens=_tok, pct=None))
        check("T6 bound sign, tokens %d -> %s" % (_tok, _want), _got == _want, _got)
    for _pct, _want in ((0.1, "≤1%"), (0.9, "≤1%"), (1.0, "≤1%"), (1.1, "≤2%"), (54.0, "≤54%"), (54.2, "≤55%"), (54.99, "≤55%"),
                        (55.0, "≤55%"), (55.01, "≤56%"), (99.5, "≤100%"), (100, "≤100%")):
        _got = tg.render({}, None, 0, "en", 0, False, dict(good, pct=_pct)).split(" ")[1]
        check("T6 bound sign, percent %s -> %s" % (_pct, _want), _got == _want, _got)
    check("T6 bound sign, Russian: the tokens go up too", tg.render({}, None, 0, "ru", 0, False, dict(good, tokens=2600001, pct=None)) == "≤Т2.7М")
    for _tok, _want in ((548000, "T548K"), (548200, "T548K"), (548499, "T548K"), (548500, "T549K"), (999499, "T999K"), (999500, "T1.0M"), (2650000, "T2.7M")):
        _got = tg.render({}, None, 0, "en", _tok, False, good)
        check("T6 live reading unchanged, tokens %d -> %s" % (_tok, _want), _got.split(" ")[0] == _want, _got)
    for _pct, _want in ((54.2, "54%"), (54.5, "55%"), (0.4, "0%")):
        _got = tg.render({}, None, _pct, "en", 548200, False, good)
        check("T6 live reading unchanged, percent %s -> %s" % (_pct, _want), _got.split(" ")[1] == _want, _got)


test_state_survives_compaction_same_session()
test_state_survives_compaction_other_session()
test_state_survives_compaction_damaged_record()
test_state_survives_compaction_not_rewritten()
test_state_survives_compaction_show_and_cold()

# ---- equivalence with v0.2.0 (kept byte for byte as tests/ref_v0_2_0.py)
_spec2 = importlib.util.spec_from_file_location("ref_v0_2_0", os.path.join(HERE, "ref_v0_2_0.py"))
ref2 = importlib.util.module_from_spec(_spec2)
_spec2.loader.exec_module(ref2)
check("reference module is v0.2.0", ref2.__version__ == "0.2.0", ref2.__version__)
EXTRA = [(win("fresh", 98.2, 7200), win("fresh", 99.9, 3 * 86400), win("fresh", 4, None), {"new": 2600000, "complete": True}, (55.2, 548200), "en"),
         (win("stale_reading", 97.6, 7200), None, None, None, (0, 0), "ru"),
         (None, win("fresh", 54, 5 * 86400), win("stale_reading", 99, 86400), {"new": 2650000, "complete": False}, (None, 548200), "en"),
         (win("fresh", 38.5, 14400), win("fresh", 99, 3600), {"status": "absent"}, None, (55.2, None), "ru")]
for e5, e7, efb, etk, ectx, elang in EXTRA:
    states.append(({n: v for n, v in (("five_hour", e5), ("seven_day", e7), ("fable", efb)) if v is not None}, etk, ectx, elang))
check("equivalence set (0.3 against 0.2) has at least 30 states", len(states) >= 30, len(states))


def both2(s, cold):
    return (unfresh(s, tg.render(s[0], s[1], s[2][0], s[3], s[2][1], cold=cold)), ref2.render(s[0], s[1], s[2][0], s[3], s[2][1]))


check("equivalence (0.4): the fresh `5h` is really there on every state with an expired 5h, and nowhere else",
      all((FRESH_RX.match(tg.render(s[0], s[1], s[2][0], s[3], s[2][1])) is not None) == _expired5(s) for s in states)
      and sum(_expired5(s) for s in states) >= 20, sum(_expired5(s) for s in states))


for label, cold in (("a warm cache / no prompt_cache", False),):
    with envset(NO_COLOR="1"):
        diff = [s for s in states if both2(s, cold)[0] != both2(s, cold)[1]]
    check("equivalence 0.3 == 0.2 (%s), NO_COLOR=1: byte for byte on all %d states" % (label, len(states)), diff == [], diff[:1])
    with envset():
        diff = [s for s in states if both2(s, cold)[0] != both2(s, cold)[1]]
    check("equivalence 0.3 == 0.2 (%s), colours on: byte for byte on all %d states" % (label, len(states)), diff == [], diff[:1])
with envset(NO_COLOR="1"):
    diff = [s for s in states if both2(s, True)[0] != both2(s, True)[1]]
check("equivalence 0.3 == 0.2 with a COLD cache, NO_COLOR=1: byte for byte on all %d states" % len(states), diff == [], diff[:1])
with envset():
    outs2 = [both2(s, True) for s in states]
check("equivalence 0.3 == 0.2 with a COLD cache, SGR stripped: identical on all %d states" % len(states),
      all(SGR_RX.sub("", a) == SGR_RX.sub("", b) for a, b in outs2), [o for o in outs2 if SGR_RX.sub("", o[0]) != SGR_RX.sub("", o[1])][:1])
check("equivalence with a cold cache is not vacuous: 0.3 paints a context group on many states",
      sum(AMB in a for a, b in outs2) >= 20 and not any(AMB in b for a, b in outs2), sum(AMB in a for a, b in outs2))
with envset(TRUEGAUGE_MARK="off", TRUEGAUGE_COLD_COLOR="off"):
    diff = [s for s in states if both2(s, True)[0] != both2(s, True)[1]]
check("equivalence: cold colour off + mark off -> a cold cache changes nothing, byte for byte", diff == [], diff[:1])

# end to end: the real scripts, with and without prompt_cache
PAYLOADS3 = [dict(PAYLOADS[0], context_window={"used_percentage": 55.2, "total_input_tokens": 548200}),
             dict(PAYLOADS[1], context_window={"used_percentage": 12, "total_input_tokens": 90000}),
             PAYLOADS[2], PAYLOADS[3]]
okb = okc = oks = True
s_ref = os.path.join(HERE, "ref_v0_2_0.py")
s_new = os.path.join(os.path.dirname(HERE), "truegauge.py")
for i, pl in enumerate(PAYLOADS3):
    c1, o1 = run_script(s_ref, pl, {})
    c2, o2 = run_script(s_new, pl, {})
    okb = okb and c1 == c2 == 0 and o1 == o2
    if i > 1:  # the NO_COLOR and prompt_cache variants run on the two payloads that carry numbers (a script start is slow)
        continue
    c3, o3 = run_script(s_ref, pl, {"NO_COLOR": "1"})
    c4, o4 = run_script(s_new, pl, {"NO_COLOR": "1"})
    okb = okb and c3 == c4 == 0 and o3 == o4
    for pcw in ({"caching_observed": True, "warm": False, "ttl": "1h", "expires_at": None},
                {"caching_observed": True, "warm": True, "ttl": "5m", "expires_at": _now - 5}):
        plc = dict(pl, prompt_cache=pcw)
        c5, o5 = run_script(s_new, plc, {})
        c6, o6 = run_script(s_new, plc, {"NO_COLOR": "1"})
        c7, o7 = run_script(s_ref, plc, {})
        okc = okc and c5 == c6 == c7 == 0 and SGR_RX.sub("", o5.decode("utf-8")) == SGR_RX.sub("", o7.decode("utf-8"))
        oks = oks and o6 == o3
check("end to end (real scripts, %d payloads): without prompt_cache 0.3 == 0.2 byte for byte, colours on and NO_COLOR=1" % len(PAYLOADS3), okb)
check("end to end: with prompt_cache (cold and warm) the stripped output equals 0.2's", okc)
check("end to end: with prompt_cache and NO_COLOR=1 the output equals 0.2's byte for byte", oks)
_c, _o = run_script(os.path.join(os.path.dirname(HERE), "truegauge.py"),
                    dict(PAYLOADS3[0], prompt_cache={"caching_observed": True, "warm": False}), {})
check("end to end: a cold payload really prints an blue context group", (AMB + CTX + RS) in _o.decode("utf-8"), _o)
_c, _o = run_script(os.path.join(os.path.dirname(HERE), "truegauge.py"),
                    dict(PAYLOADS3[0], prompt_cache={"caching_observed": True, "warm": False}), {"TRUEGAUGE_MARK": "all"})
check("end to end: TRUEGAUGE_MARK=all from the environment marks the red windows and the cold context group",
      _o.decode("utf-8").count(UL) == 3 and _o.decode("utf-8").count("{") == 3 and _o.decode("utf-8").count("}") == 3, _o)

# ---- 0.4: the agent warning (--hook) -------------------------------------------------------------------------
import subprocess  # noqa: E402

R5, R7 = NOW + 4800 + 30, NOW + 3 * 86400 + 100  # 5h window: 1h 20m left -> "1h"; week: 3d 0h -> "3d"


def win(pct, rst, status="fresh", age=10):
    """A window as read_state() returns it (the only fields the warning reads)."""
    v = {"status": status, "used_percentage": pct, "resets_at": rst, "resets_in_s": rst - NOW, "reading_age_s": age}
    if status == "expired":
        v = {"status": "expired", "resets_at": rst, "resets_in_s": rst - NOW, "last_seen_used_percentage": pct,
             "remaining_percentage": None, "reading_age_s": age}
    return v


TH = (80, 90, 95)
for pct, lvl in ((0, 0), (79.99, 0), (80.0, 80), (80.01, 80), (89.99, 80), (90.0, 90), (94.99, 90), (95.0, 95), (100.0, 95)):
    check("agent level of %s%% is %d (boundary included)" % (pct, lvl), tg.agent_level(pct, TH) == lvl, tg.agent_level(pct, TH))
for pct, lvl in ((79.99, 0), (80.0, 80), (80.01, 80), (89.99, 80), (90.0, 90), (95.0, 95), (100.0, 95)):
    t, new = tg.agent_decide({"five_hour": win(pct, R5)}, TH, {})
    check("decide: %s%% from nothing -> %s" % (pct, "silence" if lvl == 0 else "one message, level %d recorded" % lvl),
          (t is None and new == {}) if lvl == 0 else (t is not None and new == {"five_hour": {"resets_at": R5, "level": lvl}}), (t, new))

t, new = tg.agent_decide({"five_hour": win(91, R5), "seven_day": win(64, R7)}, TH, {})
check("decide: text for both windows is a statement with the number and the time left",
      t == "Claude plan usage (truegauge): 5-hour window 91% used, resets in 1h; weekly window 64% used, resets in 3d.", t)
check("decide: both windows recorded when it speaks (the weekly one at level 0)",
      new == {"five_hour": {"resets_at": R5, "level": 90}, "seven_day": {"resets_at": R7, "level": 0}}, new)
t, new2 = tg.agent_decide({"five_hour": win(91, R5), "seven_day": win(64, R7)}, TH, new)
check("decide: the same level again -> silence, record unchanged", t is None and new2 == new, (t, new2))
t, new3 = tg.agent_decide({"five_hour": win(93, R5), "seven_day": win(64, R7)}, TH, new)
check("decide: growth inside the same level (91 -> 93) -> silence", t is None, t)
t, new4 = tg.agent_decide({"five_hour": win(95, R5), "seven_day": win(64, R7)}, TH, new)
check("decide: next level (90 -> 95) -> a message", t is not None and new4["five_hour"]["level"] == 95, (t, new4))
t, a = tg.agent_decide({"five_hour": win(80, R5)}, TH, {})
t2, b = tg.agent_decide({"five_hour": win(91, R5)}, TH, a)
check("decide: 80 then 91 in one window: second message is level 90", t is not None and t2 is not None and b["five_hour"]["level"] == 90, (t, t2))
t, c = tg.agent_decide({"five_hour": win(91, R5)}, TH, {})
t2, _ = tg.agent_decide({"five_hour": win(91, R5)}, TH, c)
check("decide: a jump 0 -> 91 is ONE message of level 90 (not 80 and 90)", t is not None and c["five_hour"]["level"] == 90 and t2 is None, (t, c, t2))
t, _ = tg.agent_decide({"five_hour": win(81, R5 + 5 * 3600)}, TH, c)
check("decide: a new window (other resets_at) starts again from 0 -> level 80 speaks", t is not None, t)
t, _ = tg.agent_decide({"five_hour": win(60, R5 + 5 * 3600)}, TH, c)
check("decide: a new window below the first threshold -> silence", t is None, t)
t, _ = tg.agent_decide({"five_hour": win(97, R5, status="expired")}, TH, {})
check("decide: an expired window is silent", t is None, t)
t, _ = tg.agent_decide({"five_hour": win(97, R5, status="expired"), "seven_day": win(82, R7)}, TH, {})
check("decide: expired 5h + live week -> a message about the week only",
      t == "Claude plan usage (truegauge): weekly window 82% used, resets in 3d.", t)
t, _ = tg.agent_decide({"five_hour": win(91.7, R5, status="stale_reading", age=14 * 60), "seven_day": win(64, R7)}, TH, {})
check("decide: a stale reading is `at least` (rounded DOWN) with its age",
      t == "Claude plan usage (truegauge): 5-hour window at least 91% used (reading 14 min old), resets in 1h; weekly window 64% used, resets in 3d.", t)
t, _ = tg.agent_decide({"five_hour": win(91, R5, status="stale_reading", age=None)}, TH, {})
check("decide: a stale reading with an unknown age says so", t is not None and "reading age unknown" in t, t)
t, _ = tg.agent_decide({"five_hour": win(91, R5)}, None, {})
check("decide: off (thresholds None) -> silence", t is None, t)
t, _ = tg.agent_decide({}, TH, {})
check("decide: no windows -> silence", t is None, t)
t, _ = tg.agent_decide({"five_hour": win(91, R5), "seven_day": win(85, NOW + 5 * 3600)}, TH, {})
check("decide: the week in its last day is still written in days (0d), never in hours", t is not None and "weekly window 85% used, resets in 0d" in t, t)
t, _ = tg.agent_decide({"five_hour": win(91.0, R5, age=5), "seven_day": win(64, R7)}, TH, {}, "ru")
check("decide: Russian text", t == "Лимит плана Claude (truegauge): 5-часовое окно 91 % использовано, сброс через 1ч; недельное окно 64 % использовано, сброс через 3д.", t)
t, _ = tg.agent_decide({"five_hour": win(91.7, R5, status="stale_reading", age=14 * 60)}, TH, {}, "ru")
check("decide: Russian stale text", t is not None and "не менее 91 % использовано (показанию 14 мин)" in t, t)
for bad in (None, [], "x", {"five_hour": "x"}, {"five_hour": {"resets_at": R5}}, {"five_hour": {"resets_at": R5, "level": True}},
            {"five_hour": {"resets_at": "x", "level": 90}}, {"five_hour": {"resets_at": R5, "level": "90"}}):
    try:
        t, _ = tg.agent_decide({"five_hour": win(91, R5)}, TH, bad)
        ok = t is not None
    except Exception as e:
        ok = False
        t = repr(e)
    check("decide: a damaged record %r is treated as nothing announced" % (bad,), ok, t)
check("the text has no imperative (statement only)", not any(w in t.lower() for w in ("please", "should", "stop", "must")), t)

# thresholds
for raw, want, bad in ((None, (80, 90, 95), False), ("", (80, 90, 95), False), ("  ", (80, 90, 95), False), ("off", None, False),
                       ("OFF", None, False), ("0", None, False), ("70", (70,), False), ("95, 80,90", (80, 90, 95), False),
                       ("80,80", (80,), False), ("100", (100,), False), ("101", (80, 90, 95), True), ("-5", (80, 90, 95), True),
                       ("80,abc", (80, 90, 95), True), ("80,,90", (80, 90, 95), True), ("80.5", (80, 90, 95), True),
                       ("off,80", (80, 90, 95), True), ("1e2", (80, 90, 95), True)):
    got, why = tg.agent_thresholds(raw)
    check("TRUEGAUGE_AGENT_AT=%r -> %r%s" % (raw, want, ", refusal named" if bad else ""), got == want and bool(why) == bad, (got, why))

# the hook function: state -> JSON / silence
AGENT_ENV = ("TRUEGAUGE_AGENT_AT", "TRUEGAUGE_LANG")


@contextlib.contextmanager
def agent_env(**kw):
    old = {k: os.environ.get(k) for k in AGENT_ENV}
    for k in AGENT_ENV:
        os.environ.pop(k, None)
    os.environ.update(kw)
    try:
        yield
    finally:
        for k in AGENT_ENV:
            os.environ.pop(k, None)
        os.environ.update({k: v for k, v in old.items() if v is not None})


def hook_in(event="PostToolUse", now=NOW + 5, raw=None):
    return tg.hook_output(json.dumps({"hook_event_name": event}) if raw is None else raw, now=now)


def seed(five=None, seven=None):
    d = fresh()
    tg.update(rl(five=five, seven=seven), NOW)
    return d


with agent_env():
    seed(five=(91.0, R5), seven=(64.0, R7))
    o = hook_in()
    j = json.loads(o)
    check("hook: output is exactly {hookSpecificOutput: {hookEventName, additionalContext}}",
          list(j) == ["hookSpecificOutput"] and sorted(j["hookSpecificOutput"]) == ["additionalContext", "hookEventName"], j)
    check("hook: hookEventName is the incoming one (PostToolUse)", j["hookSpecificOutput"]["hookEventName"] == "PostToolUse", j)
    check("hook: the text carries both windows",
          j["hookSpecificOutput"]["additionalContext"].startswith("Claude plan usage (truegauge): 5-hour window 91% used")
          and "weekly window 64% used" in j["hookSpecificOutput"]["additionalContext"], j)
    rec = json.loads(get(os.path.join(tg.HOME, "agent_warned.json")))
    check("hook: agent_warned.json = {sessions: {session_id: {window: {resets_at, level}, seen}}}",
          sorted(rec) == ["hook", "sessions"] and rec["sessions"][""]["five_hour"] == {"resets_at": R5, "level": 90}
          and rec["hook"] == {"outcome": "spoke", "since": NOW + 5}
          and rec["sessions"][""]["seen"] == NOW + 5, rec)
    check("hook: second call, same state -> silence", hook_in() is None)
    check("hook: other event of the pair, same state -> silence (one announcement for all events)", hook_in("UserPromptSubmit") is None)
    tg.update(rl(five=(96.0, R5), seven=(64.0, R7)), NOW + 10)
    j = json.loads(hook_in("UserPromptSubmit", now=NOW + 15))
    check("hook: next level -> a message with the UserPromptSubmit name", j["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
          and "96% used" in j["hookSpecificOutput"]["additionalContext"], j)
    check("hook: Stop and other events are silent even above the threshold", hook_in("Stop") is None and hook_in("SessionStart") is None)
    check("hook: no hook_event_name -> silence", hook_in(raw="{}") is None)
    check("hook: JSON that is not an object -> silence", hook_in(raw="[1]") is None and hook_in(raw="null") is None)

    seed(five=(79.99, R5), seven=(10.0, R7))
    check("hook: 79.99 -> silence; the record names why and holds no session",
          hook_in() is None and json.loads(get(os.path.join(tg.HOME, "agent_warned.json")))
          == {"sessions": {}, "hook": {"outcome": "silent: below the thresholds, or already told", "since": NOW + 5}})
    seed(five=(80.0, R5), seven=(10.0, R7))
    check("hook: 80.0 -> a message", hook_in() is not None)
    seed()
    check("hook: no state file at all -> silence", hook_in() is None)
    seed(five=(97.0, NOW - 100), seven=(10.0, R7))
    check("hook: an expired window -> silence", hook_in() is None)

    d = seed(five=(91.0, R5))
    put(os.path.join(d, "agent_warned.json"), "{ not json")
    check("hook: a damaged agent_warned.json does not crash; speaks and rewrites it",
          hook_in() is not None and json.loads(get(os.path.join(d, "agent_warned.json")))["sessions"][""]["five_hour"]["level"] == 90)
    put(os.path.join(d, "agent_warned.json"), "[1,2]")
    check("hook: agent_warned.json that is not an object = nothing announced", hook_in() is not None)

    # once per threshold PER SESSION: a background `claude -p` cannot use up the main session's warning
    def hook_sid(sid, event="PostToolUse", now=NOW + 5):
        return hook_in(raw=json.dumps({"hook_event_name": event, "session_id": sid}), now=now)

    def book(d):
        return json.loads(get(os.path.join(d, "agent_warned.json")))

    d = seed(five=(91.0, R5), seven=(64.0, R7))
    check("hook/session: session A is told", hook_sid("aaaa-1") is not None)
    check("hook/session: session A again -> silence", hook_sid("aaaa-1") is None)
    check("hook/session: session A, the other event -> silence", hook_sid("aaaa-1", "UserPromptSubmit") is None)
    check("hook/session: session B is told too (A's warning is not B's)", hook_sid("bbbb-2") is not None)
    check("hook/session: no session_id -> told once (shared record)", hook_in() is not None and hook_in() is None)
    check("hook/session: a non-string session_id is the shared record", hook_sid(123) is None and hook_sid("") is None)
    check("hook/session: the book holds A, B and the shared record", sorted(book(d)["sessions"]) == ["", "aaaa-1", "bbbb-2"], book(d))
    tg.update(rl(five=(96.0, R5), seven=(64.0, R7)), NOW + 10)
    check("hook/session: the next level reaches A and B, each once",
          hook_sid("aaaa-1", now=NOW + 15) is not None and hook_sid("bbbb-2", now=NOW + 15) is not None
          and hook_sid("aaaa-1", now=NOW + 16) is None and hook_sid("bbbb-2", now=NOW + 16) is None)

    d = seed(five=(91.0, R5))
    put(os.path.join(d, "agent_warned.json"), json.dumps({"five_hour": {"resets_at": R5, "level": 90}}))
    check("hook/session: a record in the unsessioned form = nothing announced (speaks)", hook_in() is not None)

    d = seed(five=(91.0, R5))
    keep = tg.AGENT_KEEP_S
    put(os.path.join(d, "agent_warned.json"), json.dumps({"sessions": {
        "old": {"five_hour": {"resets_at": R5, "level": 90}, "seen": NOW + 5 - keep},
        "edge": {"five_hour": {"resets_at": R5, "level": 90}, "seen": NOW + 5 - keep + 1},
        "noseen": {"five_hour": {"resets_at": R5, "level": 90}},
        "boolseen": {"seen": True},
        "notadict": 7}}))
    hook_sid("new")
    check("hook/session: records older than AGENT_KEEP_S, without `seen` or damaged are dropped; one second younger is kept",
          sorted(book(d)["sessions"]) == ["edge", "new"], book(d))
    check("hook/session: a kept session stays silent at its level", hook_sid("edge") is None)

    d = seed(five=(91.0, R5))
    n = tg.AGENT_KEEP_N
    put(os.path.join(d, "agent_warned.json"), json.dumps({"sessions": {
        "s%03d" % i: {"five_hour": {"resets_at": R5, "level": 90}, "seen": NOW - 1000 + i} for i in range(n)}}))
    hook_sid("newest")
    s_ = book(d)["sessions"]
    check("hook/session: at most AGENT_KEEP_N sessions; the oldest goes, the newest is written",
          len(s_) == n and "s000" not in s_ and "s001" in s_ and "s%03d" % (n - 1) in s_ and "newest" in s_, len(s_))
    check("hook/session: AGENT_KEEP_S covers the 7-day window, AGENT_KEEP_N is 200",
          tg.AGENT_KEEP_S == 8 * 86400 and tg.AGENT_KEEP_N == 200)

    # subagents: Claude Code gives their tool calls the parent's session_id and their own agent_id (measured live 15.10)
    d = seed(five=(91.0, R5), seven=(64.0, R7))
    sub = lambda aid, now=NOW + 5: hook_in(raw=json.dumps({"hook_event_name": "PostToolUse", "session_id": "S", "agent_id": aid}), now=now)
    check("hook/subagent: a subagent is told on its own record", sub("ag1") is not None and sub("ag1") is None)
    check("hook/subagent: the main conversation of the same session is still told", hook_sid("S") is not None and hook_sid("S") is None)
    check("hook/subagent: another subagent is told too", sub("ag2") is not None)
    check("hook/subagent: records are `S`, `S#ag1`, `S#ag2`", sorted(book(d)["sessions"]) == ["S", "S#ag1", "S#ag2"], book(d))
    check("hook/subagent: an empty or non-string agent_id is the main record",
          tg._session_key({"session_id": "S", "agent_id": ""}) == "S" and tg._session_key({"session_id": "S", "agent_id": 5}) == "S")

    # one window = resets_at within AGENT_SAME_WINDOW_S (a jitter of the reset time does not re-announce)
    for shift, again in ((tg.AGENT_SAME_WINDOW_S - 1, False), (tg.AGENT_SAME_WINDOW_S, True), (-(tg.AGENT_SAME_WINDOW_S - 1), False)):
        t, w0 = tg.agent_decide({"five_hour": win(91, R5)}, TH, {})
        t2, _ = tg.agent_decide({"five_hour": win(91, R5 + shift)}, TH, w0)
        check("decide: resets_at moved by %+d s -> %s" % (shift, "new window, told again" if again else "same window, silent"),
              (t2 is not None) == again, t2)
    check("decide: AGENT_SAME_WINDOW_S is 600", tg.AGENT_SAME_WINDOW_S == 600)

    # every silence is named, and recorded only on change
    d = fresh()
    hook_in()
    check("hook/outcome: no state at all -> `no usable plan window` (file: missing)",
          book(d)["hook"]["outcome"] == "silent: no usable plan window in state.json (file: missing)", book(d))
    d = seed()
    hook_in()
    check("hook/outcome: a state without windows -> `no usable plan window` (file: ok)",
          book(d)["hook"]["outcome"] == "silent: no usable plan window in state.json (file: ok)", book(d))
    d = seed(five=(97.0, NOW - 100), seven=(10.0, NOW - 50))
    hook_in()
    check("hook/outcome: only ended windows -> `no usable plan window`", book(d)["hook"]["outcome"].startswith("silent: no usable plan window"), book(d))
    d = seed(five=(50.0, R5))
    hook_in(event="Stop", now=NOW + 5)
    check("hook/outcome: an event not ours is named", book(d)["hook"]["outcome"] == 'silent: event not ours ("Stop")', book(d))
    hook_in(event="Stop", now=NOW + 99)
    check("hook/outcome: the same outcome again is NOT rewritten (since stays)", book(d)["hook"]["since"] == NOW + 5, book(d))
    hook_in(raw="[1]", now=NOW + 100)
    check("hook/outcome: input that is not an object is named", book(d)["hook"] == {"outcome": "silent: hook input is not a JSON object", "since": NOW + 100}, book(d))
    d = seed(five=(91.0, R5))
    put(os.path.join(d, "agent_warned.json"), "{ not json")
    hook_in()
    check("hook/outcome: a damaged record is named in the outcome", book(d)["hook"]["outcome"] == "spoke; agent_warned.json was damaged (corrupt): treated as empty", book(d))
    d = seed(five=(91.0, R5))
    put(os.path.join(d, "agent_warned.json"), json.dumps({"sessions": [1]}))
    hook_in()
    check("hook/outcome: `sessions` not an object is named", book(d)["hook"]["outcome"].endswith("damaged (sessions not an object): treated as empty"), book(d))

    # the age in the text is the OLDER of: numbers last confirmed, this window last sent
    d = seed(five=(91.0, R5), seven=(64.0, R7))
    tg.update(rl(five=(92.0, R5)), NOW + 1200)  # only the 5-hour window comes again, CHANGED; the week was last sent at NOW
    st = tg.read_state(NOW + 1205)
    check("read_state: sent_age_s per window", st["five_hour"]["sent_age_s"] == 5 and st["seven_day"]["sent_age_s"] == 1205, st)
    check("read_state: the numbers were confirmed 5 s ago (the two ages really differ here)", st["seven_day"]["reading_age_s"] == 5, st)
    t, _ = tg.agent_decide({"seven_day": dict(st["seven_day"], used_percentage=91.0)}, TH, {})
    check("agent text: a window not sent for 20 min says its own age, not the fresh one of the other window",
          "reading 20 min old" in (t or ""), t)

    d = seed(five=(91.0, R5))
    check("hook: stale reading says `at least` and the real age (60 min)", "at least 91% used (reading 60 min old)" in json.loads(hook_in(now=NOW + 3600))["hookSpecificOutput"]["additionalContext"])

with agent_env(TRUEGAUGE_AGENT_AT="off"):
    seed(five=(99.0, R5), seven=(99.0, R7))
    check("hook: TRUEGAUGE_AGENT_AT=off -> silence; the record says off and holds no session",
          hook_in() is None and json.loads(get(os.path.join(tg.HOME, "agent_warned.json")))
          == {"sessions": {}, "hook": {"outcome": "silent: off (TRUEGAUGE_AGENT_AT)", "since": NOW + 5}})
with agent_env(TRUEGAUGE_AGENT_AT="garbage"):
    d = seed(five=(91.0, R5))
    check("hook: a garbage TRUEGAUGE_AGENT_AT -> the default thresholds", hook_in() is not None)
    check("hook: ... and the refusal is in the hook's own record (a settings `env` value is not visible to a shell)",
          json.loads(get(os.path.join(d, "agent_warned.json")))["hook"]["outcome"]
          == "spoke; REFUSED (default used): TRUEGAUGE_AGENT_AT='garbage' (integers 1..100 separated by commas, or off)")
with agent_env(TRUEGAUGE_AGENT_AT="50"):
    seed(five=(55.0, R5))
    check("hook: TRUEGAUGE_AGENT_AT=50 -> speaks at 55%", hook_in() is not None)
with agent_env(TRUEGAUGE_LANG="ru"):
    seed(five=(91.0, R5))
    check("hook: TRUEGAUGE_LANG=ru -> Russian text", "Лимит плана Claude (truegauge)" in json.loads(hook_in())["hookSpecificOutput"]["additionalContext"])

# the real script: stdout / exit code in every case
def run_hook(stdin, home, extra=None):
    guard_path(home)
    env = {k: v for k, v in os.environ.items() if k not in COLOR_VARS and k not in AGENT_ENV}
    env.update(TRUEGAUGE_HOME=home, TRUEGAUGE_FABLE="0", **(extra or {}))
    r = subprocess.run([sys.executable, os.path.join(os.path.dirname(HERE), "truegauge.py"), "--hook"],
                       input=stdin, capture_output=True, env=env)
    return r.returncode, r.stdout, r.stderr


import time as _time  # noqa: E402
_n = int(_time.time())
d = fresh()
tg.update(rl(five=(91.0, _n + 4800), seven=(64.0, _n + 3 * 86400 + 100)), _n)
c1, o1, e1 = run_hook(b'{"hook_event_name":"PostToolUse"}', d)
c2, o2, e2 = run_hook(b'{"hook_event_name":"PostToolUse"}', d)
check("script --hook: first call prints the JSON, exit 0, nothing on stderr", c1 == 0 and e1 == b"" and json.loads(o1)["hookSpecificOutput"]["hookEventName"] == "PostToolUse", (c1, o1, e1))
check("script --hook: second call prints NOTHING, exit 0", c2 == 0 and o2 == b"" and e2 == b"", (c2, o2, e2))
for label, raw in (("unreadable stdin (not JSON)", b"\xff\xfe nonsense"), ("empty stdin", b""), ("unknown event", b'{"hook_event_name":"Stop"}')):
    d = fresh()
    tg.update(rl(five=(91.0, _n + 4800)), _n)
    c, o, e = run_hook(raw, d)
    check("script --hook: %s -> empty stdout, exit 0" % label, c == 0 and o == b"", (c, o, e))
d = fresh()
c, o, e = run_hook(b'{"hook_event_name":"PostToolUse"}', d)
check("script --hook: no state at all -> empty stdout, exit 0", c == 0 and o == b"", (c, o, e))
c, o, e = run_hook(b"not json", d)
check("script --hook: unreadable stdin leaves the reason in the hook's own record",
      json.loads(get(os.path.join(d, "agent_warned.json")))["hook"]["outcome"].startswith("fault: hook: JSONDecodeError"),
      get(os.path.join(d, "agent_warned.json")))
check("script --hook: ... and NOT in the state file (no state file is created for it)", not os.path.exists(os.path.join(d, "state.json")))

# a fault INSIDE the decision: still silent, exit 0, recorded
import io as _io  # noqa: E402
d = fresh()
tg.update(rl(five=(91.0, R5)), NOW)
_real_rs = tg.read_state
_real_in = sys.stdin
_real_out = sys.stdout


class _Std(object):
    def __init__(self, data=b""):
        self.buffer = _io.BytesIO(data)


tg.read_state = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
sys.stdin, sys.stdout = _Std(b'{"hook_event_name":"PostToolUse"}'), _Std()
try:
    rc = tg.hook_mode()
    printed = sys.stdout.buffer.getvalue()
finally:
    tg.read_state, sys.stdin, sys.stdout = _real_rs, _real_in, _real_out
check("hook: an exception inside -> exit 0, empty stdout", rc == 0 and printed == b"", (rc, printed))
check("hook: the exception is recorded in the hook's own record", "boom" in json.loads(get(tg._warned_path()))["hook"]["outcome"], get(tg._warned_path()))
check("hook: ... and NOT in the state file's last_error", "last_error" not in json.loads(get(tg.STATE)), get(tg.STATE))
_st = tg.read_state(NOW + 5)
check("hook: a hook fault leaves the human's status line fresh (no `unresolved error` on the windows)",
      _st["five_hour"]["status"] == "fresh" and _st["_tap"]["unresolved_error"] is None, _st)

# a failed write of the record: nothing is said (a message that cannot be recorded would repeat on every call)
d = fresh()
tg.update(rl(five=(91.0, R5)), NOW)
_real_save = tg._save
tg._save = lambda *a, **k: (_ for _ in ()).throw(OSError("disk"))
try:
    with agent_env():
        try:
            out_failed = hook_in()
        except OSError:
            out_failed = "raised"
finally:
    tg._save = _real_save
check("hook: if the record cannot be saved nothing is said (the fault is raised to hook_mode, which stays silent)", out_failed == "raised", out_failed)

# --show line
d = fresh()
tg.update(rl(five=(91.0, R5)), NOW)
with agent_env():
    hook_in()
    code, out, _ = cli("--show")
check("--show: agent warning line with thresholds and what is announced",
      "agent warning: thresholds (this shell) 80,90,95; announced: 1 session(s); last told " in out
      and "(session without id): five_hour level 90" in out, out)
check("--show: the hook's own last outcome", "agent hook, as the hook saw it (its environment is Claude Code's, not this shell's): spoke (since " in out, out)
d = fresh()
code, out, _ = cli("--show")
check("--show: no hook run yet is said", "agent hook: no run recorded yet" in out, out)
put(os.path.join(d, "agent_warned.json.tmp999"), "{}")
os.utime(os.path.join(d, "agent_warned.json.tmp999"), (NOW - 600, NOW - 600))
check("orphans: a left-behind agent_warned.json.tmp is counted", tg._orphans(NOW) == 1, tg._orphans(NOW))
with agent_env(TRUEGAUGE_AGENT_AT="zzz"):
    code, out, _ = cli("--show")
check("--show: a refused TRUEGAUGE_AGENT_AT is named", "REFUSED (default used): TRUEGAUGE_AGENT_AT='zzz'" in out, out)
with agent_env(TRUEGAUGE_AGENT_AT="off"):
    code, out, _ = cli("--show")
check("--show: off", "agent warning: thresholds (this shell) off" in out, out)

# ---- install / uninstall of the hooks in a TEMP settings.json with FOREIGN hooks
FOREIGN_POST = {"matcher": "Bash", "hooks": [{"type": "command", "command": "python ~/mine/post.py", "timeout": 5}]}
FOREIGN_PROMPT = {"hooks": [{"type": "command", "command": "python ~/mine/prompt.py"}]}
d = fresh()
sp = os.path.join(d, "settings.json")
orig = {"model": "opus", "hooks": {"PostToolUse": [FOREIGN_POST], "UserPromptSubmit": [FOREIGN_PROMPT], "Stop": [{"hooks": [{"type": "command", "command": "x"}]}]}}
put(sp, json.dumps(orig, indent=2))
code, out, err = cli("--install", "--settings", sp)
s = json.loads(get(sp))
ours = [(ev, h) for ev in ("PostToolUse", "UserPromptSubmit") for e in s["hooks"][ev] for h in e["hooks"] if "truegauge.py" in h.get("command", "")]
check("hooks install: exit 0", code == 0, (code, err))
check("hooks install: our entry is added to PostToolUse and to UserPromptSubmit", sorted(ev for ev, _ in ours) == ["PostToolUse", "UserPromptSubmit"], ours)
check("hooks install: the command is `<our command> --hook`, timeout 10, type command",
      all(h["command"] == tg._our_command() + " --hook" and h["timeout"] == 10 and h["type"] == "command" for _, h in ours), ours)
check("hooks install: foreign entries are intact and first",
      s["hooks"]["PostToolUse"][0] == FOREIGN_POST and s["hooks"]["UserPromptSubmit"][0] == FOREIGN_PROMPT and s["hooks"]["Stop"] == orig["hooks"]["Stop"], s["hooks"])
check("hooks install: the status line is set too", "truegauge.py" in s["statusLine"]["command"] and "--hook" not in s["statusLine"]["command"], s["statusLine"])
check("hooks install: ONE write, ONE backup", len(backups(sp)) == 1, backups(sp))
code, outs, _ = cli("--status", "--settings", sp)
check("hooks status: both hooks yes", "PostToolUse yes, UserPromptSubmit yes" in outs, outs)
written = get(sp)
code, out2, _ = cli("--install", "--settings", sp)
check("hooks install twice: no duplicates, file untouched, says already", code == 0 and get(sp) == written and "already" in out2, out2)
code, out3, _ = cli("--uninstall", "--settings", sp)
s = json.loads(get(sp))
check("hooks uninstall: our entries gone, foreign ones intact",
      s["hooks"] == orig["hooks"] and "statusLine" not in s and s["model"] == "opus", s)
code, outs, _ = cli("--status", "--settings", sp)
check("hooks status after uninstall: both no", "PostToolUse no, UserPromptSubmit no" in outs, outs)
check("hooks status: no NOTE when nothing is off", "NOTE:" not in outs, outs)
put(sp, json.dumps({"disableAllHooks": True, "hooks": {"PostToolUse": [{"hooks": [{"type": "command", "command": "python /old/place/truegauge.py --hook"}]}]}}))
code, outs, _ = cli("--status", "--settings", sp)
check("hooks status: disableAllHooks is named", "NOTE: disableAllHooks is true in this file" in outs, outs)
check("hooks status: a hook of ours from another copy is named", "NOTE: a hook of ours runs another copy or interpreter (moved?): python /old/place/truegauge.py --hook" in outs, outs)

# our hook next to a foreign one INSIDE one entry; empties are removed, the foreign stays
d = fresh()
sp = os.path.join(d, "settings.json")
put(sp, json.dumps({"hooks": {"PostToolUse": [{"matcher": "x", "hooks": [{"type": "command", "command": "a.py"},
                    {"type": "command", "command": "python /x/truegauge.py --hook"}]}]}}))
code, out, _ = cli("--uninstall", "--settings", sp)
s = json.loads(get(sp))
check("hooks uninstall (statusLine not ours): only our command leaves a shared entry",
      code == 0 and s == {"hooks": {"PostToolUse": [{"matcher": "x", "hooks": [{"type": "command", "command": "a.py"}]}]}}, s)
check("hooks uninstall: a backup was made first", len(backups(sp)) == 1)
d = fresh()
sp = os.path.join(d, "settings.json")
put(sp, json.dumps({"model": "m", "hooks": {"PostToolUse": [{"hooks": [{"type": "command", "command": "python /x/truegauge.py --hook"}]}]}}))
code, out, _ = cli("--uninstall", "--settings", sp)
check("hooks uninstall: empty list and empty `hooks` key are removed", json.loads(get(sp)) == {"model": "m"}, get(sp))
d = fresh()
sp = os.path.join(d, "settings.json")
put(sp, json.dumps({"hooks": {"PostToolUse": [], "Foo": []}}))
before = get(sp)
code, out, _ = cli("--uninstall", "--settings", sp)
check("uninstall with no hooks of ours: file untouched even if `hooks` has empty lists", get(sp) == before and backups(sp) == [], out)
d = fresh()
sp = os.path.join(d, "settings.json")
put(sp, json.dumps({"hooks": {"PostToolUse": [{"hooks": [{"type": "command", "command": "python /x/truegauge.py"}]}]}}))
before = get(sp)
code, out, _ = cli("--uninstall", "--settings", sp)
check("uninstall: a command naming truegauge.py WITHOUT --hook is not ours", get(sp) == before, get(sp))

# --no-agent-hook
d = fresh()
sp = os.path.join(d, "settings.json")
put(sp, json.dumps(orig, indent=2))
code, out, _ = cli("--install", "--no-agent-hook", "--settings", sp)
s = json.loads(get(sp))
check("--no-agent-hook: status line set, hooks exactly as before", code == 0 and s["hooks"] == orig["hooks"] and "truegauge.py" in s["statusLine"]["command"], s)
code, out, _ = cli("--install", "--settings", sp)
s = json.loads(get(sp))
check("install after `--no-agent-hook` adds the hooks (status line already ours)",
      code == 0 and "hooks added" in out and len(s["hooks"]["PostToolUse"]) == 2 and len(s["hooks"]["UserPromptSubmit"]) == 2, (out, s["hooks"]))
code, out, _ = cli("--uninstall", "--settings", sp)
s = json.loads(get(sp))
check("uninstall after a two-step install: the line that was there BEFORE truegauge comes back (here: none), hooks gone, foreign hooks intact",
      code == 0 and "statusLine" not in s and s["hooks"] == orig["hooks"], s)
d = fresh()
sp = os.path.join(d, "settings.json")
code, out, _ = cli("--install", "--no-agent-hook", "--settings", sp)
check("--no-agent-hook on a missing file: no `hooks` key at all", "hooks" not in json.loads(get(sp)), get(sp))

# a shape we do not understand is refused, nothing written
for label, content in (("`hooks` is a list", {"hooks": []}), ("`hooks.PostToolUse` is an object", {"hooks": {"PostToolUse": {}}})):
    d = fresh()
    sp = os.path.join(d, "settings.json")
    put(sp, json.dumps(content))
    before = get(sp)
    code, out, err = cli("--install", "--settings", sp)
    check("install refuses when %s: exit 2, file untouched, no saved previous" % label,
          code == 2 and get(sp) == before and backups(sp) == [] and not os.path.exists(prev_file()), (code, err))

check("version is 0.4.0", tg.__version__ == "0.4.0")


# ---- 0.4: the fresh 5-hour group, TRUEGAUGE_TIME, the idle probe
def w1(status, used=None, rin=None):
    """The window shape read_state returns (the first `win` of this file; a later `win` takes other arguments)."""
    v = {"status": status, "resets_in_s": rin, "used_percentage": used, "remaining_percentage": None}
    if status == "fresh":
        v["remaining_percentage"] = 100 - used
    return v


EXP5 = {"five_hour": w1("expired", 99, -5)}
with envset():
    check("fresh 5h: default green #4eba65, no percent", R(EXP5) == FRESH_G, R(EXP5))
    check("live 5:00:00 left -> `5h 0%`: it keeps its percent, so it is not the fresh group",
          R({"five_hour": w1("fresh", 0, 18000)}) == "5h 0%", R({"five_hour": w1("fresh", 0, 18000)}))
    check("live 4:59:59 left -> `4h 0%`", R({"five_hour": w1("fresh", 0, 17999)}) == "4h 0%", R({"five_hour": w1("fresh", 0, 17999)}))
    check("fresh 5h with Fable and tokens: the rest of the line is unchanged",
          R(dict(EXP5, fable=w1("fresh", 4, None)), {"new": 2600000, "complete": True}) == FRESH_G + " F4% Σ2.6M",
          R(dict(EXP5, fable=w1("fresh", 4, None)), {"new": 2600000, "complete": True}))
    check("fresh 5h is never red, whatever the old reading was", RED not in R(EXP5), R(EXP5))
    check("expired week alone -> nothing (the week is not replaced)", R({"seven_day": w1("expired", 80, -5)}) == "",
          R({"seven_day": w1("expired", 80, -5)}))
    check("no 5h data at all (first install) -> no `5h` group: only a known reset shows it", R({}) == "", R({}))
with envset(NO_COLOR="1"):
    check("NO_COLOR -> plain `5h`", R(EXP5) == "5h", R(EXP5))
for off in ("off", "0", "OFF"):
    with envset(TRUEGAUGE_FRESH_COLOR=off):
        check("TRUEGAUGE_FRESH_COLOR=%r -> plain `5h`, not a refusal" % off,
              R(EXP5) == "5h" and "TRUEGAUGE_FRESH_COLOR" not in tg.color_config()["refused"], R(EXP5))
with envset(TRUEGAUGE_FRESH_COLOR="ansi"):
    check("TRUEGAUGE_FRESH_COLOR=ansi -> bold bright green", R(EXP5) == "\033[1;92m5h" + RS, R(EXP5))
with envset(TRUEGAUGE_FRESH_COLOR="#00ff00"):
    check("TRUEGAUGE_FRESH_COLOR=#00ff00 -> that colour", R(EXP5) == "\033[1;38;2;0;255;0m5h" + RS, R(EXP5))
with envset(TRUEGAUGE_FRESH_COLOR="green"):
    cc = tg.color_config()
    check("TRUEGAUGE_FRESH_COLOR=green -> default colour + refusal named",
          R(EXP5) == FRESH_G and "TRUEGAUGE_FRESH_COLOR" in cc["refused"], (R(EXP5), cc["refused"]))
with envset(TRUEGAUGE_MARK="all"):
    check("mark=all does not touch the fresh `5h`", R(EXP5) == FRESH_G, R(EXP5))
with envset(TRUEGAUGE_RED_COLOR="#123456", TRUEGAUGE_COLD_COLOR="#654321"):
    check("other colours do not change the fresh `5h`", R(EXP5) == FRESH_G, R(EXP5))

# TRUEGAUGE_TIME: parsing
for raw, want, refused in (("", "short", False), ("short", "short", False), ("full", "full", False), ("FULL", "full", False),
                           (" full ", "full", False), ("long", "short", True), ("2", "short", True)):
    with envset(TRUEGAUGE_TIME=raw):
        cc = tg.color_config()
        check("TRUEGAUGE_TIME=%r -> %s%s" % (raw, want, ", refused" if refused else ""),
              cc["time"] == want and ("TRUEGAUGE_TIME" in cc["refused"]) == refused, (cc["time"], cc["refused"]))

# fmt_left full: boundaries on both sides of each unit change, all rounded down
for sec, days_only, want in ((0, False, "0m"), (59, False, "0m"), (60, False, "1m"), (3599, False, "59m"),
                             (3600, False, "1h0m"), (3660, False, "1h1m"), (15900, False, "4h25m"), (17999, False, "4h59m"),
                             (18000, False, "5h0m"), (86399, False, "23h59m"), (86400, False, "1d0h"),
                             (568800, False, "6d14h"), (0, True, "0d0h"), (3599, True, "0d0h"), (50400, True, "0d14h"),
                             (86399, True, "0d23h"), (86400, True, "1d0h"), (604799, True, "6d23h")):
    check("fmt_left full %d%s -> %s" % (sec, " (week)" if days_only else "", want),
          tg.fmt_left(sec, W, days_only=days_only, full=True) == want, tg.fmt_left(sec, W, days_only=days_only, full=True))
# short stays as it was at the same boundaries
for sec, days_only, want in ((3599, False, "59m"), (3600, False, "1h"), (17999, False, "4h"), (18000, False, "5h"),
                             (86399, True, "0d"), (86400, True, "1d")):
    check("fmt_left short %d%s -> %s (unchanged)" % (sec, " (week)" if days_only else "", want),
          tg.fmt_left(sec, W, days_only=days_only) == want, tg.fmt_left(sec, W, days_only=days_only))
with envset(TRUEGAUGE_TIME="full"):
    got = R({"five_hour": w1("fresh", 38.4, 15900), "seven_day": w1("fresh", 54, 568800)})
    check("full: `4h25m 38% 6d14h 54%`", got == "4h25m 38% 6d14h 54%", got)
    check("full: the fresh group is `5h0m`", R(EXP5) == "\033[1;38;2;78;186;101m5h0m" + RS, R(EXP5))
    got = R({"five_hour": w1("fresh", 97.6, 2220)})
    check("full: last hour is minutes only, red still works", got == RED + "37m 98%" + RS, got)
with envset():
    got = R({"five_hour": w1("fresh", 38.4, 15900), "seven_day": w1("fresh", 54, 568800)})
    check("short (default): `4h 38% 6d 54%`", got == "4h 38% 6d 54%", got)

# --show names the new settings, for this shell and as the status line saw them
out = shown({"TRUEGAUGE_FRESH_COLOR": "off", "TRUEGAUGE_TIME": "full"})
check("--show: fresh colour off and time full for this shell", "fresh 5h colour: off; time: full" in out, out)
out = shown({"TRUEGAUGE_TIME": "long"})
check("--show: a refused TRUEGAUGE_TIME is named", "TRUEGAUGE_TIME=" in out and "REFUSED" in out, out)

# the saved render settings: 0.3 records stay readable, a wrong shape of a 0.4 key is damage
fresh()
base = {"th": {"five_hour": 98, "seven_day": 99}, "sgr": "x", "no_color": False, "refused": {}, "cold_sgr": None, "mark": "off"}
check("saved settings of 0.3 (no 0.4 keys) are still read", tg._saved_render_cfg({"render_cfg": dict(base)})[0] is not None)
check("saved settings with 0.4 keys are read",
      tg._saved_render_cfg({"render_cfg": dict(base, fresh_sgr="y", time="full")})[0] is not None)
check("saved `time` of the wrong value -> damaged, not believed",
      tg._saved_render_cfg({"render_cfg": dict(base, time="long")})[0] is None)
check("saved `fresh_sgr` of the wrong type -> damaged", tg._saved_render_cfg({"render_cfg": dict(base, fresh_sgr=5)})[0] is None)

# the Fable probe is skipped while idle: it runs only when a reply has come since the last probe
os.environ["TRUEGAUGE_FABLE"] = "1"  # on for these checks only; the spawn is injected, nothing real starts
for label, done, upd, want in (("first probe ever (no probe yet)", None, None, True),
                               ("no reply since the last probe started -> skipped", NOW, NOW - 55, False),
                               ("reply time EQUAL to the last probe's start -> skipped", NOW, NOW - 5, False),
                               ("a reply DURING the last probe (1 s after it started) -> started", NOW, NOW - 4, True),
                               ("a reply after the last probe ended -> started", NOW, NOW + 1, True),
                               ("no reply time recorded at all, a probe was done -> skipped", NOW, None, False)):
    fresh()
    d0 = {}
    if done is not None:
        d0["probe_done_at"] = done
        d0["probe_started_at"] = done - 5
    if upd is not None:
        d0["updated_at"] = upd
    tg._save(d0)
    calls = []
    r = tg.maybe_start_probe(NOW + 700, spawn=lambda: calls.append(1))
    d1, _ = tg._read()
    started_written = d1.get("probe_started_at") == NOW + 700
    check("idle probe: %s" % label, (r is True) == want and (len(calls) == 1) == want and started_written == want,
          (r, calls, d1.get("probe_started_at")))
fresh()
tg._save({"probe_done_at": NOW, "probe_started_at": NOW - 5, "updated_at": NOW + 10})
calls = []
check("idle probe: a reply after the probe, but within 600 s -> still waits for the interval",
      tg.maybe_start_probe(NOW + 599, spawn=lambda: calls.append(1)) is False and calls == [], calls)
os.environ["TRUEGAUGE_FABLE"] = "0"  # back to the run-wide setting

# the source is silent: a reply came in THIS session, yet no 5-hour reading -> a reset window is left out, not `5h`
def _silent_case(with_reply, with_rl):
    d = fresh()
    tg.update(rl(five=(70, NOW + 100)), NOW)  # the stored 5-hour window resets at NOW + 100
    tp = os.path.join(d, "s.jsonl")
    with open(tp, "w") as f:
        if with_reply:
            line = json.loads(msg("m1", 10, 100, 5, 1000))
            line["timestamp"] = "2027-01-15T08:00:00Z"
            f.write(json.dumps(line) + "\n")
        else:
            f.write(json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n")
    pl = {"transcript_path": tp, "session_id": "sil"}
    if with_rl:
        pl["rate_limits"] = {"five_hour": {"used_percentage": 70, "resets_at": NOW + 100}}  # an idle session's replay
    with envset(NO_COLOR="1"):
        return tg.statusline(pl, NOW + 200)


got = _silent_case(False, False)
check("no reply yet in this session, no reading -> `5h` (before the first reply)", got.startswith("5h") and "%" not in got.split(" ")[0], got)
got = _silent_case(True, False)
check("a reply came and still no 5-hour reading -> silent source: no `5h`", not got.startswith("5h"), got)
got = _silent_case(True, True)
check("a reply came and the (replayed) reading is there -> `5h`", got.startswith("5h") and "%" not in got.split(" ")[0], got)


# a real line on 2026-10-15 20:24: the window reset in a working session and Claude Code stopped sending it -> the group
# vanished. A window THIS session received earlier is a reset, not a silent source: `5h` stays.
def _reset_case(heard_sid, this_sid="sil", damage=None):
    d = fresh()
    tg.update(rl(five=(70, NOW + 100), session_id=heard_sid) if heard_sid else rl(five=(70, NOW + 100)), NOW)
    if damage is not None:
        s = state_now()
        s["five_hour_sids"] = damage
        tg._save(s)
    tp = os.path.join(d, "s.jsonl")
    line = json.loads(msg("m1", 10, 100, 5, 1000))
    line["timestamp"] = "2027-01-15T08:00:00Z"
    with open(tp, "w") as f:
        f.write(json.dumps(line) + "\n")
    with envset(NO_COLOR="1"):
        return tg.statusline({"transcript_path": tp, "session_id": this_sid}, NOW + 200)


got = _reset_case("sil")
check("reset: this session received the 5-hour window, then none after the reset -> `5h`",
      got.startswith("5h") and "%" not in got.split(" ")[0], got)
got = _reset_case("sil-other-session")
check("reset: ANOTHER session received it (prefix differs at char 4), this one never -> silent, no `5h`",
      not got.startswith("5h"), got)
got = _reset_case("sil", this_sid="sim")
check("reset: a neighbouring session id (sil/sim) does not count -> silent", not got.startswith("5h"), got)
got = _reset_case("sil", this_sid=None)
check("reset: no session id in the payload -> silent (nothing to match)", not got.startswith("5h"), got)
# the reading in THIS payload still counts on its own: no session id (nothing recorded), yet the window is sent
_d = fresh()
tg.update(rl(five=(70, NOW + 100)), NOW)
_tp = os.path.join(_d, "s.jsonl")
_line = json.loads(msg("m1", 10, 100, 5, 1000))
_line["timestamp"] = "2027-01-15T08:00:00Z"
with open(_tp, "w") as f:
    f.write(json.dumps(_line) + "\n")
with envset(NO_COLOR="1"):
    got = tg.statusline({"transcript_path": _tp, "rate_limits": {"five_hour": {"used_percentage": 70, "resets_at": NOW + 100}}},
                        NOW + 200)
check("reset: no session id, but the 5-hour reading is in this payload -> `5h`", got.startswith("5h") and "%" not in got.split(" ")[0], got)
# a fault in the sessions record is an extra: the line stays, the silent-source rule holds, the fault is filed as an extra
_orig_heard = tg._heard_five
def _raise(sid):
    raise RuntimeError("probe")


tg._heard_five = _raise
try:
    got = _reset_case("sil")
finally:
    tg._heard_five = _orig_heard
check("reset: a sessions-record fault -> the line stays, no `5h`, filed under last_extra_error",
      got != "" and not got.startswith("5h") and "sessions record: RuntimeError: probe" in str((state_now().get("last_extra_error") or {}).get("msg"))
      and "last_error" not in state_now(), (got, state_now().get("last_extra_error"), state_now().get("last_error")))
for _bad in ("x", ["sil"], {"sil": "yesterday"}, {"sil": None}):
    got = _reset_case("sil", damage=_bad)
    check("reset: a damaged sessions record %r -> silent, no fault in the line" % (_bad,), not got.startswith("5h"), got)
fresh()
tg.update(rl(five=(70, NOW + 100), session_id="sil"), NOW)
check("reset: a sessions record is written only with the 5-hour window", state_now().get("five_hour_sids") == {"sil": NOW},
      state_now().get("five_hour_sids"))
fresh()
tg.update(rl(seven=(40, NOW + 86400), session_id="wk"), NOW)
check("reset: the week alone does not mark the session as sending the 5-hour window", "five_hour_sids" not in state_now(),
      state_now().get("five_hour_sids"))
fresh()
tg.update({"rate_limits": {"five_hour": {"used_percentage": "x", "resets_at": NOW + 100}}, "session_id": "bad"}, NOW)
check("reset: a refused 5-hour reading does not mark the session", "five_hour_sids" not in state_now(), state_now())
# a damaged record must not stop the state write: the next 5-hour reading rebuilds it from the sound entries
fresh()
tg._save({"five_hour_sids": {"old": NOW - 50, "bad": None, "txt": "yesterday"}})
tg.update(rl(five=(70, NOW + 100), session_id="new"), NOW)
check("reset: damaged entries are dropped, the write goes through, sound ones stay",
      state_now().get("five_hour_sids") == {"old": NOW - 50, "new": NOW} and state_now().get("five_hour", {}).get("used_percentage") == 70,
      state_now())
# the record is bounded: SIDS_KEEP newest sessions, none older than SIDS_DAYS (neighbours of both edges)
_rec = {"s%03d" % i: NOW - 1000 + i for i in range(tg.SIDS_KEEP)}
_out = tg._recent_sids(_rec, "new", NOW)
check("reset: SIDS_KEEP + 1 sessions -> the oldest goes, the new one stays",
      len(_out) == tg.SIDS_KEEP and "s000" not in _out and "s001" in _out and _out["new"] == NOW, sorted(_out)[:3])
_out = tg._recent_sids({"a": NOW - tg.SIDS_DAYS * 86400, "b": NOW - tg.SIDS_DAYS * 86400 + 1}, "c", NOW)
check("reset: a session exactly SIDS_DAYS old goes, one second younger stays", sorted(_out) == ["b", "c"], _out)
with envset():
    check("render(fresh_ok=False): the reset 5-hour window is left out",
          tg.render({"five_hour": w1("expired", 99, -5)}, None, None, "en", None, fresh_ok=False) == "")
fresh()
_n4 = time.time()
tg.update(rl(five=(50, int(_n4) + 7200)), _n4)
with envset(TRUEGAUGE_FRESH_COLOR="ansi", TRUEGAUGE_TIME="full"):
    tg._remember_render_cfg(tg.color_config(), _n4)
with envset():
    out = cli("--show")[1]
check("--show: the status line's own fresh colour and time mode are shown with its settings",
      "status line fresh 5h colour: ESC[1;92m; time: full" in out, out)


# ---- 0.4 dim: no `>=` / `<=` in the line; a number not being spent now, old or incomplete is dim ----------------------
# The rule these checks hold: README, "Dim numbers".
DIM = "\033[38;2;98;98;98m"


def dimmed(text):
    return DIM + text + RS


@contextlib.contextmanager
def dimenv(value=None, **colour):
    """The dim colour for one block (None = unset = default) on top of envset's colour variables."""
    old = os.environ.get("TRUEGAUGE_DIM_COLOR")
    if value is None:
        os.environ.pop("TRUEGAUGE_DIM_COLOR", None)
    else:
        os.environ["TRUEGAUGE_DIM_COLOR"] = value
    try:
        with envset(**colour):
            yield
    finally:
        if old is None:
            os.environ.pop("TRUEGAUGE_DIM_COLOR", None)
        else:
            os.environ["TRUEGAUGE_DIM_COLOR"] = old


def lit(status, used, rin, active):
    v = w1(status, used, rin)
    v["active"] = active
    return v


TOK_DONE = {"new": 2600000, "complete": True}
TOK_PART = {"new": 2650000, "complete": False}
with dimenv():
    check("dim: a fresh 5-hour reading being spent -> plain", R({"five_hour": lit("fresh", 38.4, 7200, True)}) == "2h 38%",
          R({"five_hour": lit("fresh", 38.4, 7200, True)}))
    check("dim: a fresh 5-hour reading NOT being spent -> dim, same number",
          R({"five_hour": lit("fresh", 38.4, 7200, False)}) == dimmed("2h 38%"), R({"five_hour": lit("fresh", 38.4, 7200, False)}))
    check("dim: a window with no activity key (older state) -> dim", R({"five_hour": w1("fresh", 38.4, 7200)}) == dimmed("2h 38%"))
    check("dim: an OLD reading -> dim, rounded DOWN, no `>=`",
          R({"five_hour": lit("stale_reading", 38.9, 7200, False)}) == dimmed("2h 38%"), R({"five_hour": lit("stale_reading", 38.9, 7200, False)}))
    check("dim: the week not being spent -> dim", R({"seven_day": lit("fresh", 54, 3 * 86400, False)}) == dimmed("3d 54%"))
    check("dim: the week being spent -> plain", R({"seven_day": lit("fresh", 54, 3 * 86400, True)}) == "3d 54%")
    check("dim: red wins over dim (fresh 98, not spent now)", R({"five_hour": lit("fresh", 98, 7200, False)}) == red_of("2h 98%"),
          R({"five_hour": lit("fresh", 98, 7200, False)}))
    check("dim: red wins over dim (OLD 97.6: half up 98 -> red, number rounded down, no sign)",
          R({"five_hour": lit("stale_reading", 97.6, 7200, False)}) == red_of("2h 97%"), R({"five_hour": lit("stale_reading", 97.6, 7200, False)}))
    check("dim: old 97.49 -> not red, dim", R({"five_hour": lit("stale_reading", 97.49, 7200, False)}) == dimmed("2h 97%"))
    check("dim: the fresh green `5h` is never dim", R(EXP5) == FRESH_G, R(EXP5))
    check("dim: Fable being spent -> plain", R({"fable": lit("fresh", 4, None, True)}) == "F4%")
    check("dim: Fable not being spent -> dim", R({"fable": lit("fresh", 4, None, False)}) == dimmed("F4%"))
    check("dim: Fable old -> dim, no sign", R({"fable": lit("stale_reading", 4.7, 86400, False)}) == dimmed("F4%"),
          R({"fable": lit("stale_reading", 4.7, 86400, False)}))
    check("dim: a complete session total -> plain", R({}, TOK_DONE) == "Σ2.6M", R({}, TOK_DONE))
    check("dim: an incomplete session total -> dim, rounded DOWN, no `>=`", R({}, TOK_PART) == dimmed("Σ2.6M"), R({}, TOK_PART))
    _st = {"five_hour": lit("fresh", 38.4, 7200, True), "seven_day": lit("fresh", 54, 3 * 86400, False),
           "fable": lit("fresh", 4, None, False)}
    check("dim: the whole line -- each group on its own, the spaces between groups never coloured",
          R(_st, TOK_DONE, 55.2, "en", 548200) == "2h 38% " + dimmed("3d 54%") + " " + dimmed("F4%") + " T548K 55% Σ2.6M",
          R(_st, TOK_DONE, 55.2, "en", 548200))
    _signs = []
    for _status in ("fresh", "stale_reading"):
        for _act in (True, False):
            for _u in (0, 0.4, 38.5, 97.6, 98, 99.5, 100):
                _o = R({"five_hour": lit(_status, _u, 7200, _act), "seven_day": lit(_status, _u, 86400 * 2, _act),
                        "fable": lit(_status, _u, 86400, _act)}, TOK_PART, 55.2, "en", 548200)
                if tg.GE in _o or tg.LE in _o:
                    _signs.append(_o)
    check("dim: with the dim colour on, no `>=` / `<=` anywhere (84 states)", _signs == [], _signs[:2])
    # the context group drawn from the saved reading (0 tokens now: before the first reply / after /compact)
    _last = {"tokens": 548200, "pct": 55.2, "sid": "abc", "at": NOW}
    _o = tg.render({}, None, 0, "en", 0, False, _last)
    check("dim: the context from the saved reading -> dim, rounded UP, no `<=`", _o == dimmed("T549K 56%"), _o)
    _o = tg.render({}, None, 0, "en", 0, True, _last)
    check("dim: the saved context stays dim even with a cold cache (not live: dim wins over blue)", _o == dimmed("T549K 56%"), _o)
    check("dim: a live context is never dim", tg.render({}, None, 55.2, "en", 548200) == "T548K 55%")
with dimenv(NO_COLOR="1"):
    check("NO_COLOR: no dim; an old reading keeps `>=` (meaning not lost)",
          R({"five_hour": lit("stale_reading", 38.9, 7200, False)}) == "2h ≥38%", R({"five_hour": lit("stale_reading", 38.9, 7200, False)}))
    check("NO_COLOR: a fresh reading not being spent -> plain, no sign", R({"five_hour": lit("fresh", 38.4, 7200, False)}) == "2h 38%")
    check("NO_COLOR: incomplete total keeps `Σ>=`", R({}, TOK_PART) == "Σ≥2.6M", R({}, TOK_PART))
    check("NO_COLOR: the saved context keeps `<=`", tg.render({}, None, 0, "en", 0, False, _last) == "≤T549K ≤56%")
for _off in ("off", "0", "OFF"):
    with dimenv(_off):
        check("TRUEGAUGE_DIM_COLOR=%r -> no dim, the signs are back, not a refusal" % _off,
              R({"fable": lit("stale_reading", 4.7, 86400, False)}) == "F≥4%" and "TRUEGAUGE_DIM_COLOR" not in tg.color_config()["refused"],
              R({"fable": lit("stale_reading", 4.7, 86400, False)}))
with dimenv("ansi"):
    check("TRUEGAUGE_DIM_COLOR=ansi -> bright black of the theme, no bold", R({"fable": lit("fresh", 4, None, False)}) == "\033[90mF4%" + RS)
with dimenv("#112233"):
    check("TRUEGAUGE_DIM_COLOR=#112233 -> that colour, no bold", R({"fable": lit("fresh", 4, None, False)}) == "\033[38;2;17;34;51mF4%" + RS)
with dimenv("grey"):
    _cc = tg.color_config()
    check("TRUEGAUGE_DIM_COLOR=grey -> default + refusal named",
          R({"fable": lit("fresh", 4, None, False)}) == dimmed("F4%") and "TRUEGAUGE_DIM_COLOR" in _cc["refused"], _cc["refused"])
with dimenv(" "):
    check("TRUEGAUGE_DIM_COLOR=' ' -> default, silently", "TRUEGAUGE_DIM_COLOR" not in tg.color_config()["refused"])

# activity: what makes a number bright, from the state file
fresh()
T0 = NOW
tg.update(rl(five=(40, T0 + 7200), seven=(50, T0 + 3 * 86400)), T0)
st = tg.read_state(T0 + 10)
check("activity: a first sighting with no reply -> fresh but not spent now", st["five_hour"]["status"] == "fresh"
      and st["five_hour"]["active"] is False and st["seven_day"]["active"] is False, st["five_hour"])
tg.update(rl(five=(40, T0 + 7200), seven=(50, T0 + 3 * 86400)), T0 + 20)
check("activity: the same numbers again (a replay) -> still not spent", tg.read_state(T0 + 30)["five_hour"]["active"] is False)
tg.update(rl(five=(41, T0 + 7200), seven=(50, T0 + 3 * 86400)), T0 + 40)
st = tg.read_state(T0 + 50)
check("activity: the 5-hour number GREW with no reply here (spent elsewhere) -> bright", st["five_hour"]["active"] is True, st["five_hour"])
check("activity: ... and only that window: the week did not move -> dim", st["seven_day"]["active"] is False, st["seven_day"])
fresh()
tg.update(dict(rl(five=(40, T0 + 7200), seven=(50, T0 + 3 * 86400)), model={"id": "claude-opus-5-5"}), T0, response_at=T0 - 5)
st = tg.read_state(T0)
check("activity: a reply in this window -> both plan windows bright (the account is being spent)",
      st["five_hour"]["active"] is True and st["seven_day"]["active"] is True, st)
d, _ = tg._read()
check("activity: a reply on Opus does not mark Fable", d["active"].get("fable") is None and d["active"]["any"] == T0 - 5, d.get("active"))
check("activity: 900 s after the last spend -> still bright", tg.read_state(T0 - 5 + 900)["five_hour"]["active"] is True)
check("activity: 901 s after the last spend -> dim", tg.read_state(T0 - 5 + 901)["five_hour"]["active"] is False)
tg.update(dict(rl(five=(40, T0 + 7200)), model={"id": "claude-opus-5-5"}), T0 + 100, response_at=T0 - 300)
d, _ = tg._read()
check("activity: an idle window re-sending an OLDER reply never moves the mark back", d["active"]["any"] == T0 - 5, d["active"])
tg.update(dict(rl(five=(40, T0 + 7200)), model={"id": "claude-opus-5-5"}), T0 + 100, response_at=T0 + 100 + 61)
d, _ = tg._read()
check("activity: a reply time more than 60 s in the future is ignored", d["active"]["any"] == T0 - 5, d["active"])
tg.update(dict(rl(five=(40, T0 + 7200)), model={"id": "claude-opus-5-5"}), T0 + 100, response_at=T0 + 100 + 60)
d, _ = tg._read()
check("activity: 60 s ahead (clock skew) is taken, capped at now", d["active"]["any"] == T0 + 100, d["active"])
fresh()
tg.update(rl(five=(40, T0 + 7200)), T0, response_at=T0 - 5, fable_at=T0 - 5)
d, _ = tg._read()
check("activity: a Fable reply time marks Fable AND the account", d["active"].get("fable") == T0 - 5 and d["active"]["any"] == T0 - 5,
      d.get("active"))
fresh()
tg.update(dict(rl(five=(40, T0 + 7200)), model={"id": "claude-fable-5-1"}), T0, response_at=T0 - 5)
d, _ = tg._read()
check("lens 0.4 dim: a model SWITCH (Fable in the payload, last reply on another model) does not mark Fable",
      d["active"].get("fable") is None and d["active"]["any"] == T0 - 5, d.get("active"))
fresh()
tg.update({"model": {"id": "claude-opus-5-5"}}, T0, response_at=T0 - 5, fable_at=T0 - 5)
d, _ = tg._read()
check("lens 0.4 dim: a reply in a session with NO plan windows (API key) marks nothing", d.get("active") is None, d.get("active"))
fresh()
tg.update(rl(five=(40, T0 + 7200)), T0, response_at=T0 - 5, spent_at=T0 - 2)
d, _ = tg._read()
check("activity: spent_at (subagents included) is the mark when given", d["active"]["any"] == T0 - 2, d.get("active"))
# the model of the reply itself, read from the transcript (main and subagents)
_td = fresh()
_mp = os.path.join(_td, "main.jsonl")
os.makedirs(os.path.join(_td, "main", "subagents"))


def _reply(path, mid, ts, model):
    line = json.loads(msg(mid, 10, 100, 5, 1000))
    line["timestamp"] = ts
    line["message"]["model"] = model
    with open(path, "a") as f:
        f.write(json.dumps(line) + "\n")


_reply(_mp, "a1", "2027-01-15T08:00:00Z", "claude-opus-5-5")
_tk = tg.session_tokens(_mp, "sx")
check("transcript: a main reply on Opus -> spend time, no Fable time", _tk["last_spend_at"] == NOW and _tk["last_fable_at"] is None, _tk)
_reply(os.path.join(_td, "main", "subagents", "agent-1.jsonl"), "s1", "2027-01-15T08:01:00Z", "claude-fable-5-1")
_tk = tg.session_tokens(_mp, "sx")
check("transcript: a SUBAGENT reply on Fable -> Fable time and the later spend time",
      _tk["last_fable_at"] == NOW + 60 and _tk["last_spend_at"] == NOW + 60 and _tk["last_reply_at"] == NOW, _tk)
_reply(_mp, "a2", "2027-01-15T08:02:00Z", "CLAUDE-FABLE-5-1[1m]")
_tk = tg.session_tokens(_mp, "sx")
check("transcript: a main reply on Fable (any letter case) -> Fable time", _tk["last_fable_at"] == NOW + 120, _tk)
_reply(_mp, "a3", "2027-01-15T08:03:00Z", 5)
_tk = tg.session_tokens(_mp, "sx")
check("transcript: a model that is not text -> not Fable, the reply still counts", _tk["last_fable_at"] == NOW + 120
      and _tk["last_spend_at"] == NOW + 180, _tk)
# Fable from the state: bright when a Fable reply is recent or its share moved between two reads
TF = NOW
fresh()
tg.update(rl(five=(40, TF + 7200), seven=(50, TF + 3 * 86400)), TF)
tg.probe_usage(TF, runner=lambda: "Current week (Fable): 4% used · resets Jan 18\nCurrent week (all models): 50% used · resets Jan 18\n")
st = tg.read_state(TF + 10)
check("Fable: first read, no Fable reply -> fresh, dim", st["fable"]["status"] == "fresh" and st["fable"]["active"] is False, st["fable"])
tg.probe_usage(TF + 600, runner=lambda: "Current week (Fable): 4% used · resets Jan 18\nCurrent week (all models): 50% used · resets Jan 18\n")
check("Fable: the same share on the next read -> still dim", tg.read_state(TF + 610)["fable"]["active"] is False)
tg.probe_usage(TF + 1200, runner=lambda: "Current week (Fable): 5% used · resets Jan 18\nCurrent week (all models): 51% used · resets Jan 18\n")
st = tg.read_state(TF + 1210)
check("Fable: the share moved -> bright (spent somewhere, any window or machine)", st["fable"]["active"] is True, st["fable"])
tg.probe_usage(TF + 1800, runner=lambda: "Current week (Fable): 5% used · resets Jan 18\nCurrent week (all models): 51% used · resets Jan 18\n")
d, _ = tg._read()
check("Fable: an unchanged read keeps the time of the last move", d["fable"].get("changed_at") == TF + 1200, d["fable"])
check("Fable: 900 s after the move -> bright; 901 -> dim",
      tg.read_state(TF + 2100)["fable"]["active"] is True and tg.read_state(TF + 2101)["fable"]["active"] is False)
tg.update(rl(five=(41, TF + 7200)), TF + 3000, response_at=TF + 2990, fable_at=TF + 2990)
check("Fable: a reply on Fable -> bright at once, without waiting for the next read",
      tg.read_state(TF + 3000)["fable"]["active"] is True, tg.read_state(TF + 3000)["fable"])
_U = "Current week (Fable): %d%% used · resets Jan 18\nCurrent week (all models): 50%% used · resets Jan 18\n"
fresh()
tg.update(rl(five=(40, TF + 7200), seven=(50, TF + 3 * 86400)), TF)
tg.probe_usage(TF, runner=lambda: _U % 80)
tg.probe_usage(TF + 600, runner=lambda: _U % 0)
check("lens 0.4 dim: Fable share DROPPED (a new week) -> not a spend, dim", tg.read_state(TF + 610)["fable"]["active"] is False)
fresh()
tg.update(rl(five=(40, TF + 7200), seven=(50, TF + 3 * 86400)), TF)
tg.probe_usage(TF, runner=lambda: _U % 4)
tg.probe_usage(TF + 1201, runner=lambda: _U % 6)
check("lens 0.4 dim: Fable grew over a gap > 2 x 600 s -> when it was spent is unknown, dim",
      tg.read_state(TF + 1210)["fable"]["active"] is False)
fresh()
tg.update(rl(five=(40, TF + 7200), seven=(50, TF + 3 * 86400)), TF)
tg.probe_usage(TF, runner=lambda: _U % 4)
tg.probe_usage(TF + 1200, runner=lambda: _U % 6)
check("lens 0.4 dim: Fable grew over a gap of exactly 2 x 600 s -> bright", tg.read_state(TF + 1210)["fable"]["active"] is True)
fresh()
tg.update(rl(five=(40, TF + 7200)), TF - 100)
tg.update(rl(five=(41, TF + 7200)), TF - 10)
d, _ = tg._read()
d["active"] = {"any": TF + 3600}
tg._save(d)
check("lens 0.4 dim: a mark from the future (clock set back) does not mask a real growth 10 s ago",
      tg.read_state(TF)["five_hour"]["active"] is True, tg.read_state(TF)["five_hour"])
fresh()
tg.update(rl(five=(40, TF + 7200)), TF, response_at=TF - 5)
d, _ = tg._read()
d["active"] = "damaged"
d["changed_at"] = ["damaged"]
tg._save(d)
st = tg.read_state(TF)
check("activity: a damaged record -> dim, never an exception", st["five_hour"]["active"] is False, st["five_hour"])
tg.update(dict(rl(five=(41, TF + 7200))), TF + 10, response_at=TF + 5)
d, _ = tg._read()
check("activity: the next reply rewrites a damaged record", d["active"] == {"any": TF + 5} and isinstance(d["changed_at"], dict), d)

# end to end: two windows of one account share the brightness
fresh()
_dir = tg.HOME


def _tx(name, with_reply):
    p = os.path.join(_dir, name + ".jsonl")
    with open(p, "w") as f:
        if with_reply:
            line = json.loads(msg("m-" + name, 10, 100, 5, 1000))
            line["timestamp"] = "2027-01-15T08:00:00Z"  # = NOW
            f.write(json.dumps(line) + "\n")
        else:
            f.write(json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n")
    return p


_rl = {"five_hour": {"used_percentage": 40, "resets_at": NOW + 7200}, "seven_day": {"used_percentage": 50, "resets_at": NOW + 3 * 86400}}
with dimenv():
    _quiet = tg.statusline({"transcript_path": _tx("quiet", False), "session_id": "quiet", "rate_limits": _rl,
                            "model": {"id": "claude-opus-5-5"}}, NOW + 30)
    check("e2e: before any reply on the account -> plan windows dim", _quiet.startswith(dimmed("1h 40%")), _quiet)
    _busy = tg.statusline({"transcript_path": _tx("busy", True), "session_id": "busy", "rate_limits": _rl,
                           "model": {"id": "claude-opus-5-5"}}, NOW + 40)
    check("e2e: a reply in one window -> that window bright", _busy.startswith("1h 40% 2d 50%"), _busy)
    _quiet = tg.statusline({"transcript_path": _tx("quiet", False), "session_id": "quiet", "rate_limits": _rl,
                            "model": {"id": "claude-opus-5-5"}}, NOW + 50)
    check("e2e: ... and the OTHER window of the same account bright too (one account, one brightness)",
          _quiet.startswith("1h 40% 2d 50%"), _quiet)
    _quiet = tg.statusline({"transcript_path": _tx("quiet", False), "session_id": "quiet", "rate_limits": _rl,
                            "model": {"id": "claude-opus-5-5"}}, NOW + 902)
    check("e2e: 15 minutes with no reply anywhere -> both groups dim again",
          _quiet.startswith(dimmed("1h 40%") + " " + dimmed("2d 50%")), _quiet)

# one state folder per account (0.4)
_u = os.path.expanduser("~")
_base = os.path.join(_u, ".truegauge")
check("home: TRUEGAUGE_HOME wins over CLAUDE_CONFIG_DIR",
      tg._home({"TRUEGAUGE_HOME": "X:/own", "CLAUDE_CONFIG_DIR": "Y:/other"}) == "X:/own")
check("home: no CLAUDE_CONFIG_DIR -> ~/.truegauge", tg._home({}) == _base, tg._home({}))
check("home: CLAUDE_CONFIG_DIR empty or spaces -> ~/.truegauge", tg._home({"CLAUDE_CONFIG_DIR": "  "}) == _base)
check("home: CLAUDE_CONFIG_DIR = the default ~/.claude -> ~/.truegauge (the default login is not split)",
      tg._home({"CLAUDE_CONFIG_DIR": os.path.join(_u, ".claude")}) == _base
      and tg._home({"CLAUDE_CONFIG_DIR": os.path.join(_u, ".claude") + os.sep}) == _base)
_a = tg._home({"CLAUDE_CONFIG_DIR": os.path.join(ROOT, "acct_a")})
_b = tg._home({"CLAUDE_CONFIG_DIR": os.path.join(ROOT, "acct_b")})
check("home: another settings folder -> its own state folder under ~/.truegauge/cfg-<8 hex>",
      os.path.dirname(_a) == _base and os.path.basename(_a).startswith("cfg-") and len(os.path.basename(_a)) == 12, _a)
check("home: two settings folders -> two state folders", _a != _b, (_a, _b))
check("home: the same folder written differently -> the same state folder",
      tg._home({"CLAUDE_CONFIG_DIR": os.path.join(ROOT, "acct_a") + os.sep}) == _a
      and tg._home({"CLAUDE_CONFIG_DIR": os.path.join(ROOT, ".", "acct_a")}) == _a)
if os.name == "nt":
    check("home: on Windows the letter case of the folder does not split one account",
          tg._home({"CLAUDE_CONFIG_DIR": os.path.join(ROOT, "ACCT_A")}) == _a)
check("home: the folder name is the same on every run (not random)", _a == tg._home({"CLAUDE_CONFIG_DIR": os.path.join(ROOT, "acct_a")}))
fresh()
tg.update(dict(rl(five=(40, int(time.time()) + 7200)), model={"id": "claude-opus-5-5"}), time.time(), response_at=time.time() - 5)
with dimenv():
    _out = cli("--show")[1]
check("--show: the brightness of each window and the time of the last spend",
      "five_hour: being spent now: yes (last spend seen: " in _out and "bright for 900 s after it" in _out, _out)
check("--show: the dim colour of this shell", "dim colour: ESC[38;2;98;98;98m" in _out, _out)
_old_home = os.environ.pop("TRUEGAUGE_HOME")
try:
    _out = cli("--show", cfg=os.path.join(ROOT, "acct_a"))[1]
    check("--show: another settings folder -> says it has its own state folder",
          "account: CLAUDE_CONFIG_DIR=%s -> its own state folder" % os.path.join(ROOT, "acct_a") in _out, _out)
    _out = cli("--show", cfg=os.path.join(_u, ".claude"))[1]
    check("--show: the default settings folder -> says so", "-> the default state folder" in _out, _out)
    _old_cfg = os.environ.pop("CLAUDE_CONFIG_DIR", None)
    try:
        with contextlib.redirect_stdout(io.StringIO()) as _buf:
            tg.show()
        check("--show: no CLAUDE_CONFIG_DIR in the shell -> says it reads the default folder and why it may differ",
              "account: no CLAUDE_CONFIG_DIR in this shell -> the default state folder" in _buf.getvalue(), _buf.getvalue())
    finally:
        if _old_cfg is not None:
            os.environ["CLAUDE_CONFIG_DIR"] = _old_cfg
finally:
    os.environ["TRUEGAUGE_HOME"] = _old_home

fresh()
with dimenv():
    tg._remember_render_cfg(tg.color_config(), time.time())
_d, _ = tg._read()
check("saved settings: the dim colour the status line used is kept", tg._saved_render_cfg(_d)[0]["dim_sgr"] == DIM, _d.get("render_cfg"))
_d["render_cfg"]["dim_sgr"] = 5
check("saved settings: a saved dim colour of the wrong type -> the record is not believed", tg._saved_render_cfg(_d) == (None, None))
_d["render_cfg"]["dim_sgr"] = None
check("saved settings: dim off (None) is a valid record", tg._saved_render_cfg(_d)[0] is not None)

fresh()
tg.update(rl(five=(40, NOW + 7200)), NOW, response_at=NOW - 5)
_d, _ = tg._read()
_d["last_error"] = {"at": NOW + 1, "msg": "test fault"}  # an unresolved tap error makes the reading stale_reading
tg._save(_d)
_st = tg.read_state(NOW + 10)
check("lens 0.4 dim: an OLD reading stays dim even while the account is being spent (spend 15 s ago, stale)",
      _st["five_hour"]["status"] == "stale_reading" and _st["five_hour"]["active"] is False, _st["five_hour"])

# stand 2026-10-15: a fault of an EXTRA, recorded in the same redraw, must not mark live numbers old (dim, rounded down);
# a fault about the readings still does. Neighbours: the same moment, extra vs not.
for _extra, _want in ((True, "fresh"), (False, "stale_reading")):
    fresh()
    _t = time.time()
    tg.update(rl(five=(96.6, int(_t) + 7200)), _t, response_at=_t - 5)
    tg._record_error("colour setting refused (default used): TRUEGAUGE_RED_5H=97.5", extra=_extra)
    _st = tg.read_state(time.time())
    check("stand 2026-10-15: a recorded fault (extra=%s) -> the fresh reading is %s" % (_extra, _want),
          _st["five_hour"]["status"] == _want, _st["five_hour"])
    if _extra:
        check("stand 2026-10-15: ... it stays bright (being spent) and is not rounded down",
              _st["five_hour"]["active"] is True and tg._used(_st["five_hour"], False) == "97%", _st["five_hour"])

# the separate settings recorder (_remember_render_cfg) files a refusal as a fault of an extra too (mutant 2026-10-15)
fresh()
os.environ["TRUEGAUGE_RED_5H"] = "95%"
try:
    tg._remember_render_cfg(tg.color_config(), time.time())
finally:
    os.environ.pop("TRUEGAUGE_RED_5H", None)
check("stand 2026-10-15: _remember_render_cfg files a refusal under last_extra_error, not last_error",
      "colour setting refused" in str((state_now().get("last_extra_error") or {}).get("msg")) and "last_error" not in state_now(),
      state_now())

print("\n%d failed" % len(fails) if fails else "\nALL PASS")
sys.exit(1 if fails else 0)
