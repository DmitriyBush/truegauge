# truegauge

A status line for Claude Code that never shows an old number as a current one.

```
5h: 1h21m left 78% | week: 5d 7h left 82% | F left 96% | session 529k tok | context 31%
```

| Part | Meaning |
|---|---|
| `5h: 1h21m left 78%` | time until the 5-hour window resets, and how much of it is still left |
| `week: 5d 7h left 82%` | the same for the weekly window (all models) |
| `F left 96%` | the separate weekly **Fable** allowance, still left |
| `session 529k tok` | tokens spent in this session: input + cache writes + output, main conversation **and its subagents** (cache reads are counted apart, see `--show`) |
| `context 31%` | how full the context window is |

## Why another status line

There are many status lines that print the plan limits. Most of them print whatever the last
reading was. But a Claude Code session that sits idle keeps re-sending its **last** reading, and a
number from an hour ago looks exactly like a number from a minute ago. If you plan work by that number
(for example, "there is enough left to let it run overnight"), a stale value is the worst kind of wrong.

truegauge follows three rules:

1. **A replayed reading never wins.** Several sessions share one account. Per window, the reading with
   the later reset time and the higher usage wins; an older, lower reading from an idle session is ignored.
2. **An unchanged reading ages.** When the numbers have not changed for 15 minutes, truegauge shows
   `<=78%` ("at most 78% left") instead of `78%`. Usage inside a window only grows, so the true remainder
   can only be lower.
3. **A window that has reset says `reset`**, not the number from before the reset.

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
  at most once every 10 minutes (about 3 seconds, no model call, no session saved) and reads the line
  `Current week (Fable): N% used`. If your plan has no separate Fable allowance, the `F` part is not shown.
  Turn this off with `TRUEGAUGE_FABLE=0`.

## Install

Requires Python 3.8+ (standard library only) and Claude Code.

1. Put `truegauge.py` anywhere, for example `~/.claude/truegauge.py`.
2. Add to `~/.claude/settings.json`:

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
`refreshInterval` keeps the time left moving while the session is quiet.

## Settings (environment variables)

| Variable | Default | |
|---|---|---|
| `TRUEGAUGE_LANG` | `en` | `ru` for Russian labels |
| `TRUEGAUGE_FABLE` | `1` | `0` turns off the background `/usage` read |
| `TRUEGAUGE_PROBE_S` | `600` | seconds between `/usage` reads (minimum 120) |
| `TRUEGAUGE_MAX_AGE_S` | `900` | after this many seconds without a change a reading is shown as `<=` |
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

## License

Apache-2.0. Not affiliated with Anthropic. Claude and Claude Code are trademarks of Anthropic.
