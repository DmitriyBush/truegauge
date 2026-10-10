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
| `TRUEGAUGE_RED_5H` | `98` | red threshold of the 5-hour window (see Colors) |
| `TRUEGAUGE_RED_7D` | `99` | red threshold of the weekly window |
| `TRUEGAUGE_RED_COLOR` | `#ff0000` | `#rrggbb` or `ansi` |
| `TRUEGAUGE_COLD_COLOR` | `#ffaf00` | colour of the context group when the cache is cold: `#rrggbb`, `ansi` or `off` (see Cold cache) |
| `TRUEGAUGE_MARK` | `off` | `underline`, `brackets`, `both` or `off`: a mark without colour (see Colour-blind friendly mark) |
| `NO_COLOR` | unset | any non-empty value: no colour |

## Colors

A window group (time and percent together, e.g. `4h 98%`) turns **bold red** when that window's reading has
reached a threshold:

| Window | Threshold variable | Default |
|---|---|---|
| 5-hour | `TRUEGAUGE_RED_5H` | `98` |
| weekly | `TRUEGAUGE_RED_7D` | `99` |

A threshold is an integer 1..100, or `off` (also `0`) for never. Empty or unset means the default; anything else
(`101`, `-1`, `97.5`, `abc`) is refused, the default is used, and `--show` names the refusal. Spaces around the
value are ignored.

How to set them: the main path is the `env` block of `~/.claude/settings.json`, which Claude Code documents as reaching every command it starts (so the status line too; not measured by the
tests):

```json
{ "env": { "TRUEGAUGE_RED_5H": "95", "TRUEGAUGE_RED_7D": "off", "TRUEGAUGE_RED_COLOR": "#ff5500" } }
```

The second path is an `export` in your shell before starting Claude Code. `python truegauge.py --show` prints two
lines: the values of **that shell**, and the settings **the status line itself last used** (it saves them in the
state file when they change, so an `env` block in settings.json shows up there, with the time it was seen). The
"past the red threshold" verdict follows the second one; before the status line has saved anything it says
`unknown`. A refused value (say `"95%"`) is also written to the last error once.

- `TRUEGAUGE_RED_COLOR`: `#rrggbb` (any case, default `#ff0000`) or `ansi`. `ansi` is the bold red of your terminal
  theme, so its shade depends on that theme (it can look crimson in some). A wrong value is refused like a
  threshold. The group is closed with `ESC[22;39m` (bold off, default foreground; see the last note below); the
  space between groups is never coloured.
- `NO_COLOR`: any non-empty value turns colour off entirely, and the output is then byte for byte the same as 0.1
  (while `TRUEGAUGE_MARK` is unset). An empty value does not.
- Only the two plan windows turn red. Fable, tokens and the context fill never do (the context group has its own
  amber, see Cold cache below); a window that has reset has no group at all.

The colour follows the **raw reading**: red when the reading, rounded half up, is at or above the threshold. For a
fresh reading that is the number you see. For an old one it is true too: an old `≥97%` can be red at threshold 98,
because the reading behind it was 97.6 (shown as 98% while fresh) and usage inside a window only grows. Ageing
changes the text (`98%` becomes `≥97%`), never the colour.

What the line does **not** tell you:

- With `NO_COLOR`, `off`, or a refused value there is no near-the-limit sign on the line at all: a plain `97%`
  looks the same as a plain `40%`. `--show` tells you which of these is in force.
- An old reading that is not red does not mean "far from the limit". `≥` means "at least": `≥95%` can be a real
  100% that has not been read again yet.
- The group is closed with `ESC[22;39m`, which switches off bold **and also faint**. If Claude Code draws the
  status line faint, the items after a red group may look brighter than the rest. This is not measured in Claude
  Code (it can only be seen in a terminal). If you cannot see the red at all, try `TRUEGAUGE_RED_COLOR=ansi`.

## Cold cache (amber context group)

