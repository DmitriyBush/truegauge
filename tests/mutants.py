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
    ("stale shown as point value", "return \"%s%d%%\" % (GE, math.floor(v[\"used_percentage\"]))", "return \"%d%%\" % math.floor(v[\"used_percentage\"])"),
    ("expired keeps old number", "if rst is not None and rst <= now:", "if rst is not None and rst < now:"),
    ("updated_at moves without change", "    elif changed:\n        d[\"updated_at\"]", "    elif True:\n        d[\"updated_at\"]"),
    ("split message counted twice", "ids[mid] = vals if old is None else [max(a, b) for a, b in zip(old, vals)]", "ids[mid] = vals if old is None else [a + b for a, b in zip(old, vals)]"),
    ("new week does not end the Fable reading", "if rst is not None and _valid(week) and week[\"resets_at\"] > rst + 3600:", "if False:"),
    ("old Fable bound shown without reset time", "if rst is None and v[\"status\"] == \"stale_reading\":", "if False:"),
    ("stale floor -> round (value above the reading)", "(GE, math.floor(v[\"used_percentage\"]))", "(GE, _half_up(v[\"used_percentage\"]))"),
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
    ("fmt_left hours boundary", "    if sec >= 3600:\n", "    if sec > 3600:\n"),
    ("fmt_left days boundary", "    if days_only or sec >= 86400:", "    if days_only or sec > 86400:"),
    ("double count: file key not normalized", "    return os.path.normcase(os.path.abspath(path))", "    return path"),
    ("double count: stored spellings not merged on load", "    rec, changed = _merge_spellings(rec)\n", "    changed = False\n"),
    ("double count: merge keeps the lagging record", "st.get(\"offset\", 0) > prev.get(\"offset\", 0)", "st.get(\"offset\", 0) < prev.get(\"offset\", 0)"),
    ("expired window shown", "if not v or v[\"status\"] not in (\"fresh\", \"stale_reading\"):", "if not v:"),
    ("expired Fable shown", "if fb and fb.get(\"status\") in (\"fresh\", \"stale_reading\"):  # expired", "if fb:  # expired"),
    ("week shown in hours in its last day", "days_only=(key == \"seven_day\")", "days_only=False"),
    ("reply time ignored", "    ra = _num(response_at)\n", "    ra = None\n"),
    ("reply from the future accepted", "ra <= now + 60 and ", ""),
    ("reply time not recorded", "            st[\"last_reply_at\"] = ts\n", "            pass\n"),
    ("lower-bound total rounded up", "down=not tokens.get(\"complete\", True)", "down=False"),
    ("fill shown with zero tokens", "if ctx_pct is not None and not no_reading:", "if ctx_pct is not None:"),
    ("unknown window tokens shown as 0", "    if ct is not None and not no_reading:", "    ct = ct or 0\n    if True:"),
    ("unknown context fill shown as 0", "    if ctx_pct is not None and not no_reading:  # a fill with 0 tokens is not from a real reading either\n        parts.append(\"%d%%\" % _half_up(ctx_pct))",
     "    if True:\n        parts.append(\"%d%%\" % _half_up(ctx_pct or 0))"),
    ("zero window tokens shown as T0", "no_reading = ct is not None and ct <= 0", "no_reading = False"),
    ("incomplete total shown as exact", "\"\" if tokens.get(\"complete\", True) else GE", "\"\""),
    ("token K/M boundary", "    if n < 999_500:", "    if n < 1_000_000:"),
    ("token number rounds down", "(n + 500) // 1000", "n // 1000"),
    ("window tokens not read from payload", "cti = _num(cw.get(\"total_input_tokens\"))", "cti = None"),
    ("ru letters lost", "\"T\": \"Т\"", "\"T\": \"T\""),
    ("fresh percentage rounds down", "return \"%d%%\" % _half_up(v[\"used_percentage\"])", "return \"%d%%\" % int(v[\"used_percentage\"])"),
    ('install: no backup before the write', '    bak = _backup(path)\n    _write_atomic(path, json.dumps(d,', '    bak = None\n    _write_atomic(path, json.dumps(d,'),
    ('install twice: saved previous overwritten by the truegauge line', '    if "statusLine" in d and _is_ours(d["statusLine"]):\n        print("truegauge is already', '    if False:\n        print("truegauge is already'),
    ('uninstall touches a foreign line', '    if "statusLine" not in d or not _is_ours(d["statusLine"]):\n        print("truegauge is not the status line in', '    if "statusLine" not in d:\n        print("truegauge is not the status line in'),
    ('invalid JSON treated as empty (would be overwritten)', '        raise Refuse("%s is not valid JSON (%s); nothing changed" % (path, e))', '        return {}'),
    ('non-object JSON treated as empty', '        raise Refuse("%s is not a JSON object; nothing changed" % path)', '        return {}'),
    ('uninstall always removes the key (previous not restored)', '    if isinstance(rec, dict) and rec.get("had_line"):', '    if False:'),
    ('install drops the other keys', '    d["statusLine"] = {"type": "command", "command": _our_command(), "refreshInterval": 60}', '    d = {"statusLine": {"type": "command", "command": _our_command(), "refreshInterval": 60}}'),
    ('CLAUDE_CONFIG_DIR ignored', '    cfg = os.environ.get("CLAUDE_CONFIG_DIR")', '    cfg = None'),
    ('one saved previous for all settings files', '    entries[key] = {"had_line"', '    entries.clear()\n    entries[key] = {"had_line"'),
    ('saved file removed before the settings write', '    bak = _write_settings(path, d)\n    entries.pop(key, None)\n    _prev_store(entries)  # only after the settings write succeeded', '    entries.pop(key, None)\n    _prev_store(entries)\n    bak = _write_settings(path, d)'),
    ('damaged saved-previous file ignored', '        raise Refuse("saved previous line %s is unreadable (%s); nothing changed" % (_prev_file(), e))', '        return {}'),
    # ---- review round 2: strict UTF-8, refusal without a saved record, the test fuse
    ('settings read with errors="replace"', 'text = raw.decode("utf-8-sig")  # strict', 'text = raw.decode("utf-8-sig", "replace")  # strict'),
    ('uninstall without a record removes the key silently', '    if rec is None and not force:', '    if False:'),
    ('--force always on', 'force="--force" in argv', 'force=True'),
    ('--force ignored', 'force="--force" in argv', 'force=False'),
    ('refusal names the oldest backup, not the newest', 'return max(found, key=lambda p: (os.path.getmtime(p), p))', 'return min(found, key=lambda p: (os.path.getmtime(p), p))'),
    # mutants of the TEST FILE: the fuse must not be removable without the suite going red
    ('FUSE: real-directory branch removed', '    for real in REAL_PROTECTED:', '    for real in []:', "test"),
    ('FUSE: outside-root branch removed', '    if not _inside(p, ROOT):', '    if False:', "test"),
    ('FUSE: not called on the settings path', '        guard_path(tg._settings_path(list(args)))', '        pass', "test"),
    ('FUSE: not called on the state dir', '    guard_path(tg.HOME)', '    pass', "test"),
]

