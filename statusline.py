#!/usr/bin/env python3
"""Claude Code statusline — simple single-line + graph layout."""

import json
import math
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ─── Constants ────────────────────────────────────────────────────────────────

GRAPH_ROWS = 7
STALE_SECONDS = 5 * 60
FETCH_LOCK_SECONDS = 60
SAFE_MARGIN = 30
BAR_WIDTH = 20
MAX_WIDTH = 128
COMPACT_GRAPH_W = 80

_GRID_CHAR = "╌"
_GRID_ROWS_SET = {0, 3, 6}
BLOCKS = " ▁▂▃▄▅▆▇█"  # index 0=space, 1–8 = U+2581–U+2588

# [GRADIENT_RGB]
# Baked at install from actual terminal palette via OSC 4 (pct, r, g, b).
_GRADIENT_RGB: list[tuple[int, int, int, int]] | None = None
# [/GRADIENT_RGB]

# Fallback RGB stops used when _GRADIENT_RGB is not baked.
_GRADIENT_FALLBACK: list[tuple[int, int, int, int]] = [
    (0,   95, 135, 255),  # blue   ≈ C.bar_ok
    (70, 255, 175,   0),  # orange ≈ C.bar_warn
    (90, 255,   0,   0),  # red    ≈ C.bar_crit
]


def _pct_color(pct: float) -> str:
    """Smooth truecolor gradient: bar_ok (0%) → bar_warn (70%) → bar_crit (90%+)."""
    stops = _GRADIENT_RGB or _GRADIENT_FALLBACK
    if pct <= stops[0][0]:
        _, r, g, b = stops[0]
        return f"\x1b[38;2;{r};{g};{b}m"
    if pct >= stops[-1][0]:
        _, r, g, b = stops[-1]
        return f"\x1b[38;2;{r};{g};{b}m"
    for i in range(len(stops) - 1):
        p0, r0, g0, b0 = stops[i]
        p1, r1, g1, b1 = stops[i + 1]
        if p0 <= pct <= p1:
            t = (pct - p0) / (p1 - p0)
            return f"\x1b[38;2;{round(r0+t*(r1-r0))};{round(g0+t*(g1-g0))};{round(b0+t*(b1-b0))}m"
    _, r, g, b = stops[-1]
    return f"\x1b[38;2;{r};{g};{b}m"


CACHE_PATH = Path(__file__).parent / "usage-cache.json"
FETCH_LOCK_PATH = Path(__file__).parent / "usage-fetch.lock"

# ─── ANSI helpers ─────────────────────────────────────────────────────────────

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

RST = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"


class C:
    # [COLORS]
    border = "\x1b[38;5;245m"
    label = "\x1b[38;5;252m"
    model = "\x1b[38;5;183m"
    effort = "\x1b[38;5;250m"
    git = "\x1b[38;5;215m"
    git_add = "\x1b[38;5;77m"
    bar_ok = "\x1b[38;5;75m"
    bar_warn = "\x1b[38;5;214m"
    bar_crit = "\x1b[38;5;196m"
    bar_bg = "\x1b[38;5;238m"
    bracket = "\x1b[38;5;248m"
    divider = "\x1b[38;5;242m"
    # [/COLORS]


def vlen(s: str) -> int:
    """Visual length: strip ANSI codes before measuring."""
    return len(ANSI_RE.sub("", s))


class Span:
    """Paired (plain, colored) text; concatenate with +."""

    __slots__ = ("plain", "colored")

    def __init__(self, text: str, color: str = "") -> None:
        self.plain = text
        self.colored = f"{color}{text}{RST}" if color else text

    def __add__(self, other: "Span") -> "Span":
        s = Span.__new__(Span)
        s.plain = self.plain + other.plain
        s.colored = self.colored + other.colored
        return s

    @property
    def width(self) -> int:
        return len(self.plain)

    def truncate(self, max_width: int) -> "Span":
        if self.width <= max_width:
            return self
        return Span(self.plain[: max_width - 1] + "…")


# ─── Terminal / input helpers ─────────────────────────────────────────────────


def _term_width() -> int:
    try:
        fd = os.open("/dev/tty", os.O_RDONLY)
        try:
            return os.get_terminal_size(fd).columns
        finally:
            os.close(fd)
    except OSError:
        pass
    try:
        result = subprocess.run(
            ["tput", "cols"],
            capture_output=True,
            text=True,
            timeout=1,
        )
        if result.returncode == 0:
            w = int(result.stdout.strip())
            if w > 0:
                return w
    except Exception:
        pass
    try:
        w = int(os.environ.get("COLUMNS", 0))
        if w > 0:
            return w
    except ValueError:
        pass
    return 80


