<div align="center">

```
┌─┐┌─┐┌─┐┬  ┌─┐┬─┐┌─┐┌─┐┬ ┬┌─┐
│  │  └─┐│  │ ┬├┬┘├─┤├─┘├─┤└─┐
└─┘└─┘└─┘┴─┘└─┘┴└─┴ ┴┴  ┴ ┴└─┘
```

A statusline for Claude Code with a live context bar, git info, and usage graphs.

</div>

## Install

```sh
curl -fsSL https://raw.githubusercontent.com/olmo-francesconi/ccslgraphs/main/install.py | python3
```

Downloads the scripts to `~/.claude/ccslgraphs/` and patches your `settings.json`. Restart Claude Code and it's live.

To uninstall:

```sh
curl -fsSL https://raw.githubusercontent.com/olmo-francesconi/ccslgraphs/main/uninstall.py | python3
```

## Example

```
claude-sonnet-4-6  low  ██████████░░░░░░░░░░  12k/100k  main*
──────────────────────────────────────────────────────────────────────────
 5h session                      │  7d weekly
 100% ●                          │  100%
  75%  │                         │   75%
  50%  │  ●                      │   50%          ●
  25%  │  │  ●  ●                │   25%    ●  ●  │  ●
   0%  ──────────────────        │    0%  ──────────────────
       23:00  00:00  01:00       │        Mon  Tue  Wed  Thu
```

Model · effort · context bar · git branch on the top line. Two graphs below — current 5h session and rolling 7-day window, both color-coded blue → orange → red as you climb.
