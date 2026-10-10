"""Question: does truegauge ever show an old, void or replayed number as a current one, count a token twice,
or print a wrong time left at the edges of its thresholds?
Every case runs in a NEW temporary state dir (the live state is never touched). Run: python tests/test_truegauge.py"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

# The REAL user's places, computed (never opened) BEFORE the environment is replaced.
REAL_HOME = os.path.expanduser("~")
REAL_PROTECTED = [os.path.join(REAL_HOME, ".claude"), os.path.join(REAL_HOME, ".truegauge")]
ROOT = tempfile.mkdtemp(prefix="tg_test_")
SANDBOX = os.path.join(ROOT, "sandbox_home")
os.makedirs(os.path.join(SANDBOX, ".claude"))
# The whole run lives in temporary places: whatever default truegauge computes lands inside ROOT.
for _k, _v in (("HOME", SANDBOX), ("USERPROFILE", SANDBOX), ("CLAUDE_CONFIG_DIR", os.path.join(SANDBOX, ".claude")),
               ("TRUEGAUGE_HOME", os.path.join(SANDBOX, ".truegauge"))):
    os.environ[_k] = _v
# Colour settings of the person running the tests must not reach the checks (a TRUEGAUGE_RED_5H=50 in their shell
# would turn every 0.1 check at >= 50% into a false FAIL); the colour checks set what they need themselves.
for _k in ("TRUEGAUGE_RED_5H", "TRUEGAUGE_RED_7D", "TRUEGAUGE_RED_COLOR", "TRUEGAUGE_COLD_COLOR", "TRUEGAUGE_MARK", "NO_COLOR"):
    os.environ.pop(_k, None)
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
check("expired window is left out, not the old number and not a guessed 0%", tg.render({"five_hour": s}, None, None) == "",
      tg.render({"five_hour": s}, None, None))

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
calls = []
tg.maybe_start_probe(NOW, spawn=lambda: calls.append(1))
tg.maybe_start_probe(NOW + 599, spawn=lambda: calls.append(1))
check("second probe within 599 s not started", len(calls) == 1, calls)
tg.maybe_start_probe(NOW + 600, spawn=lambda: calls.append(1))
check("probe at 600 s started", len(calls) == 2, calls)
os.environ["TRUEGAUGE_FABLE"] = "0"
check("TRUEGAUGE_FABLE=0 -> never", tg.maybe_start_probe(NOW + 99999, spawn=lambda: calls.append(1)) is False)
del os.environ["TRUEGAUGE_FABLE"]


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
calls = []
tg.maybe_start_probe(NOW, spawn=lambda: calls.append(1))
tg.maybe_start_probe(NOW + 200, spawn=lambda: calls.append(1))
check("unfinished probe blocks a new one for 300 s", len(calls) == 1, calls)
tg.maybe_start_probe(NOW + 300, spawn=lambda: calls.append(1))
check("after 300 s a hung probe no longer blocks", len(calls) == 2, calls)
del os.environ["TRUEGAUGE_PROBE_S"]

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
check("expired windows are left out", got == "", got)
got = R({"five_hour": win("expired", 90, -5), "seven_day": win("fresh", 54, 3600)})
check("week in its last day with the 5h window absent cannot pass for the 5h group", got == "0d 54%", got)
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
check("install: key order kept (statusLine stays in place)", list(s) == ["model", "statusLine", "env", "permissions"], list(s))
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

COLOR_VARS = ("NO_COLOR", "TRUEGAUGE_RED_5H", "TRUEGAUGE_RED_7D", "TRUEGAUGE_RED_COLOR", "TRUEGAUGE_COLD_COLOR", "TRUEGAUGE_MARK")
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
    check("red: expired window -> no group at all", g5(99.9, "expired", -5) == "" and g7(99.9, "expired", -5) == "")
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
check("version is 0.2.0 or later (0.3.0 is pinned in the 0.3 block)", tg.__version__ in ("0.2.0", "0.3.0"))

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


def both(s):
    return (tg.render(s[0], s[1], s[2][0], s[3], s[2][1]), ref.render(s[0], s[1], s[2][0], s[3], s[2][1]))


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

    def spy(msg):
        calls.append(msg)
        return orig(msg)
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
check("SF1: the error reaches last_error in the state", "colour setting refused" in state_now().get("last_error", {}).get("msg", ""))
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
      "five_hour: past the red threshold: yes" in out and "unknown" not in out, out)
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

# ======================================================================== 0.3: amber context group when the main session's cache is cold
# (table of boundaries: 01_PROEKT.md section 2; now = 1000000)
AMB = "\033[1;38;2;255;175;0m"
UL, ULX = "\033[4m", "\033[24m"
CNOW = 1_000_000
CTX = "T548K 55%"


def amber(text):
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
    check("cold: observed, warm, expires_at = now -> AMBER (the moment it cools)", ctxline(PC(exp=CNOW)) == amber(CTX), ctxline(PC(exp=CNOW)))
    check("cold: observed, warm, expires_at = now-1 -> AMBER", ctxline(PC(exp=CNOW - 1)) == amber(CTX), ctxline(PC(exp=CNOW - 1)))
    check("cold: observed, warm=false, expires_at=null -> AMBER", ctxline(PC(warm=False, exp=None)) == amber(CTX))
    check("cold: observed, warm=false, expires_at=now+3600 -> AMBER (warm wins over a future time)",
          ctxline(PC(warm=False, exp=CNOW + 3600)) == amber(CTX))
    check("cold: observed=false, warm=false -> plain (nothing was cached, nothing cooled)", ctxline(PC(observed=False, warm=False)) == CTX)
    check("cold: observed missing, warm=false -> plain", ctxline(PC(observed=ABSENT, warm=False)) == CTX)
    check("cold: observed='true' (a string), warm=false -> plain", ctxline(PC(observed="true", warm=False)) == CTX)
    check("cold: observed=1 (a number), warm=false -> plain", ctxline(PC(observed=1, warm=False)) == CTX)
    check("cold: observed=true, warm='false' (a string), expires_at=now+10 -> plain", ctxline(PC(warm="false", exp=CNOW + 10)) == CTX)
    check("cold: observed=true, warm='false', expires_at=now-10 -> AMBER (by the time)", ctxline(PC(warm="false", exp=CNOW - 10)) == amber(CTX))
    check("cold: warm=0 (a number, not False) with a past time -> by the time only", ctxline(PC(warm=0, exp=CNOW - 10)) == amber(CTX)
          and ctxline(PC(warm=0, exp=CNOW + 10)) == CTX)
    check("cold: observed, warm=true, expires_at=null -> plain", ctxline(PC(exp=None)) == CTX)
    check("cold: warm absent, expires_at in the past -> AMBER", ctxline(PC(warm=ABSENT, exp=CNOW - 1)) == amber(CTX))
    check("cold: warm absent, expires_at absent -> plain", ctxline(PC(warm=ABSENT, exp=ABSENT)) == CTX)
    for bad in ("1000", "999999", True, False, [1], {"a": 1}, float("nan"), float("inf"), float("-inf")):
        check("cold: observed, warm=true, expires_at=%r -> plain (not a number, not judged)" % (bad,), ctxline(PC(exp=bad)) == CTX)
    check("cold: expires_at as a float in the past -> AMBER", ctxline(PC(exp=CNOW - 0.5)) == amber(CTX))

    # ---- what is painted (rule 1)
    check("cold, no T and no percent (0 tokens) -> nothing painted, line empty",
          tg.render({}, None, None, "en", 0, cold=True) == "")
    check("cold, 0 tokens with a percent of 0 -> nothing painted, no reading",
          tg.render({}, None, 0, "en", 0, cold=True) == "")
    base = tg.render({"five_hour": win("fresh", 38, 14400)}, {"new": 2600000, "complete": True}, None, "en", 0, cold=False)
    check("cold, no reading: the line equals the line with a warm cache",
          tg.render({"five_hour": win("fresh", 38, 14400)}, {"new": 2600000, "complete": True}, None, "en", 0, cold=True) == base, base)
    check("cold, only a percent (no T) -> only the percent is painted", tg.render({}, None, 55.2, "en", None, cold=True) == amber("55%"))
    check("cold, only T (no percent) -> only T is painted", tg.render({}, None, None, "en", 548200, cold=True) == amber("T548K"))
    check("cold, both -> one sequence around both with the space inside",
          tg.render({}, None, 55.2, "en", 548200, cold=True) == AMB + "T548K 55%" + RS)
    full = tg.render({"five_hour": win("fresh", 98.2, 7200), "fable": win("fresh", 4, None)},
                     {"new": 2600000, "complete": True}, 55.2, "en", 548200, cold=True)
    check("cold + 5h past the red threshold: 5h red, context amber, plain spaces between, F and Σ without colour",
          full == red_of("2h 98%") + " F4% " + amber(CTX) + " Σ2.6M", full)
    check("cold: Σ is never painted (2 sequences starts, 2 resets)", full.count("\x1b[1;38") == 2 and full.count(RS) == 2, full)
    full_ru = tg.render({}, {"new": 2600000, "complete": True}, 55.2, "ru", 548200, cold=True)
    check("cold, ru words: amber around Т548К 55%, Σ outside", full_ru == amber("Т548К 55%") + " Σ2.6М", full_ru)
    check("cold group sits between Fable and Σ (order of groups unchanged)",
          full.index("F4%") < full.index(AMB) < full.index("Σ"))
    check("a warm cache: the full line has no amber at all",
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
    check("TRUEGAUGE_COLD_COLOR=ansi -> bold yellow", tg.render({}, None, 55.2, "en", 548200, cold=True) == "\033[1;33m" + CTX + RS)
with envset(TRUEGAUGE_COLD_COLOR="ANSI"):
    check("TRUEGAUGE_COLD_COLOR=ANSI -> bold yellow", tg.render({}, None, 55.2, "en", 548200, cold=True) == "\033[1;33m" + CTX + RS)
for val in ("#FFAF00", "#ffaf00", " #ffaf00 "):
    with envset(TRUEGAUGE_COLD_COLOR=val):
        check("TRUEGAUGE_COLD_COLOR=%r -> accepted (amber)" % val, tg.render({}, None, 55.2, "en", 548200, cold=True) == amber(CTX))
    check("TRUEGAUGE_COLD_COLOR=%r -> --show silent" % val, "REFUSED" not in shown({"TRUEGAUGE_COLD_COLOR": val}))
with envset(TRUEGAUGE_COLD_COLOR="#00ff7f"):
    check("TRUEGAUGE_COLD_COLOR=#00ff7f -> that colour, bold",
          tg.render({}, None, 55.2, "en", 548200, cold=True) == "\033[1;38;2;0;255;127m" + CTX + RS)
for val in ("yellow", "#ffa", "#gg0000", "#ffaf000", "ffaf00", "33", "of"):
    with envset(TRUEGAUGE_COLD_COLOR=val):
        got = tg.render({}, None, 55.2, "en", 548200, cold=True)
    out = shown({"TRUEGAUGE_COLD_COLOR": val})
    check("TRUEGAUGE_COLD_COLOR=%r -> default amber used" % val, got == amber(CTX), got)
    check("TRUEGAUGE_COLD_COLOR=%r -> --show names the refusal" % val, "REFUSED" in out and "TRUEGAUGE_COLD_COLOR" in out, out)
for blank in ("", "  ", "\t"):
    with envset(TRUEGAUGE_COLD_COLOR=blank):
        got = tg.render({}, None, 55.2, "en", 548200, cold=True)
    check("TRUEGAUGE_COLD_COLOR=%r -> default amber, --show silent" % blank,
          got == amber(CTX) and "REFUSED" not in shown({"TRUEGAUGE_COLD_COLOR": blank}), got)
with envset(TRUEGAUGE_RED_5H="off"):
    got = tg.render({"five_hour": win("fresh", 100, 7200)}, None, 55.2, "en", 548200, cold=True)
    check("red threshold off + cold: no red, amber present (settings are independent)", got == "2h 100% " + amber(CTX), got)
with envset(TRUEGAUGE_COLD_COLOR="off"):
    got = tg.render({"five_hour": win("fresh", 100, 7200)}, None, 55.2, "en", 548200, cold=True)
    check("cold colour off + red window: red stays (settings are independent)", got == red_of("2h 100%") + " " + CTX, got)
with envset(TRUEGAUGE_RED_COLOR="ansi"):
    got = tg.render({"five_hour": win("fresh", 100, 7200)}, None, 55.2, "en", 548200, cold=True)
    check("red colour setting does not change the amber one", got == "\033[1;31m2h 100%" + RS + " " + amber(CTX), got)

# ---- the mark without colour (rule 8a): TRUEGAUGE_MARK = list of underline, braces, bold | all | off
BO, BOX = "\033[1m", "\033[22m"  # bold on / bold off (no colour in the line)
with envset():
    five = {"five_hour": win("fresh", 98.2, 7200)}
    both_w = {"five_hour": win("fresh", 98.2, 7200), "seven_day": win("fresh", 99.9, 3 * 86400), "fable": win("fresh", 100, None)}
    tk = {"new": 2600000, "complete": True}
    plain_all = tg.render(both_w, tk, 55.2, "en", 548200, cold=True)
    check("mark: unset -> no mark anywhere, line is the colour-only line",
          plain_all == red_of("2h 98%") + " " + red_of("3d 100%") + " F100% " + amber(CTX) + " Σ2.6M" and "{" not in plain_all and UL not in plain_all and BO not in plain_all, plain_all)
with envset(TRUEGAUGE_MARK=""):
    check("mark: empty -> off, silently", tg.render(both_w, tk, 55.2, "en", 548200, cold=True) == plain_all
          and "REFUSED" not in shown({"TRUEGAUGE_MARK": ""}))
# each element alone (with colour)
with envset(TRUEGAUGE_MARK="underline"):
    got = tg.render(five, None, None, "en", None)
    check("mark=underline, 5h past the threshold: colour, underline on, text, underline off, colour reset",
          got == RED + UL + "2h 98%" + ULX + RS, got)
    got = tg.render({}, None, 55.2, "en", 548200, cold=True)
    check("mark=underline, cold: amber, underline around the whole context group",
          got == AMB + UL + CTX + ULX + RS, got)
    check("mark=underline: a group not past the threshold has no mark", tg.render({"five_hour": win("fresh", 38, 7200)}, None, None, "en", None) == "2h 38%")
with envset(TRUEGAUGE_MARK="braces"):
    got = tg.render({}, None, 55.2, "en", 548200, cold=True)
    check("mark=braces, cold: {T548K 55%} inside the amber", got == AMB + "{" + CTX + "}" + RS, got)
    got = tg.render(five, None, None, "en", None)
    check("mark=braces, 5h red: {2h 98%} inside the red, no underline, no square bracket",
          got == RED + "{2h 98%}" + RS and UL not in got and "[" not in SGR_RX.sub("", got) and "]" not in got, got)
with envset(TRUEGAUGE_MARK="bold"):
    got = tg.render(five, None, None, "en", None)
    check("mark=bold + colour: the colour already carries bold -> line equals the colour-only line (bold not doubled)",
          got == RED + "2h 98%" + RS and BO not in got and BOX not in got, got)
    got = tg.render({}, None, 55.2, "en", 548200, cold=True)
    check("mark=bold + colour, cold: amber only, bold not doubled", got == AMB + CTX + RS and BO not in got, got)
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
    check("mark=underline: an expired window has no group, so no mark", tg.render({"five_hour": win("expired", 99.9, -5)}, None, None, "en", None) == "")
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
check("--show: the mark line (this shell)", "mark: underline,braces,bold" in out and "cold colour: ESC[1;33m" in out, out)
out = shown({"TRUEGAUGE_MARK": " Bold, underline "})
check("--show: the mark line shows the canonical list", "mark: underline,bold" in out, out)
out = shown({})
check("--show: defaults are amber and mark off", "cold colour: ESC[1;38;2;255;175;0m; mark: off" in out, out)
fresh()
_n = time.time()
tg.update(rl(five=(97.6, int(_n) + 7200)), _n)
check("--show: no record of prompt_cache -> 'cache: no record yet'", "cache: no record yet" in cli("--show")[1], cli("--show")[1])
with envset(TRUEGAUGE_MARK="underline"):
    tg._remember_render_cfg(tg.color_config(), _n)
    out = cli("--show")[1]
check("--show: the status line's own mark and cold colour are shown with its settings",
      "status line cold colour: ESC[1;38;2;255;175;0m; mark: underline" in out, out)
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
check("cache record: --show says present=False and cold now: no",
      "present=False" in cli("--show")[1] and "cold now: no" in cli("--show")[1], cli("--show")[1])
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
check("statusline: expires_at one second before `now` -> amber context group", AMB + CTX + RS in out1, out1)
fresh()
out2 = sl_pc(dict(pcx, expires_at=int(_n) + 1), _n)
check("statusline: expires_at one second after `now` -> no amber", AMB not in out2 and CTX in out2, out2)
fresh()
out3 = sl_pc(dict(pcx, warm=False), _n)
check("statusline: warm=false -> amber", AMB + CTX + RS in out3, out3)
fresh()
out4 = sl_pc(ABSENT, _n)
check("statusline: no prompt_cache -> no amber", AMB not in out4 and CTX in out4, out4)
check("statusline: amber is the only difference between cold and warm lines", SGR_RX.sub("", out1) == SGR_RX.sub("", out2) == SGR_RX.sub("", out4))

# SF4 for the new code: a fault costs the colour or the record, never the line
fresh()
_n = time.time()
for what in ("_cache_cold", "_decorate", "_remember_cache", "_cold_sgr", "_mark"):
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
check("SF4 (0.3): the cold test failing means not cold (no amber, no error escape)", AMB not in line and CTX in line, line)

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
    return (tg.render(s[0], s[1], s[2][0], s[3], s[2][1], cold=cold), ref2.render(s[0], s[1], s[2][0], s[3], s[2][1]))


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
check("end to end: a cold payload really prints an amber context group", (AMB + CTX + RS) in _o.decode("utf-8"), _o)
_c, _o = run_script(os.path.join(os.path.dirname(HERE), "truegauge.py"),
                    dict(PAYLOADS3[0], prompt_cache={"caching_observed": True, "warm": False}), {"TRUEGAUGE_MARK": "all"})
check("end to end: TRUEGAUGE_MARK=all from the environment marks the red windows and the cold context group",
      _o.decode("utf-8").count(UL) == 3 and _o.decode("utf-8").count("{") == 3 and _o.decode("utf-8").count("}") == 3, _o)
check("version is 0.3.0", tg.__version__ == "0.3.0")

print("\n%d failed" % len(fails) if fails else "\nALL PASS")
sys.exit(1 if fails else 0)