src = open(SRC, encoding="utf-8").read()
slipped = 0
TEST_SRC = open(os.path.join(HERE, "test_truegauge.py"), encoding="utf-8").read()
for name, old, new, *target in MUTANTS:
    on_test = bool(target)
    base = TEST_SRC if on_test else src
    if base.count(old) != 1:
        print("BLIND  %s (pattern found %d times -- mutant not applied)" % (name, base.count(old)))
        slipped += 1
        continue
    d = tempfile.mkdtemp(prefix="tg_mut_")
    os.makedirs(os.path.join(d, "tests"))
    open(os.path.join(d, "truegauge.py"), "w", encoding="utf-8").write(src if on_test else src.replace(old, new))
    open(os.path.join(d, "tests", "test_truegauge.py"), "w", encoding="utf-8").write(TEST_SRC.replace(old, new) if on_test else TEST_SRC)
    r = subprocess.run([sys.executable, os.path.join(d, "tests", "test_truegauge.py")], capture_output=True, text=True)
    ok = r.returncode != 0
    slipped += 0 if ok else 1
    print(("CAUGHT " if ok else "SLIPPED ") + name)
print("\n%d of %d caught" % (len(MUTANTS) - slipped, len(MUTANTS)))
sys.exit(1 if slipped else 0)