def _load_input() -> dict:
    try:
        raw = sys.stdin.read().strip()
        return json.loads(raw) if raw else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _load_usage_cache() -> dict | None:
    try:
        return json.loads(CACHE_PATH.read_text())
    except Exception:
        return None


def _is_stale(cache: dict | None) -> bool:
    if not cache or "fetchedAt" not in cache:
        return True
    fetched = datetime.fromisoformat(cache["fetchedAt"].replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - fetched).total_seconds() > STALE_SECONDS


def _spawn_fetcher() -> None:
    candidate = Path(__file__).parent / "usage_fetch.py"
    if candidate.exists():
        token = _claim_fetch_lock()
        if not token:
            return
        try:
            env = os.environ.copy()
            env["CCSL_FETCH_LOCK_TOKEN"] = token
            subprocess.Popen(
                [sys.executable, str(candidate)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
                env=env,
            )
        except Exception:
            _clear_fetch_lock(token)


def _lock_age_seconds(lock: dict | None) -> float | None:
    if not lock:
        return None
    created_at = lock.get("createdAt")
    if not isinstance(created_at, str):
        return None
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - created).total_seconds()


def _read_fetch_lock() -> dict | None:
    try:
        return json.loads(FETCH_LOCK_PATH.read_text())
    except Exception:
        return None


def _clear_fetch_lock(token: str) -> None:
    lock = _read_fetch_lock()
    if lock and lock.get("token") != token:
        return
    try:
        FETCH_LOCK_PATH.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _claim_fetch_lock() -> str | None:
    for _ in range(2):
        token = uuid.uuid4().hex
        payload = json.dumps(
            {
                "createdAt": datetime.now(timezone.utc).isoformat(),
                "token": token,
            }
        )
        try:
            fd = os.open(FETCH_LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            age = _lock_age_seconds(_read_fetch_lock())
            if age is not None and age < FETCH_LOCK_SECONDS:
                return None
            try:
                FETCH_LOCK_PATH.unlink()
            except FileNotFoundError:
                continue
            except OSError:
                return None
            continue

        try:
            with os.fdopen(fd, "w") as f:
                f.write(payload)
            return token
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            _clear_fetch_lock(token)
            return None
    return None


def _effort_level() -> str | None:
    try:
        p = Path.home() / ".claude" / "settings.json"
        return json.loads(p.read_text()).get("effortLevel")
    except Exception:
        return None


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{round(n / 1000)}k"
    return str(n)


def _git_shortstat(cwd: str, cached: bool) -> tuple[int, int, int]:
    """Returns (files_changed, insertions, deletions) from git diff --shortstat."""
    args = ["git", "--no-optional-locks", "-C", cwd, "diff"]
    if cached:
        args.append("--cached")
    args += ["--shortstat", "--"]
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=3).stdout.strip()
    except Exception:
        return 0, 0, 0
    if not out:
        return 0, 0, 0
    files, ins, dels = 0, 0, 0
    for part in out.split(","):
        part = part.strip()
        words = part.split()
        try:
            n = int(words[0])
        except (IndexError, ValueError):
            continue
        if "file" in part:
            files = n
        elif "insertion" in part:
            ins = n
        elif "deletion" in part:
            dels = n
    return files, ins, dels


def _git_change_summary(cwd: str, untracked: int) -> str:
    sf, si, sd = _git_shortstat(cwd, cached=True)
    uf, ui, ud = _git_shortstat(cwd, cached=False)
    files = sf + uf
    insertions = si + ui
    deletions = sd + ud
    pieces = []
    if insertions > 0:
        pieces.append(f"+{insertions}")
    if deletions > 0:
        pieces.append(f"-{deletions}")
    if not pieces and files > 0:
        pieces.append(f"{files} files")
    if untracked > 0:
        pieces.append(f"{untracked} untracked")
    return ", ".join(pieces) if pieces else "dirty"


