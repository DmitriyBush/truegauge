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

print("\n%d failed" % len(fails) if fails else "\nALL PASS")
sys.exit(1 if fails else 0)
