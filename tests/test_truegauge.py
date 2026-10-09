"""Question: does truegauge ever show an old, void or replayed number as a current one, count a token twice,
or print a wrong time left at the edges of its thresholds?
Every case runs in a NEW temporary state dir (the live state is never touched). Run: python tests/test_truegauge.py"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import truegauge as tg  # noqa: E402

ROOT = tempfile.mkdtemp(prefix="tg_test_")
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
check("expired renders a full window at 0%, not the old number", tg.render({"five_hour": s}, None, None) == "5h 0%",
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
check("expired Fable renders F0%", tg.render({"fable": s}, None, None) == "F0%")
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
# time left, week: >= 1 day -> floor days, < 1 day -> hours, < 1 h -> minutes
for rin, want in ((5 * 86400 + 7 * 3600, "5d"), (86400, "1d"), (86399, "23h"), (86399 - 59 * 60, "23h"),
                  (3600, "1h"), (3599, "59m"), (60, "1m")):
    got = R({"seven_day": win("fresh", 54, rin)})
    check("week %d s left -> %s 54%%" % (rin, want), got == want + " 54%", got)
# fresh / stale / expired for each window
got = R({"five_hour": win("stale_reading", 38.9, 14400)})
check("stale 5h: floor with >=, never above the reading", got == "4h ≥38%", got)
got = R({"five_hour": win("fresh", 38.5, 14400)})
check("fresh rounds half up", got == "4h 39%", got)
got = R({"five_hour": win("expired", 90, -5), "seven_day": win("expired", 80, -5)})
check("expired windows: full window, 0% used", got == "5h 0% 7d 0%", got)
got = R({"seven_day": win("stale_reading", 54.7, 3 * 86400)})
check("stale week", got == "3d ≥54%", got)
got = R({"fable": win("fresh", 4)})
check("Fable fresh", got == "F4%", got)
got = R({"fable": win("stale_reading", 4.9)})
check("Fable stale", got == "F≥4%", got)
got = R({"fable": {"status": "expired", "last_seen_used_percentage": 96, "remaining_percentage": None}})
check("Fable expired -> F0%", got == "F0%", got)
for status in ("unknown", "absent"):
    got = R({"fable": {"status": status}})
    check("Fable %s -> group omitted, not 0" % status, got == "", got)
check("no data at all -> empty line (no 0 groups)", R({}) == "" and R({}, None, None, "en", None) == "")
# tokens in the window now (Т) and session total (Σ), boundaries
for n, want in ((0, "T0"), (812, "T812"), (999, "T999"), (1000, "T1K"), (1499, "T1K"), (1500, "T2K"),
                (548_200, "T548K"), (999_499, "T999K"), (999_500, "T1.0M"), (1_000_000, "T1.0M"),
                (2_640_000, "T2.6M"), (2_650_000, "T2.7M"), (13_800_000, "T13.8M")):
    got = R({}, None, None, "en", n)
    check("window tokens %d -> %s" % (n, want), got == want, got)
    got = R({}, {"new": n, "complete": True})
    check("session total %d -> Σ%s" % (n, want[1:]), got == "Σ" + want[1:], got)
check("ru letters", R({}, {"new": 2_640_000, "complete": True}, None, "ru", 548_200) == "Т548К Σ2.6М",
      R({}, {"new": 2_640_000, "complete": True}, None, "ru", 548_200))
check("incomplete total -> Σ≥", R({}, {"new": 2_640_000, "complete": False}) == "Σ≥2.6M",
      R({}, {"new": 2_640_000, "complete": False}))
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

print("\n%d failed" % len(fails) if fails else "\nALL PASS")
sys.exit(1 if fails else 0)
