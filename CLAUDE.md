# Claude Code Statusline — Project Context

## What This Is

A custom statusline for Claude Code, rendered via the `statusLine` command setting in `~/.claude/settings.json`. Both scripts are pure Python — no Node dependency.

**Deploy**: run `install.py` — copies scripts to `~/.claude/ccslgraphs/` and sets `statusLine` in `settings.json`. Run `uninstall.py` to reverse.

## Project Layout

```
statusline.py       # main script — single-line status + side-by-side usage graphs
usage_fetch.py      # background fetcher — writes usage-cache.json beside itself
install.py          # installs into ~/.claude/ccslgraphs/, patches settings.json
uninstall.py        # removes ~/.claude/ccslgraphs/, cleans settings.json
pyproject.toml      # project metadata (requires-python = ">=3.11")
tests/              # unittest suite (test_statusline.py, test_install.py)
```

## Development

```bash
# Run tests
python -m unittest discover tests

# Manual invocation (simulate what Claude Code calls)
python statusline.py
```

## Architecture

### `statusline.py`
- `Span` — paired `(plain, colored)` text; `plain` for width arithmetic, `colored` for terminal output.
- `render_line1()` — adaptive single-line status: model · context bar · git info.
- `render_graphs()` — 5h session + 7d weekly graphs side-by-side; header + `GRAPH_ROWS` rows.
- Graph renderer: 2D cell grid with bend-corner box-drawing chars and a terminal `●` dot.
- Spawns `usage_fetch.py` detached when cache is stale (>5 min); a lock file prevents concurrent fetch fan-out and `· stale` appears in both graph headers when stale data is displayed.
- `TOP_RIGHT_MARGIN = 30` — reserves space for Claude Code's own token counter overlay.
- Git info comes from a single `git status --porcelain=2 --branch` call so branch + dirty state stay cheap on the hot path.

### `usage_fetch.py`
- Reads OAuth token from macOS Keychain (`security find-generic-password`) or `~/.claude/.credentials.json`.
- Fetches `https://api.anthropic.com/api/oauth/usage` (header: `anthropic-beta: oauth-2025-04-20`).
- Builds cache: trims history to current session/week window, appends new point.
- Writes atomically to `usage-cache.json` (sibling of the script) via a unique temp file and `os.replace()`.
- Clears the fetch lock in a `finally` block so failed workers do not leave the cache permanently locked.
- Paths resolved via `Path(__file__).parent` — works regardless of install location.

### Usage cache (`~/.claude/ccslgraphs/usage-cache.json`)
```json
{
  "fetchedAt": "<ISO-8601>",
  "session":       { "utilization": 0-100, "resetsAt": "<ISO-8601>" },
  "weekly":        { "utilization": 0-100, "resetsAt": "<ISO-8601>" },
  "monthly":       { "enabled": true, "usedCents": 0, "limitCents": 0 },
  "history":       [{ "ts": "<ISO-8601>", "pct": 42 }],
  "weeklyHistory": [{ "ts": "<ISO-8601>", "pct": 18 }]
}
```

## Commit Format

```
(type) short description
```

- `fix` — bug fix
- `feature` — new capability
- `update` — change to existing behaviour or refactor
- `test` — add or update tests
- `docs` — documentation only
- `chore` — tooling, config, or maintenance

Sentence case, no period, ≤72 chars total.

## Key Conventions

- ANSI: use `vlen(s)` (strip ANSI before measuring) for all padding arithmetic — never measure colored strings directly.
- Terminal width: read via `/dev/tty` first, then `tput cols`, then `$COLUMNS`. Never use `sys.stdout` (it's a pipe).
- Fetcher: detach with `subprocess.Popen(..., start_new_session=True)` — statusline must never wait for fetch.
- Cache writes: atomic `os.rename()` from `.tmp` file on POSIX.
- No third-party Python packages.
