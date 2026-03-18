# ccslgraphs

A custom statusline for [Claude Code](https://claude.ai/code) that adds a live context bar, git info, and side-by-side usage graphs at the bottom of every session.

---

## What it shows

**Status line** — model name · effort level · context bar with token counts · git branch and dirty state

**Usage graphs** — two side-by-side sparkline graphs that update every 5 minutes:
- **5h session** — context utilization across the current rate-limit window
- **7d weekly** — utilization across the rolling 7-day window

Color coding: blue → orange → red as utilization climbs.

---

## Requirements

- Python 3.11+
- macOS (Keychain auth) or `~/.claude/.credentials.json` fallback
- Claude Code

---

## Install

```sh
python3 install.py
```

Copies `statusline.py` and `usage_fetch.py` into `~/.claude/ccslgraphs/` and patches `~/.claude/settings.json` with the `statusLine` entry. Restart Claude Code to activate.

To uninstall:

```sh
python3 uninstall.py
```

---

## How it works

`statusline.py` is invoked by Claude Code as the `statusLine` command on every turn. It reads session JSON from stdin and prints the status line + graphs to stdout.

When the usage cache is stale (>5 min), it spawns `usage_fetch.py` detached in the background. A lock file deduplicates fetches so repeated renders do not fan out concurrent workers, and the statusline never blocks waiting for a fetch.

`usage_fetch.py` reads your OAuth token from the macOS Keychain (or `~/.claude/.credentials.json` as a fallback), calls the Anthropic usage API, and writes the result atomically to `~/.claude/ccslgraphs/usage-cache.json`.

---

## Files

| File | Purpose |
|------|---------|
| `statusline.py` | Main statusline script — renders status line + graphs |
| `usage_fetch.py` | Background fetcher — writes usage-cache.json |
| `install.py` | Copies scripts to `~/.claude/ccslgraphs/`, patches settings.json |
| `uninstall.py` | Removes install directory, cleans settings.json |
| `tests/` | unittest suite |