The context group (`T548K 55%`, both parts together) turns **amber** when the prompt cache of your main session has
gone cold. A cold cache means the next message re-sends the whole context at full price instead of the cheap cached
price, so this is the moment to think before sending a long-context message.

- **Where the data comes from.** Claude Code's status-line input carries a `prompt_cache` object (its documentation:
  main session only, from v2.1.251; subagents are not counted). truegauge reads `caching_observed`, `warm` and
  `expires_at` from it.
- **Cold** means: `caching_observed` is `true` AND (`warm` is `false` OR `expires_at` is a number that has passed).
  No `prompt_cache`, one of another type, caching not observed, or a field of the wrong type means **not cold**: no
  data is never shown as cold. Claude Code restarts the line itself when a warm cache reaches `expires_at`, so the
  amber appears without a new message.
- **How long a cache lives.** On a subscription, within the plan, the main session's cache lives 1 hour; when usage
  goes over the plan it is 5 minutes, so amber appears more often then.
- **What is not coloured.** `Σ` (tokens spent in the session, subagents included) is never coloured: the cache state
  is known for the main session only, so it cannot say anything about the subagents' part. Fable and the window groups
  keep their own rules. If there is no context reading (0 tokens: before the first reply, after `/compact`) there is
  nothing to colour.
- **Colour.** `TRUEGAUGE_COLD_COLOR`: `#rrggbb` (default `#ffaf00`, bold amber), `ansi` (the bold yellow of your
  terminal theme) or `off` / `0` (no colour). Empty or unset = default; anything else is refused, the default is used
  and `--show` names the refusal. `NO_COLOR` removes this colour too. The group is closed with `ESC[22;39m`, like the
  red ones.
- **Check that the data really arrives.** The status line keeps the last `prompt_cache` it saw in the state file, and
  `python truegauge.py --show` prints it: `cache: as the status line saw it at <time>: present=..., warm=..., ttl=...,
  expires in N s / expired N s ago, recache_if_cold=..., cold now: yes/no`. `cache: no record yet` means the line has
  not run since this version was installed; `present=False` means Claude Code did not send the object (then amber
  never appears, by the rule above). The arrival of `prompt_cache` on a given install is not asserted by the tests.

## Colour-blind friendly mark

For people who do not tell the colours apart there is a mark that needs no colour. **It is off by default.**
`TRUEGAUGE_MARK` (any case, spaces around are ignored):

| Value | What it does |
|---|---|
| `off` (or empty, unset) | no mark |
| `underline` | the group is underlined (`ESC[4m` ... `ESC[24m`, which switches off only the underline) |
| `brackets` | the group is put in square brackets: `[4h 98%]`, `[T548K 55%]` (a terminal draws no frame around text, brackets are text and show everywhere) |
| `both` | brackets inside the underline: `[4h 98%]` underlined |

Any other value is refused: no mark, and `--show` names the refusal.

The mark goes on exactly the groups that get a colour, under the same conditions: a window group at or above its red
threshold, and the context group when the cache is cold. It does not depend on colour: with `NO_COLOR` set or
`TRUEGAUGE_COLD_COLOR=off` the mark is still drawn (that is who it is for). A threshold set to `off` means no
condition, so no mark. `Σ` and Fable never get one.

Put it in the `env` block of `~/.claude/settings.json`:

```json
{ "env": { "TRUEGAUGE_MARK": "both" } }
```

`python truegauge.py --show` prints `mark: ...` (this shell) and the setting the status line itself last used. How the
underline looks inside Claude Code's status line has to be checked by eye; the tests check the characters only.

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
- The amber context group follows the cache of the MAIN session only. Subagents have their own cache (5 minutes
  according to Claude Code's documentation) and the line cannot see it; `Σ` is not coloured for that reason.
- Whether `prompt_cache` reaches your status line is a property of your Claude Code version; `--show` tells you
  (`cache:` line). Without it there is no amber, and nothing is guessed.

## License

Apache-2.0. Not affiliated with Anthropic. Claude and Claude Code are trademarks of Anthropic.
