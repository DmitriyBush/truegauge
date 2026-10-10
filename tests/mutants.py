"""Question: does the test suite go red when a guarded behaviour is broken? Each mutant edits a COPY of
truegauge.py in a temp dir and runs the suite against it. CAUGHT = suite red, SLIPPED = suite green (a hole).
Run: python tests/mutants.py [--jobs N]   (N parallel suite runs; the first run is a control on an unmutated copy)"""
import concurrent.futures
import glob
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
    ("stale shown as point value", "(GE if stale else \"\", _pct_int", "(\"\", _pct_int"),
    ("expired keeps old number", "if rst is not None and rst <= now:", "if rst is not None and rst < now:"),
    ("updated_at moves without change", "    elif changed:\n        d[\"updated_at\"]", "    elif True:\n        d[\"updated_at\"]"),
    ("split message counted twice", "ids[mid] = vals if old is None else [max(a, b) for a, b in zip(old, vals)]", "ids[mid] = vals if old is None else [a + b for a, b in zip(old, vals)]"),
    ("new week does not end the Fable reading", "if rst is not None and _valid(week) and week[\"resets_at\"] > rst + 3600:", "if False:"),
    ("old Fable bound shown without reset time", "if rst is None and v[\"status\"] == \"stale_reading\":", "if False:"),
    ("stale floor -> round (value above the reading)", "_pct_int(v[\"used_percentage\"], floor=stale)", "_pct_int(v[\"used_percentage\"], floor=False)"),
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
    ("fresh percentage rounds down", "_pct_int(v[\"used_percentage\"], floor=stale)", "_pct_int(v[\"used_percentage\"], floor=True)"),
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
    # ---- 0.2: red group at the limit threshold
    ("red: 5h default threshold 98 -> 97", '"five_hour": ("TRUEGAUGE_RED_5H", 98)', '"five_hour": ("TRUEGAUGE_RED_5H", 97)'),
    ("red: week default threshold 99 -> 98", '"seven_day": ("TRUEGAUGE_RED_7D", 99)', '"seven_day": ("TRUEGAUGE_RED_7D", 98)'),
    ("red: 5h default threshold 98 -> 99", '"five_hour": ("TRUEGAUGE_RED_5H", 98)', '"five_hour": ("TRUEGAUGE_RED_5H", 99)'),
    ("red: half_up broken in the colour path only", '_pct_int(v["used_percentage"]) >= threshold', 'int(v["used_percentage"]) >= threshold'),
    ("red: ageing changes the colour (colour from the shown floor)", '_pct_int(v["used_percentage"]) >= threshold',
     '_pct_int(v["used_percentage"], floor=v.get("status") != "fresh") >= threshold'),
    ("red: reset is a full reset 0m", r'RED_RESET = "\033[22;39m"', r'RED_RESET = "\033[0m"'),
    ("red: no reset after the group", 'group = cc["sgr"] + group + RED_RESET', 'group = cc["sgr"] + group'),
    ("red: NO_COLOR ignored", '"no_color": bool(env.get("NO_COLOR"))', '"no_color": False'),
    ("red: empty NO_COLOR counts as set", '"no_color": bool(env.get("NO_COLOR"))', '"no_color": "NO_COLOR" in env'),
    ("red: empty threshold is a refusal, not 'unset'", '    if not s:\n        return default, None\n    if s.lower() == "off"',
     '    if not s and raw is None:\n        return default, None\n    if s.lower() == "off"'),
    ("red: out-of-range threshold accepted", "1 <= int(s) <= 100", "1 <= int(s) <= 1000"),
    ("red: refused threshold not reported", 'return default, "%r (an integer 1..100, or off / 0)" % raw', 'return default, None'),
    ("red: spaces at the edges of a threshold refuse it", '    s = (raw or "").strip()\n    if not s:\n        return default, None',
     '    s = (raw or "")\n    if not s:\n        return default, None'),
    ("red: `off` is case-sensitive", 's.lower() == "off" or s == "0"', 's == "off" or s == "0"'),
    ("red: `off` not recognised", 's.lower() == "off" or s == "0"', 's == "0"'),
    ("red: short hex colour accepted", r're.fullmatch(r"#[0-9a-fA-F]{6}", s)', r're.fullmatch(r"#[0-9a-fA-F]{3,6}", s)'),
    ("red: ansi colour changed to bright 91", r'RED_ANSI = "\033[1;31m"', r'RED_ANSI = "\033[1;91m"'),
    ("red: refused colour not reported", 'return _rgb(RED_DEFAULT_RGB), "%r (#rrggbb or ansi)" % raw', 'return _rgb(RED_DEFAULT_RGB), None'),
    # ---- 0.2 round 3: the status line keeps its own colour settings (SF1-SF4, CR2, CR3)
    ("red: refusal of a setting is not recorded as an error", "    if new_refusal:\n", "    if False:\n"),
    ("red: --show judges by the shell's settings, not the status line's", "    judge = rc  # the status line's own", "    judge = {\"th\": cc[\"th\"], \"no_color\": cc[\"no_color\"]}  # the status line's own"),
    ("red: colour config fault takes the whole line (no try/except)", "except Exception as e:  # SF4 config", "except ZeroDivisionError as e:  # SF4 config"),
    ("red: colour paint fault takes the whole line (no try/except)", "except Exception as e:  # SF4 paint", "except ZeroDivisionError as e:  # SF4 paint"),
    ("red: render_cfg rewritten on every call", "        if old == cfg:\n            return\n", "        if False:\n            return\n"),
    ("red: render_cfg never saved", "        d[\"render_cfg\"] = cfg\n", "        pass\n"),
    ("red: --show says plain yes under NO_COLOR", "                if verdict == \"yes\" and judge[\"no_color\"]:", "                if False:"),
    ("red: spaces-only threshold is a refusal", '    s = (raw or "").strip()\n    if not s:\n        return default, None\n    if s.lower() == "off"',
     '    s = (raw or "").strip()\n    if not (raw or ""):\n        return default, None\n    if s.lower() == "off"'),
    ("red: spaces-only colour is a refusal", '    s = (raw or "").strip()\n    if not s:\n        return _rgb(RED_DEFAULT_RGB), None',
     '    s = (raw or "").strip()\n    if not (raw or ""):\n        return _rgb(RED_DEFAULT_RGB), None'),
    # ---- 0.3: amber context group when the cache is cold; the mark without colour
    ("cold: expires_at boundary >= -> >", "return exp is not None and now >= exp", "return exp is not None and now > exp"),
    ("cold: warm=false no longer wins over a future time", 'if pc.get("warm") is False:\n        return True', 'if False:\n        return True'),
    ("cold: caching_observed not checked", 'if not isinstance(pc, dict) or pc.get("caching_observed") is not True:', 'if not isinstance(pc, dict):'),
    ("cold: caching_observed='true' (string) accepted", 'pc.get("caching_observed") is not True', 'pc.get("caching_observed") not in (True, "true")'),
    ('cold: warm "false" (string) read as False', 'if pc.get("warm") is False:', 'if pc.get("warm") in (False, "false"):'),
    ("cold: warm=0 read as False", 'if pc.get("warm") is False:', 'if pc.get("warm") == False:'),
    ("cold: expires_at judged without a type check", 'exp = _num(pc.get("expires_at"))', 'exp = pc.get("expires_at")'),
    ("cold: now ignored (time 0)", 'cold = _cache_cold(payload.get("prompt_cache"), now)', 'cold = _cache_cold(payload.get("prompt_cache"), 0)'),
    ("cold: not passed to the renderer", 'os.environ.get("TRUEGAUGE_LANG", "en"), cti, cold)', 'os.environ.get("TRUEGAUGE_LANG", "en"), cti)'),
    ("cold: Sigma painted", 'parts.append("%s%s%s" % (SIGMA,', 'parts.append("\\033[1;38;2;255;175;0m" + "%s%s%s" % (SIGMA,'),
    ("cold: only T painted, percent outside", 'group = _decorate(group, None if cc["no_color"] else cc["cold_sgr"], cc["mark"])',
     'group = _decorate(group.split(" ")[0], None if cc["no_color"] else cc["cold_sgr"], cc["mark"]) + group[len(group.split(" ")[0]):]'),
    ("cold: empty group painted when there is no reading", "if group and cold and cc is not None:", "if cold and cc is not None:"),
    ("cold: no reset after a group", "        group = paint + group + RED_RESET", "        group = paint + group"),
    ("cold: NO_COLOR ignored for the amber", 'group = _decorate(group, None if cc["no_color"] else cc["cold_sgr"], cc["mark"])',
     'group = _decorate(group, cc["cold_sgr"], cc["mark"])'),
    ("cold: default amber changed", 'COLD_DEFAULT_RGB = "#ffaf00"', 'COLD_DEFAULT_RGB = "#ffbf00"'),
    ("cold: ansi colour changed to bright 93", r'COLD_ANSI = "\033[1;33m"', r'COLD_ANSI = "\033[1;93m"'),
    ("cold: `off` not recognised", 'if s.lower() in ("off", "0"):', 'if s.lower() in ("0",):'),
    ("cold: `0` not recognised", 'if s.lower() in ("off", "0"):', 'if s.lower() in ("off",):'),
    ("cold: `off` is case-sensitive", 'if s.lower() in ("off", "0"):', 'if s in ("off", "0"):'),
    ("cold: empty colour is a refusal, not 'unset'", '    if not s:\n        return _rgb(COLD_DEFAULT_RGB), None',
     '    if not s and raw is None:\n        return _rgb(COLD_DEFAULT_RGB), None'),
    ("cold: short hex accepted", 'if re.fullmatch(r"#[0-9a-fA-F]{6}", s):\n        return _rgb(s), None\n    return _rgb(COLD_DEFAULT_RGB)',
     'if re.fullmatch(r"#[0-9a-fA-F]{3,6}", s):\n        return _rgb(s), None\n    return _rgb(COLD_DEFAULT_RGB)'),
    ("cold: refused colour not reported", 'return _rgb(COLD_DEFAULT_RGB), "%r (#rrggbb, ansi or off)" % raw', 'return _rgb(COLD_DEFAULT_RGB), None'),
    ("cold: refusal not stored in the config", '        refused["TRUEGAUGE_COLD_COLOR"] = bad\n', '        pass\n'),
    ("cold: colour setting read from the red variable", 'cold_sgr, bad = _cold_sgr(env.get("TRUEGAUGE_COLD_COLOR"))', 'cold_sgr, bad = _cold_sgr(env.get("TRUEGAUGE_RED_COLOR"))'),
    ("cold: the amber test fault takes the whole line (no try/except)", "except Exception as e:  # SF4 cold test", "except ZeroDivisionError as e:  # SF4 cold test"),
    ("cold: the amber paint fault takes the whole line (no try/except)", "except Exception as e:  # SF4 cold paint", "except ZeroDivisionError as e:  # SF4 cold paint"),
    ("cache record: fault takes the whole line (no try/except)", "except Exception as e:  # an extra (the measuring instrument)", "except ZeroDivisionError as e:  # an extra (the measuring instrument)"),
    ("cache record: rewritten on every call", 'if isinstance(old, dict) and {k: v for k, v in old.items() if k != "seen_at"} == rec:\n            return', 'if False:\n            return'),
    ("cache record: never saved", 'd["prompt_cache_seen"] = dict(rec, seen_at=int(now))', 'pass'),
    ("cache record: seen_at not recorded", 'dict(rec, seen_at=int(now))', 'dict(rec)'),
    ("cache record: an absent object not recorded as present=False", 'rec = {"present": isinstance(pc, dict)}', 'rec = {"present": True}'),
    ("cache record: a container stored whole", 'return "<%s>" % type(v).__name__', 'return v'),
    ("cache record: nan stored as a number", 'return v if _num(v) is not None else "<float nan/inf>"', 'return v'),
    ("cache record: long text not cut", "return v[:32]", "return v"),
    ("cache record: overwrites a damaged state", "        if problem not in (None, \"missing\"):\n            return\n        old = d.get(\"prompt_cache_seen\")", "        old = d.get(\"prompt_cache_seen\")"),
    ("--show: cold now always no", "    cold = _cache_cold(rec, now)\n", "    cold = False\n"),
    ("--show: a missing record not named", 'print("cache: no record yet")', 'print("cache: ")'),
    ("--show: damaged record crashes (not named)", 'print("cache: the record is damaged; the status line rewrites it on its next call")', 'raise KeyError("damaged")'),
    ("--show: the status line's own mark not saved", '"cold_sgr": cc["cold_sgr"], "mark": cc["mark"],', '"cold_sgr": cc["cold_sgr"], "mark": "off",'),
    ("mark: on by default", '    s = (raw or "").strip().lower()\n    if not s:\n        return "off", None', '    s = (raw or "").strip().lower()\n    if not s:\n        return "both", None'),
    ("mark: empty value is a refusal, not 'unset'", '    s = (raw or "").strip().lower()\n    if not s:\n        return "off", None', '    s = (raw or "").strip().lower()\n    if not s and raw is None:\n        return "off", None'),
    ("mark: case-sensitive", '    s = (raw or "").strip().lower()\n    if not s:\n        return "off", None', '    s = (raw or "").strip()\n    if not s:\n        return "off", None'),
    ("mark: spaces at the edges refuse it", '    s = (raw or "").strip().lower()\n    if not s:\n        return "off", None', '    s = (raw or "").lower()\n    if not s:\n        return "off", None'),
    ("mark: unknown value accepted as set", 'return "off", "%r (underline, brackets, both or off)" % raw', 'return "both", None'),
    ("mark: refusal not reported", 'return "off", "%r (underline, brackets, both or off)" % raw', 'return "off", None'),
    ("mark: refusal not stored in the config", '        refused["TRUEGAUGE_MARK"] = bad\n', '        pass\n'),
    ("mark: removed when NO_COLOR is set (window groups)", 'group = _decorate(group, None if cc["no_color"] else cc["sgr"], cc["mark"])',
     'group = _decorate(group, None if cc["no_color"] else cc["sgr"], "off" if cc["no_color"] else cc["mark"])'),
    ("mark: removed when NO_COLOR is set (context group)", 'group = _decorate(group, None if cc["no_color"] else cc["cold_sgr"], cc["mark"])',
     'group = _decorate(group, None if cc["no_color"] else cc["cold_sgr"], "off" if cc["no_color"] else cc["mark"])'),
    ("mark: removed when the cold colour is off", 'group = _decorate(group, None if cc["no_color"] else cc["cold_sgr"], cc["mark"])',
     'group = _decorate(group, None if cc["no_color"] else cc["cold_sgr"], cc["mark"] if cc["cold_sgr"] else "off")'),
    ("mark: no underline-off after an underlined group", "        group = UL_ON + group + UL_OFF", "        group = UL_ON + group"),
    ("mark: underline-off is a full reset", r'UL_ON, UL_OFF = "\033[4m", "\033[24m"', r'UL_ON, UL_OFF = "\033[4m", "\033[0m"'),
    ("mark: both does not underline", 'if mark in ("underline", "both"):', 'if mark in ("underline",):'),
    ("mark: both has no brackets", 'if mark in ("brackets", "both"):', 'if mark in ("brackets",):'),
    ("mark: underline brings brackets", 'if mark in ("brackets", "both"):', 'if mark in ("brackets", "both", "underline"):'),
    ("mark: brackets put outside the underline and colour", '    if paint:\n        group = paint + group + RED_RESET\n    return group',
     '    if paint:\n        group = paint + group + RED_RESET\n    if mark in ("brackets", "both"):\n        group = "[" + group + "]"\n    return group'),
    ("mark: Sigma bracketed", 'parts.append("%s%s%s" % (SIGMA,', 'parts.append("[" + "%s%s%s" % (SIGMA,'),
    ("mark: put on a window group that is not past the threshold", "                if _reached(v, cc[\"th\"][key]):\n                    group = _decorate",
     "                if True:\n                    group = _decorate"),
    # mutants of the TEST FILE: the fuse must not be removable without the suite going red
    ('FUSE: real-directory branch removed', '    for real in REAL_PROTECTED:', '    for real in []:', "test"),
    ('FUSE: outside-root branch removed', '    if not _inside(p, ROOT):', '    if False:', "test"),
    ('FUSE: not called on the settings path', '        guard_path(tg._settings_path(list(args)))', '        pass', "test"),
    ('FUSE: not called on the state dir', '    guard_path(tg.HOME)', '    pass', "test"),
]

