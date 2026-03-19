<div align="center">

```
┌─┐┌─┐┌─┐┬  ┌─┐┬─┐┌─┐┌─┐┬ ┬┌─┐
│  │  └─┐│  │ ┬├┬┘├─┤├─┘├─┤└─┐
└─┘└─┘└─┘┴─┘└─┘┴└─┴ ┴┴  ┴ ┴└─┘
```

A statusline for Claude Code with a live context bar, git info, and API usage graphs.

</div>

---

![statusline screenshot](assets/statusline.png)

## What it shows

**Top line** — adapts to your terminal width:

| Segment | What it displays |
|---|---|
| Model | Active model name and effort level |
| Context bar | Filled/empty bar + token counts (`used/total`) + I/O breakdown |
| Git | Branch name, dirty state, and `+insertions / -deletions` summary |

The bar color shifts from blue → orange → red as context fills up (70% and 90% thresholds).

**Graphs** — side-by-side below the status line:

| Graph | Window |
|---|---|
| 5h session | Current rolling 5-hour session |
| 7d weekly | Rolling 7-day window |

Both graphs render as smooth line curves with bend-corner box-drawing characters, color-coded by usage level. On narrow terminals they fall back to compact dot-progress bars.

![graphs screenshot](assets/graphs.png)

## Requirements

- Python 3.11+
- macOS (the usage fetcher reads your OAuth token from the system Keychain)
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) installed and authenticated

## Install

```sh
curl -fsSL https://raw.githubusercontent.com/olmo-francesconi/ccslgraphs/main/install.py | python3
```

This downloads `statusline.py` and `usage_fetch.py` to `~/.claude/ccslgraphs/` and sets the `statusLine` key in `~/.claude/settings.json`. Restart Claude Code and the statusline is live.

> **Choosing a graph style** — the installer will ask whether you want line graphs (default) or bar graphs.

## How it works

Usage data is fetched in the background from the Anthropic API. On each render, `statusline.py` checks whether the local cache (`~/.claude/ccslgraphs/usage-cache.json`) is older than 5 minutes; if so, it spawns `usage_fetch.py` as a detached subprocess and displays `· stale` in the graph headers until fresh data arrives. A lock file prevents concurrent fetches.

Context window info (tokens used, total size, model) is passed directly by Claude Code as JSON on `stdin` — no polling needed.

Git info is read from a single `git status --porcelain=2 --branch` call to keep the hot path fast.

## Uninstall

```sh
curl -fsSL https://raw.githubusercontent.com/olmo-francesconi/ccslgraphs/main/uninstall.py | python3
```

Removes `~/.claude/ccslgraphs/` and cleans the `statusLine` entry from `settings.json`.

## Development

```sh
# Run the test suite
python -m unittest discover tests

# Simulate what Claude Code calls
python statusline.py
```