def _git_info(cwd: str) -> dict | None:
    try:
        stdout = subprocess.run(
            [
                "git",
                "--no-optional-locks",
                "-C",
                cwd,
                "status",
                "--porcelain=2",
                "--branch",
                "--untracked-files=normal",
                "--ignore-submodules=dirty",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except subprocess.CalledProcessError:
        return None

    branch: str | None = None
    detached_oid: str | None = None
    dirty = False
    untracked = 0

    for line in stdout.splitlines():
        if line.startswith("# branch.head "):
            head = line.removeprefix("# branch.head ").strip()
            branch = None if head == "(detached)" else head
        elif line.startswith("# branch.oid "):
            oid = line.removeprefix("# branch.oid ").strip()
            if oid and oid != "(initial)":
                detached_oid = oid[:7]
        elif line.startswith("? "):
            untracked += 1
            dirty = True
        elif line and not line.startswith("#"):
            dirty = True

    return {
        "branch": branch or detached_oid,
        "dirty": dirty,
        "summary": _git_change_summary(cwd, untracked) if dirty else "clean",
    }


# ─── Graph renderer ───────────────────────────────────────────────────────────


def _build_columns(
    history: list,
    session_start_ms: float,
    session_end_ms: float,
    fill_until_ms: float,
    num_cols: int,
) -> list[float]:
    cols: list[float] = [float("nan")] * num_cols
    duration = session_end_ms - session_start_ms
    if duration <= 0:
        return cols
    for point in history:
        try:
            t = datetime.fromisoformat(point["ts"].replace("Z", "+00:00")).timestamp() * 1000
        except Exception:
            continue
        if t < session_start_ms or t > session_end_ms:
            continue
        idx = min(num_cols - 1, int((t - session_start_ms) / duration * num_cols))
        cols[idx] = point["pct"]
    # Fill elapsed columns up to now:
    #   • gap between two known values → linear interpolation
    #   • leading gap (no left neighbour) → 0.0 (no usage yet)
    #   • trailing elapsed gap (no right neighbour) → forward-fill
    clamped = max(session_start_ms, min(fill_until_ms, session_end_ms))
    fill_cols = min(num_cols, math.ceil((clamped - session_start_ms) / duration * num_cols))
    i = 0
    while i < fill_cols:
        if not math.isnan(cols[i]):
            i += 1
            continue
        j = i
        while j < fill_cols and math.isnan(cols[j]):
            j += 1
        left = cols[i - 1] if i > 0 else float("nan")
        if not math.isnan(left):
            for k in range(i, j):  # forward-fill from left neighbour
                cols[k] = left
        else:
            for k in range(i, j):  # leading gap: no usage yet
                cols[k] = 0.0
        i = j

    # Enforce monotonically increasing — usage within a window never goes down;
    # any API dip (e.g. rolling-window jitter) is clamped to the previous high.
    prev_real = float("nan")
    for i in range(num_cols):
        if not math.isnan(cols[i]):
            if not math.isnan(prev_real) and cols[i] < prev_real:
                cols[i] = prev_real
            prev_real = cols[i]

    return cols


def _render_line_graph(
    columns: list[float],
    current_pct: int,
    header_text: str = "",
) -> tuple[list[str], list[str]]:
    num_cols = len(columns)

    def pct_to_row(pct: float) -> int:
        return round((100 - pct) * (GRAPH_ROWS - 1) / 100)

    row_for_col: list[float] = [float("nan") if math.isnan(pct) else float(pct_to_row(pct)) for pct in columns]
    dot_color = _pct_color(current_pct)

    last_data_col = -1
    for col in range(num_cols):
        if not math.isnan(row_for_col[col]):
            last_data_col = col

    last_val = float("nan")
    for col in range(last_data_col + 1):
        if not math.isnan(row_for_col[col]):
            last_val = row_for_col[col]
        elif not math.isnan(last_val):
            row_for_col[col] = last_val

    grid = _init_graph_grid(num_cols)

    for col in range(num_cols):
        row_c_f = row_for_col[col]
        if math.isnan(row_c_f):
            continue
        row_c = int(row_c_f)
        col_color = _pct_color(columns[col] if not math.isnan(columns[col]) else 0.0)

        prev_raw = row_for_col[col - 1] if col > 0 else float("nan")
        next_raw = row_for_col[col + 1] if col < num_cols - 1 else float("nan")
        prev_row = row_c if math.isnan(prev_raw) else int(prev_raw)
        next_row = row_c if math.isnan(next_raw) else int(next_raw)

        left_bend = not math.isnan(prev_raw) and prev_row < row_c
        right_bend = not math.isnan(next_raw) and row_c > next_row

        if col == last_data_col:
            _set_grid_cell(grid, row_c, col, "●", dot_color)
            if left_bend:
                prev_pct = columns[col - 1]
                _set_grid_cell(grid, prev_row, col, "╮", _pct_color(prev_pct if not math.isnan(prev_pct) else 0.0))
        elif left_bend and right_bend:
            prev_pct = columns[col - 1]
            next_pct = columns[col + 1]
            _set_grid_cell(grid, prev_row, col, "╮", _pct_color(prev_pct if not math.isnan(prev_pct) else 0.0))
            _set_grid_cell(grid, row_c, col, "─", col_color)
            _set_grid_cell(grid, next_row, col, "╭", _pct_color(next_pct if not math.isnan(next_pct) else 0.0))
        elif left_bend:
            prev_pct = columns[col - 1]
            _set_grid_cell(grid, prev_row, col, "╮", _pct_color(prev_pct if not math.isnan(prev_pct) else 0.0))
            _set_grid_cell(grid, row_c, col, "╰", col_color)
        elif right_bend:
            next_pct = columns[col + 1]
            _set_grid_cell(grid, row_c, col, "╯", col_color)
            _set_grid_cell(grid, next_row, col, "╭", _pct_color(next_pct if not math.isnan(next_pct) else 0.0))
        else:
            _set_grid_cell(grid, row_c, col, "─", col_color)

        if left_bend:
            for r in range(prev_row + 1, row_c):
                _set_grid_cell(grid, r, col, "│", _pct_color((GRAPH_ROWS - 1 - r) * 100.0 / (GRAPH_ROWS - 1)))
        if right_bend:
            for r in range(next_row + 1, row_c):
                _set_grid_cell(grid, r, col, "│", _pct_color((GRAPH_ROWS - 1 - r) * 100.0 / (GRAPH_ROWS - 1)))

    if last_data_col >= 0 and not math.isnan(row_for_col[last_data_col]):
        max_lbl = num_cols - 2
        header_end_col = len(header_text[:max_lbl]) + 2 if header_text else 0
        _place_label(
            grid,
            dot_row=int(row_for_col[last_data_col]),
            dot_col=last_data_col,
            label_str=f"{current_pct}%",
            color=dot_color,
            header_end_col=header_end_col,
        )

    _apply_graph_header(grid, header_text)
    return _finalize_graph_grid(grid)


def _render_bar_graph(
    columns: list[float],
    header_text: str = "",
) -> tuple[list[str], list[str]]:
    num_cols = len(columns)
    grid = _init_graph_grid(num_cols)

    for col, pct in enumerate(columns):
        if math.isnan(pct):
            continue
        pct = max(0.0, min(100.0, pct))
        color = _pct_color(pct)
        total_eighths = round(pct * GRAPH_ROWS * 8 / 100)
        full_rows = total_eighths // 8
        partial = total_eighths % 8
        for r in range(GRAPH_ROWS - full_rows, GRAPH_ROWS):
            _set_grid_cell(grid, r, col, "█", color)
        partial_row = GRAPH_ROWS - full_rows - 1
        if partial > 0 and partial_row >= 0:
            _set_grid_cell(grid, partial_row, col, BLOCKS[partial], color)

    _apply_graph_header(grid, header_text)
    return _finalize_graph_grid(grid)


def _empty_graph(num_cols: int, header_text: str = "") -> tuple[list[str], list[str]]:
    grid = _init_graph_grid(num_cols)

    msg = "no data"
    start = max(0, (num_cols - len(msg)) // 2)
    for i, ch in enumerate(msg):
        col = start + i
        if col < num_cols:
            _set_grid_cell(grid, 2, col, ch, C.label)

    _apply_graph_header(grid, header_text)
    return _finalize_graph_grid(grid)


def _init_graph_grid(num_cols: int) -> list[list[tuple[str, str | None]]]:
    grid: list[list[tuple[str, str | None]]] = [[(" ", None)] * num_cols for _ in range(GRAPH_ROWS)]
    for _gr in _GRID_ROWS_SET:
        for _gc in range(num_cols):
            grid[_gr][_gc] = (_GRID_CHAR, C.divider)
    return grid


def _set_grid_cell(
    grid: list[list[tuple[str, str | None]]],
    row: int,
    col: int,
    ch: str,
    color: str | None,
) -> None:
    if 0 <= row < GRAPH_ROWS and 0 <= col < len(grid[0]):
        grid[row][col] = (ch, color)


def _place_label(
    grid: list[list[tuple[str, str | None]]],
    dot_row: int,
    dot_col: int,
    label_str: str,
    color: str,
    header_end_col: int,
) -> None:
    num_cols = len(grid[0])
    llen = len(label_str)
    candidates = [
        (dot_row, dot_col + 1),
        (dot_row - 1, dot_col - llen // 2),
        (dot_row + 1, dot_col - llen // 2),
        (dot_row, dot_col - llen - 1),
    ]
    for row, start_col in candidates:
        if not (0 <= row < GRAPH_ROWS):
            continue
        start_col = max(0, start_col)
        end_col = start_col + llen
        if end_col > num_cols:
            start_col = num_cols - llen
            end_col = num_cols
        if start_col < 0:
            continue
        cols = range(start_col, end_col)
        if row == 0 and any(c < header_end_col for c in cols):
            continue
        if any(c == dot_col for c in cols) and row == dot_row:
            continue
        for i, ch in enumerate(label_str):
            grid[row][start_col + i] = (ch, color)
        return


def _apply_graph_header(grid: list[list[tuple[str, str | None]]], header_text: str) -> None:
    if not header_text:
        return
    num_cols = len(grid[0])
    grid[0][0] = ("[", C.divider)
    max_lbl = num_cols - 2
    for i, ch in enumerate(header_text[:max_lbl]):
        grid[0][i + 1] = (ch, C.label)
    close = len(header_text[:max_lbl]) + 1
    if close < num_cols:
        grid[0][close] = ("]", C.divider)


def _finalize_graph_grid(grid: list[list[tuple[str, str | None]]]) -> tuple[list[str], list[str]]:
    plain_rows = ["".join(ch for ch, _ in row) for row in grid]
    colored_rows = ["".join((color + ch + RST if color else " ") for ch, color in row) for row in grid]
    return plain_rows, colored_rows


def _graph_rows(
    history: list,
    window_start_ms: float,
    window_end_ms: float,
    fill_until_ms: float,
    current_pct: int,
    num_cols: int,
    header_text: str = "",
    bar: bool = False,
) -> tuple[list[str], list[str]]:
    if not (isinstance(history, list) and history):
        return _empty_graph(num_cols, header_text)
    cols = _build_columns(history, window_start_ms, window_end_ms, fill_until_ms, num_cols)
    return _render_bar_graph(cols, header_text) if bar else _render_line_graph(cols, current_pct, header_text)


def _fmt_12h(ts_ms: float) -> str:
    d = datetime.fromtimestamp(ts_ms / 1000)
    h = d.hour
    suffix = "am" if h < 12 else "pm"
    h12 = h % 12 or 12
    return f"{h12}{suffix}"


def _fmt_day_date(ts_ms: float) -> str:
    d = datetime.fromtimestamp(ts_ms / 1000)
    return d.strftime("%a") + " " + str(d.day)


# ─── Renderers ────────────────────────────────────────────────────────────────


def _truncate_segments(segments: list["Span"], max_width: int) -> str:
    """Render segments with color, truncating the last fitting segment with …"""
    available = max_width
    parts: list[str] = []
    for s in segments:
        if s.width <= available:
            parts.append(s.colored)
            available -= s.width
        else:
            if available > 1:
                parts.append(s.plain[: available - 1] + "…")
            elif available == 1:
                parts.append("…")
            break
    return "".join(parts)


def render_line(ctx: dict, safe_width: int) -> str:
    """Build and return the single-line status row."""
    # Model segment
    model = ctx.get("model", "Unknown")
    effort = ctx.get("effort")
    if effort:
        model_span = Span(model, C.model) + Span(" · ", C.border) + Span(effort, C.effort)
    else:
        model_span = Span(model, C.model)

    # Context segment — full (with bar) and compact (stats only)
    used_pct = ctx.get("used_pct")
    if used_pct is not None:
        used_int = round(used_pct)
        bar_color = C.bar_crit if used_int >= 90 else C.bar_warn if used_int >= 70 else C.bar_ok
        ctx_size = ctx.get("ctx_size", 0)
        used_tok = round(used_int / 100 * ctx_size) if ctx_size else 0
        used_str = _fmt_tokens(used_tok)
        total_str = _fmt_tokens(ctx_size) if ctx_size else "?"
        in_str = _fmt_tokens(ctx.get("input_tokens") or 0)
        out_str = _fmt_tokens(ctx.get("output_tokens") or 0)
        _io = (
            Span(" · ", C.border)
            + Span("↓", C.bar_ok)
            + Span(in_str, C.label)
            + Span(" / ", C.border)
            + Span("↑", C.bar_warn)
            + Span(out_str, C.label)
        )
        filled = min(BAR_WIDTH, round(used_int * BAR_WIDTH / 100))
        _stats_full = (
            Span("  [", C.bracket)
            + Span(f"{used_str}/{total_str}", C.label)
            + Span("] ", C.bracket)
            + Span(f"{used_int}%", bar_color)
            + _io
        )
        _stats_compact = Span(f"{used_int}%", bar_color) + _io
        bar_full = (
            Span("▰" * filled, bar_color)
            + Span("▱" * (BAR_WIDTH - filled), C.bar_bg)
            + _stats_full
        )
        bar_compact = _stats_compact
    else:
        bar_full = Span("▱" * BAR_WIDTH, C.bar_bg)
        bar_compact = Span("ctx: ?", C.label)

    # Git segment
    git = ctx.get("git")
    if git:
        branch = git.get("branch") or "?"
        dirty = git.get("dirty", False)
        summary = git.get("summary", "clean")
        if not dirty:
            change_span = Span(summary, C.bar_ok)
        else:
            change_span = Span.__new__(Span)
            change_span.plain = ""
            change_span.colored = ""
            for i, token in enumerate(summary.split(", ")):
                if i > 0:
                    change_span = change_span + Span(", ", C.border)
                if token.startswith("+"):
                    color = C.git_add
                elif token.startswith("-"):
                    color = C.bar_crit
                else:
                    color = C.bar_warn
                change_span = change_span + Span(token, color)
        git_span = (
            Span("⎇ ", C.git)
            + Span(branch, BOLD + C.git)
            + Span(" ", "")
            + Span("(", C.bracket)
            + change_span
            + Span(")", C.bracket)
        )
    else:
        git_span = Span("no repo", C.label)

    sep = Span("  ") + Span("|", C.border) + Span("  ")

    def _sep(total: int) -> Span:
        l = max(0, (total - 1) // 2)
        r = max(0, total - 1 - l)
        return Span(" " * l) + Span("|", C.border) + Span(" " * r)

    def _spread(mid: Span) -> str | None:
        remaining = safe_width - model_span.width - mid.width - git_span.width
        if remaining < 2:
            return None
        per_gap = remaining // 2
        leftover = remaining - per_gap * 2
        return (model_span + _sep(per_gap + leftover) + mid + _sep(per_gap) + git_span).colored

    # Adaptive layout: full bar → compact (no bar) → truncate; always spread to fill safe_width
    return (
        _spread(bar_full)
        or _spread(bar_compact)
        or _truncate_segments([model_span, sep, bar_compact, sep, git_span], safe_width)
    )


def _render_compact_graphs(cache: dict, safe_width: int, stale: bool) -> list[str]:
    """Two-line compact fallback: dot-fill progress bars for 5h and 7d windows."""
    bar_w = min(12, max(4, safe_width // 6))
    now_ms = datetime.now(timezone.utc).timestamp() * 1000

    def _parse_ms(s: str | None) -> float:
        if not s:
            return now_ms
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000

    def _fmt_reset_in(resets_ms: float) -> str:
        secs = max(0.0, (resets_ms - now_ms) / 1000)
        if secs < 60:
            return "<1m"
        mins = int(secs // 60)
        hours, mins = divmod(mins, 60)
        days, hours = divmod(hours, 24)
        if days > 0:
            return f"{days}d{hours}h" if hours else f"{days}d"
        if hours > 0:
            return f"{hours}h{mins}m" if mins else f"{hours}h"
        return f"{mins}m"

    def _make_bar(pct: int, width: int) -> "Span":
        filled = min(width, round(pct * width / 100))
        color = C.bar_crit if pct >= 90 else C.bar_warn if pct >= 70 else C.bar_ok
        return Span("●" * filled, color) + Span("○" * (width - filled), C.bar_bg)

    sess = cache.get("session") or {}
    week = cache.get("weekly") or {}
    sess_pct = round(sess.get("utilization", 0))
    week_pct = round(week.get("utilization", 0))
    sess_end_ms = _parse_ms(sess.get("resetsAt"))
    week_end_ms = _parse_ms(week.get("resetsAt"))

    stale_span = Span(" · stale", C.label + DIM) if stale else Span("")

    def _line(label: str, pct: int, reset_ms: float) -> str:
        pct_color = C.bar_crit if pct >= 90 else C.bar_warn if pct >= 70 else C.bar_ok
        span = (
            Span(label, C.label)
            + _make_bar(pct, bar_w)
            + Span("  ", "")
            + Span(f"{pct:3d}%", pct_color)
            + Span(" · reset in ", C.border)
            + Span(_fmt_reset_in(reset_ms), pct_color)
            + stale_span
        )
        return span.colored

    return [
        _line("5h: ", sess_pct, sess_end_ms),
        _line("7d: ", week_pct, week_end_ms),
    ]


def render_graphs(ctx: dict, safe_width: int, bar: bool = False) -> list[str]:
    """Build graph rows (line or bar style): header + GRAPH_ROWS lines, side-by-side."""
    cache = ctx.get("usage_cache") or {}
    stale = bool(ctx.get("usage_stale"))

    if safe_width < COMPACT_GRAPH_W:
        return _render_compact_graphs(cache, safe_width, stale)

    nc = max(8, (safe_width - 3) // 2)
    now_ms = datetime.now(timezone.utc).timestamp() * 1000

    sess = cache.get("session") or {}
    week = cache.get("weekly") or {}
    sess_pct = round(sess.get("utilization", 0))
    week_pct = round(week.get("utilization", 0))

    def _parse_ms(resets_at: str | None) -> float:
        if not resets_at:
            return now_ms
        return datetime.fromisoformat(resets_at.replace("Z", "+00:00")).timestamp() * 1000

    sess_end_ms = _parse_ms(sess.get("resetsAt"))
    sess_start_ms = sess_end_ms - 5 * 3600 * 1000
    week_end_ms = _parse_ms(week.get("resetsAt"))
    week_start_ms = week_end_ms - 7 * 24 * 3600 * 1000

    stale_suffix = " · stale" if stale else ""
    pct_suffix = f"  {sess_pct}%" if bar else ""
    lbl5 = f"5h usage: {_fmt_12h(sess_start_ms)} / {_fmt_12h(sess_end_ms)}{pct_suffix}{stale_suffix}"
    pct_suffix = f"  {week_pct}%" if bar else ""
    lbl7 = f"7d usage: {_fmt_day_date(week_start_ms)} / {_fmt_day_date(week_end_ms)}{pct_suffix}{stale_suffix}"

    _, sc = _graph_rows(cache.get("history") or [], sess_start_ms, sess_end_ms, now_ms, sess_pct, nc, lbl5, bar)
    _, wc = _graph_rows(cache.get("weeklyHistory") or [], week_start_ms, week_end_ms, now_ms, week_pct, nc, lbl7, bar)

    sep = " " + C.divider + "│" + RST + " "
    return [sc[r] + sep + wc[r] for r in range(GRAPH_ROWS)]


# ─── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    data = _load_input()

    cwd = (data.get("workspace") or {}).get("current_dir") or data.get("cwd") or os.getcwd()

    cache = _load_usage_cache()
    stale = _is_stale(cache)
    if stale:
        _spawn_fetcher()

    ctx_w = data.get("context_window") or {}
    ctx = {
        "cwd": cwd,
        "model": (data.get("model") or {}).get("display_name") or "Unknown Model",
        "effort": _effort_level(),
        "used_pct": ctx_w.get("used_percentage"),
        "ctx_size": ctx_w.get("context_window_size", 0),
        "input_tokens": ctx_w.get("total_input_tokens"),
        "output_tokens": ctx_w.get("total_output_tokens"),
        "git": _git_info(cwd),
        "usage_cache": cache,
        "usage_stale": stale and cache is not None,
    }

    tw = _term_width()
    safe = min(max(1, tw - SAFE_MARGIN), MAX_WIDTH)
    if safe % 2 == 0:  # snap to 2n+3 form so graph width == safe exactly
        safe -= 1

    line1 = render_line(ctx, safe_width=safe)
    divider = C.border + "─" * safe + RST
    # [GRAPH_TYPE]
    graph_lines = render_graphs(ctx, safe_width=safe)
    # [/GRAPH_TYPE]

    print()
    for line in [line1, divider] + graph_lines:
        print(RST + line)


if __name__ == "__main__":
    main()
