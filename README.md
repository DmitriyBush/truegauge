# truegauge

A status line for Claude Code that never shows an old number as a current one.

```
4h 38% 3d 54% F4% T548K 55% Σ2.6M
```

A number and its unit are written together; groups are separated by one space. Percentages are **used**,
not left. A group whose data is unknown is left out, never shown as 0.

| Group | Meaning |
|---|---|
| `4h 38%` | 5-hour window: time until it resets (hours; under 1 h minutes, `45m`), and the share used |
| `3d 54%` | the same for the weekly window (all models): always whole days, `0d` in the last day (so it never looks like the 5-hour group) |
| `F4%` | the separate weekly **Fable** allowance, share used |
| `T548K` | tokens in the context window right now (`context_window.total_input_tokens`: input including cache reads and writes) |
| `55%` | how full the context window is |
| `Σ2.6M` | tokens spent in this session: input + cache writes + output, main conversation **and its subagents**, each API message once (cache reads are counted apart, see `--show`). `Σ≥2.6M` = a lower bound (a large transcript is still being read, or a line was unreadable) |

Times are rounded down. Numbers: `812`, `548K`, `2.6M` (decimal point; with `TRUEGAUGE_LANG=ru` the letters
`T K M` are Cyrillic). A percentage with `≥` (`≥38%`) is a stale reading: it can only have grown, so the number
is a lower bound (rounded down). A window that has reset is left out until a new reading arrives.

## Why another status line

There are many status lines that print the plan limits. Most of them print whatever the last
reading was. But a Claude Code session that sits idle keeps re-sending its **last** reading, and a
number from an hour ago looks exactly like a number from a minute ago. If you plan work by that number
(for example, "there is enough left to let it run overnight"), a stale value is the worst kind of wrong.

truegauge follows three rules:

1. **A replayed reading never wins.** Several sessions share one account. Per window, the reading with
   the later reset time and the higher usage wins; an older, lower reading from an idle session is ignored.
2. **An unchanged reading ages.** When no plan number has changed for 15 minutes, truegauge shows
   `≥38%` ("used at least 38%") instead of `38%`. Usage inside a window only grows, so the true figure
   can only be higher.
3. **A window that has reset is left out** until a new reading arrives, not shown with the number from before the
   reset and not as a guessed `0%` (usage from the Claude web app or another session counts against the same limit).

Any failure leaves the line partial or empty, never wrong, and is written to `~/.truegauge/errors.log`
or shown by `truegauge.py --show`.

## Where the numbers come from

Only documented Claude Code surfaces. Your login token is never read or sent anywhere.

- **5-hour and weekly windows, context:** the JSON Claude Code passes to every status-line command
  (`rate_limits`, `context_window`). Present for Pro and Max plans after the first reply in a session.
- **Session tokens:** the session transcript Claude Code names in that JSON (`transcript_path`) and the
  subagent transcripts next to it. Each API message is counted once (Claude Code writes one message over
  several lines). Only new lines are read on each update.
- **Fable:** the status-line JSON does not carry it. truegauge runs `claude -p /usage` in the background
  at most once every 10 minutes (about 3.5 seconds measured; no settings or hooks loaded, no session saved,
  no model request) and reads the line
  `Current week (Fable): N% used`. If your plan has no separate Fable allowance, the `F` part is not shown.
  Turn this off with `TRUEGAUGE_FABLE=0`.

## Install

Requires Python 3.8+ (standard library only) and Claude Code. Put `truegauge.py` anywhere, for example
`~/.claude/truegauge.py`, then:

```
python ~/.claude/truegauge.py --install
```

This makes truegauge your status line in `~/.claude/settings.json` (or `$CLAUDE_CONFIG_DIR/settings.json`;
another file: `--settings PATH`). Your previous status line, if any, is saved first, and a copy of the settings
file is made next to it (`settings.json.truegauge-backup-<time>`) before every change. Running it twice changes
nothing. Open a new Claude Code session to see the line.

## Turn off

```
python ~/.claude/truegauge.py --uninstall
```

puts your previous status line back, or removes the `statusLine` key if you had none. If the active line is not
truegauge (you changed it since), nothing is touched. `python ~/.claude/truegauge.py --status` says whether
truegauge is the active line and whether a previous one is saved. If the settings file is not valid JSON, all
three commands refuse and leave it as it is. The file is rewritten as indented JSON with every key and its order
kept; the backup copy has the exact old bytes.

### By hand (alternative)

Add to `~/.claude/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "python ~/.claude/truegauge.py",
    "refreshInterval": 60
  }
}
```

On Windows use the full path, for example `"python C:/Users/you/.claude/truegauge.py"`.
`refreshInterval` keeps the time left moving while the session is quiet. `--uninstall` refuses on a line added
this way (there is no saved previous line, so removing the key would drop it silently); it names the newest backup
and `--uninstall --force` removes the key.

## Settings (environment variables)

| Variable | Default | |
|---|---|---|
| `TRUEGAUGE_LANG` | `en` | `ru` for Cyrillic unit letters (`Т К М`) |
| `TRUEGAUGE_FABLE` | `1` | `0` turns off the background `/usage` read |
| `TRUEGAUGE_PROBE_S` | `600` | seconds between `/usage` reads (minimum 120) |
| `TRUEGAUGE_MAX_AGE_S` | `900` | after this many seconds without a change a reading is shown as `≥` |
| `TRUEGAUGE_HOME` | `~/.truegauge` | where the state lives |

## Check it

```
python truegauge.py --show
```

prints what the tool would conclude right now (fresh / stale / reset for every window, Fable included)
and its own health: when it was last called, the last error, rejected readings.

## Tests

```
python tests/test_truegauge.py   # boundary values at every threshold
python tests/mutants.py          # breaks the code on purpose; every break must turn the tests red
```

## Limits (said plainly)

- The weekly Fable reset time is not machine-readable; truegauge uses the weekly reset when `/usage`
  prints the same reset text for both, otherwise only the percentage is shown.
- The `/usage` text is not a documented format. If it changes, the `F` part disappears and `--show`
  says why. It never shows a guessed number.
- Token counts are what the API reported per message. They are not the plan's internal weighting of
  usage and cannot be converted into "percent of limit".
- Each background `/usage` read creates an empty project folder for `~/.truegauge` under
  `~/.claude/projects/` (one folder, not one per read).
- Staleness is judged by the last time ANY plan number changed, not per window.
- Several sessions write one state file under a short lock; if the lock cannot be taken within about
  1 second the update goes ahead, and a lost update is repaired by the next reading.
- A Fable reading is dropped (not shown as a bound) when it is old and its reset time is unknown,
  and it ends when a newer weekly window starts.
- On a very large transcript the first count is finished over several refreshes and shown as `Σ≥`;
  a line that cannot be read also turns the count into `Σ≥` (a lower bound).
- One state directory per Claude account: two logins sharing `~/.truegauge` would mix their readings --
  give each its own `TRUEGAUGE_HOME`.
- A transcript replaced by a different file of greater length is not detected (transcripts are append-only).

## License

Apache-2.0. Not affiliated with Anthropic. Claude and Claude Code are trademarks of Anthropic.
