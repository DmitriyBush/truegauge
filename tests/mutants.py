"""Question: does the test suite go red when a guarded behaviour is broken? Each mutant edits a COPY of
truegauge.py in a temp dir and runs the suite against it. CAUGHT = suite red, SLIPPED = suite green (a hole).
Run: python tests/mutants.py"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "truegauge.py")
MUTANTS = [
    ("replay wins (merge >=)", "(new[\"resets_at\"], new[\"used_percentage\"]) > (old", "(new[\"resets_at\"], new[\"used_percentage\"]) >= (old"),
    ("last writer wins", "if not _valid(old) or (new", "if True or (new"),
    ("stale boundary off by one", "elif age > max_age_s:", "elif age >= max_age_s:"),
    ("stale shown as point value", "return \"<=%d%%\" % math.ceil(v[\"remaining_at_most\"])", "return \"%d%%\" % math.ceil(v[\"remaining_at_most\"])"),
    ("expired keeps old number", "if rst is not None and rst <= now:", "if rst is not None and rst < now:"),
    ("updated_at moves without change", "    elif changed:\n        d[\"updated_at\"]", "    elif True:\n        d[\"updated_at\"]"),
    ("split message counted twice", "ids[mid] = vals if old is None else [max(a, b) for a, b in zip(old, vals)]", "ids[mid] = vals if old is None else [a + b for a, b in zip(old, vals)]"),
    ("new week does not end the Fable reading", "if rst is not None and _valid(week) and week[\"resets_at\"] > rst + 3600:", "if False:"),
    ("old Fable bound shown without reset time", "if rst is None and v[\"status\"] == \"stale_reading\":", "if False:"),
    ("stale bound rounded down", "return \"<=%d%%\" % math.ceil(v[\"remaining_at_most\"])", "return \"<=%d%%\" % round(v[\"remaining_at_most\"])"),
    ("no-id messages merged", "st[\"noid\"] += 1", "pass"),
    ("brackets break subagent glob", "glob.escape(transcript_path[:-6])", "transcript_path[:-6]"),
    ("incomplete read shown as complete", "        if not st.get(\"at_eof\", True):", "        if False:"),
    ("hung probe does not block", "if started > done and now - started < 300:", "if False:"),
    ("window not sent stays fresh", "if r is None or now - r > max_age_s:", "if False:"),
    ("damaged state gives a fresh first point", "    if changed and problem == \"corrupt\":", "    if False:"),
    ("probe wipes unreadable state", "raise OSError(\"state file stayed unreadable; Fable reading not stored, state kept\")", "pass"),
    ("Fable without reset keeps 2x age", "max_age_s * 2 if rst is not None else max_age_s", "max_age_s * 2"),
    ("eof judged from the cap", "st[\"at_eof\"] = len(chunk) < READ_CAP", "st[\"at_eof\"] = True"),
    ("bad lines ignored", "complete = False  # an unreadable line may have carried tokens", "pass  # an unreadable line may have carried tokens"),
    ("missing transcript reads as 0", "        return None  # a wrong transcript_path", "        pass  # a wrong transcript_path"),
    ("partial line read", "end = chunk.rfind(b\"\\n\")", "end = len(chunk) - 1"),
    ("subagents ignored", "sorted(glob.glob(os.path.join(glob.escape(transcript_path[:-6]), \"subagents\", \"*.jsonl\")))", "[]"),
    ("Fable kept when line absent", "d[\"fable\"] = {\"absent\": True, \"read_at\": int(now)}", "pass"),
    ("Fable range unchecked", "if not 0 <= pct <= 100:", "if False:"),
    ("probe interval ignored", "if now - max(started, done) < every:", "if False:"),
    # "ms epoch accepted" (EPOCH_MAX 1e11 -> 1e13) is an EQUIVALENT mutant: the too-far-ahead check rejects a
    # millisecond resets_at as well (measured 2026-10-09: suite stays green, behaviour unchanged). Not listed.
    ("fmt_left hours boundary", "    if h:\n", "    if h > 1:\n"),
]

src = open(SRC, encoding="utf-8").read()
slipped = 0
for name, old, new in MUTANTS:
    if src.count(old) != 1:
        print("BLIND  %s (pattern found %d times -- mutant not applied)" % (name, src.count(old)))
        slipped += 1
        continue
    d = tempfile.mkdtemp(prefix="tg_mut_")
    os.makedirs(os.path.join(d, "tests"))
    open(os.path.join(d, "truegauge.py"), "w", encoding="utf-8").write(src.replace(old, new))
    shutil.copy(os.path.join(HERE, "test_truegauge.py"), os.path.join(d, "tests"))
    r = subprocess.run([sys.executable, os.path.join(d, "tests", "test_truegauge.py")], capture_output=True, text=True)
    ok = r.returncode != 0
    slipped += 0 if ok else 1
    print(("CAUGHT " if ok else "SLIPPED ") + name)
print("\n%d of %d caught" % (len(MUTANTS) - slipped, len(MUTANTS)))
sys.exit(1 if slipped else 0)