src = open(SRC, encoding="utf-8").read()
TEST_SRC = open(os.path.join(HERE, "test_truegauge.py"), encoding="utf-8").read()
JOBS = int(sys.argv[sys.argv.index("--jobs") + 1]) if "--jobs" in sys.argv else 1  # parallel runs; 1 = one by one


def run_one(m):
    """-> (status, name): CAUGHT | SLIPPED | BLIND."""
    name, old, new, *target = m
    on_test = bool(target)
    base = TEST_SRC if on_test else src
    if base.count(old) != 1:
        return "BLIND  ", "%s (pattern found %d times -- mutant not applied)" % (name, base.count(old))
    d = tempfile.mkdtemp(prefix="tg_mut_")
    os.makedirs(os.path.join(d, "tests"))
    for ref in glob.glob(os.path.join(HERE, "ref_*.py")):  # the suite loads its reference modules from its own folder
        shutil.copy(ref, os.path.join(d, "tests"))
    open(os.path.join(d, "truegauge.py"), "w", encoding="utf-8").write(src if on_test else src.replace(old, new))
    open(os.path.join(d, "tests", "test_truegauge.py"), "w", encoding="utf-8").write(TEST_SRC.replace(old, new) if on_test else TEST_SRC)
    r = subprocess.run([sys.executable, os.path.join(d, "tests", "test_truegauge.py")], capture_output=True, text=True)
    return ("CAUGHT " if r.returncode != 0 else "SLIPPED "), name


# CONTROL: the unmutated copy must be green, else every "CAUGHT" below would only mean "the harness is broken"
# (found 2026-10-10: the 0.2 runner did not copy the reference modules, the suite crashed on load, all 95 mutants "caught")
_status, _ = run_one(("control: no change", "__version__ = ", "__version__ = "))
if _status != "SLIPPED ":
    print("CONTROL FAILED: the suite is not green on an unmutated copy; no mutant result below could be trusted -- stopped")
    sys.exit(2)
print("control: unmutated copy is green", flush=True)
slipped = 0
with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, JOBS)) as pool:
    for status, name in pool.map(run_one, MUTANTS):  # results come back in list order
        slipped += 0 if status == "CAUGHT " else 1
        print(status + name, flush=True)
print("\n%d of %d caught" % (len(MUTANTS) - slipped, len(MUTANTS)))
sys.exit(1 if slipped else 0)
