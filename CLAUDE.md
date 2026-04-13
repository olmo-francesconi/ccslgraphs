# Claude Code Statusline — Project Context

## What This Is

A custom statusline for Claude Code, rendered via the `statusLine` command setting in `~/.claude/settings.json`. Pure Python — no Node dependency.

**Deploy**: run `install.py` — copies scripts to `~/.claude/ccslgraphs/` and sets `statusLine` in `settings.json`. Run `uninstall.py` to reverse.

## Project Layout

```
statusline.py       # main script — single-line status + side-by-side usage graphs
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
python src/statusline.py
```

## Architecture

### `statusline.py`
- `Span` — paired `(plain, colored)` text; `plain` for width arithmetic, `colored` for terminal output.
- `render_line()` — adaptive single-line status: model · context bar · git info.
- `render_graphs()` — 5h session + 7d weekly graphs side-by-side; header + `GRAPH_ROWS` rows.
- Graph renderer: 2D cell grid with bend-corner box-drawing chars and a terminal `●` dot.
- On each render, reads `rate_limits` from Claude Code's stdin JSON, appends to history, and writes the cache atomically. No background process.
- History is sampled at 1 min intervals for the 5h graph (max 300 points) and 10 min for the 7d graph (max 1008 points). Old points are trimmed on every write.
- `SAFE_MARGIN = 30` — reserves space for Claude Code's own token counter overlay.
- Git info comes from a single `git status --porcelain=2 --branch` call so branch + dirty state stay cheap on the hot path.
- Token counts: `input` and `output` are cumulative session totals from `context_window.total_input_tokens` / `total_output_tokens`; `cache_read` is per-response from `context_window.current_usage.cache_read_input_tokens` (no cumulative available).

### Usage cache (`~/.claude/ccslgraphs/usage-cache.json`)
```json
{
  "updatedAt": "<ISO-8601>",
  "session":       { "utilization": 0-100, "resetsAt": "<ISO-8601>" },
  "weekly":        { "utilization": 0-100, "resetsAt": "<ISO-8601>" },
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
- Cache writes: atomic `os.replace()` from a `.tmp` file — always write via `tempfile.NamedTemporaryFile` then replace.
- No third-party Python packages.
