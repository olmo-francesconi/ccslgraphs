#!/usr/bin/env python3
"""Claude Code statusline installer (Python version)."""

import argparse
import importlib.util
import json
import os
import re
import select
import shlex
import shutil
import subprocess
import sys
import tempfile
import termios
import tty
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

REMOTE_BASE = "https://raw.githubusercontent.com/olmo-francesconi/ccslgraphs/main"
SCRIPTS = ["statusline.py", "usage_fetch.py"]

_M  = "\x1b[35m"  # magenta
_D  = "\x1b[2m"   # dim
_OK = "\x1b[32m"  # green — success
_WN = "\x1b[33m"  # yellow — warning / skip
_R  = "\x1b[0m"   # reset

_HEADER = (
    "\n"
    f"  {_M}┌─┐┌─┐┌─┐┬  ┌─┐┬─┐┌─┐┌─┐┬ ┬┌─┐{_R}\n"
    f"  {_M}│  │  └─┐│  │ ┬├┬┘├─┤├─┘├─┤└─┐{_R}\n"
    f"  {_M}└─┘└─┘└─┘┴─┘└─┘┴└─┴ ┴┴  ┴ ┴└─┘{_R}\n"
    "\n"
    "  Claude Code statusline installer\n"
)


def print_header() -> None:
    print(_HEADER, end="")


def _step_header(step: int, total: int, title: str) -> None:
    """Clear terminal, redraw logo, and print step indicator."""
    print("\x1b[2J\x1b[H", end="", flush=True)
    print_header()
    print(f"  {_D}{step} / {total}  —  {title}{_R}\n")


def _raw_input(prompt: str) -> str | None:
    """Raw-mode line input from /dev/tty. Returns None on ESC/Ctrl-C/Ctrl-D."""
    try:
        with open("/dev/tty", "r+b", buffering=0) as tty_fh:
            fd = tty_fh.fileno()
            old = termios.tcgetattr(fd)
            tty.setraw(fd)
            try:
                tty_fh.write(prompt.encode())
                tty_fh.flush()
                buf = ""
                while True:
                    ch = tty_fh.read(1)
                    if ch == b"\x1b":
                        # Escape sequence (arrow keys etc.) → swallow and continue
                        if select.select([tty_fh], [], [], 0.05)[0]:
                            while select.select([tty_fh], [], [], 0.05)[0]:
                                tty_fh.read(1)
                            continue
                        # Bare ESC → cancel
                        tty_fh.write(b"\r\n")
                        tty_fh.flush()
                        return None
                    if ch in (b"\x03", b"\x04"):  # Ctrl-C, Ctrl-D
                        tty_fh.write(b"\r\n")
                        tty_fh.flush()
                        return None
                    if ch in (b"\r", b"\n"):
                        tty_fh.write(b"\r\n")
                        tty_fh.flush()
                        return buf
                    if ch in (b"\x7f", b"\x08"):  # Backspace / DEL
                        if buf:
                            buf = buf[:-1]
                            tty_fh.write(b"\x08 \x08")
                            tty_fh.flush()
                    elif ch >= b" ":  # printable
                        buf += ch.decode("utf-8", errors="replace")
                        tty_fh.write(ch)
                        tty_fh.flush()
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
    except SystemExit:
        raise
    except Exception:
        try:
            return input(prompt)
        except (EOFError, KeyboardInterrupt):
            return None


def _validate_install_dir(p: Path) -> str | None:
    """Return an error string if p is not a safe install target, else None."""
    if p.exists() and not p.is_dir():
        return f"{p} exists and is a file"
    ancestor = p
    while not ancestor.exists():
        ancestor = ancestor.parent
    if not os.access(ancestor, os.W_OK):
        return f"no write permission for {ancestor}"
    return None


def prompt_install_dir() -> Path:
    _step_header(1, 3, "Install location")
    default = Path.home() / ".claude"
    while True:
        result = _raw_input(f"  Parent directory [{default}]: ")
        if result is None:
            sys.exit("Installation cancelled.")
        raw = result.strip()
        parent = Path(raw).expanduser().resolve() if raw else default
        p = parent / "ccslgraphs"
        err = _validate_install_dir(p)
        if err is None:
            return p
        print(f"  {_WN}⚠{_R}  {err} — please try again.\n")


def _load_statusline_module(source_path: Path):
    spec = importlib.util.spec_from_file_location("statusline_preview", source_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _build_mock_ctx() -> dict:
    now = datetime.now(timezone.utc)

    # Session history: 20 points filling the elapsed part of the 5h window.
    # resetsAt = now+2h  →  window = now-3h … now+2h
    # 19 gaps × 9min = 171min < 180min, so last point lands at now-9min (inside fill_cols).
    sess_pcts = [0, 2, 5, 10, 18, 25, 30, 28, 22, 30, 38, 44, 50, 55, 52, 48, 56, 62, 58, 55]
    sess_reset = now + timedelta(hours=2)
    sess_start = sess_reset - timedelta(hours=5)
    history = [
        {"ts": (sess_start + timedelta(minutes=i * 9)).isoformat(), "pct": p}
        for i, p in enumerate(sess_pcts)
    ]

    # Weekly history: 18 points filling the full 7d window.
    # resetsAt = now+2d  →  window = now-5d … now+2d
    # 17 gaps × 7h = 119h ≈ 5d, so last point lands at ~now.
    week_pcts = [0, 4, 9, 15, 22, 30, 38, 45, 51, 57, 63, 68, 73, 78, 82, 86, 90, 94]
    week_reset = now + timedelta(days=2)
    week_start = week_reset - timedelta(days=7)
    weekly_history = [
        {"ts": (week_start + timedelta(hours=i * 7)).isoformat(), "pct": p}
        for i, p in enumerate(week_pcts)
    ]

    return {
        "cwd": Path.home(),
        "model": "claude-sonnet-4-6",
        "effort": None,
        "used_pct": 55,
        "ctx_size": 200000,
        "input_tokens": 110000,
        "output_tokens": None,
        "git": None,
        "usage_cache": {
            "session": {"utilization": 55, "resetsAt": sess_reset.isoformat()},
            "weekly": {"utilization": 94, "resetsAt": week_reset.isoformat()},
            "monthly": {"enabled": False, "usedCents": 0, "limitCents": 0},
            "history": history,
            "weeklyHistory": weekly_history,
        },
        "usage_stale": False,
    }


# Default 256-color escapes used when tput is unavailable in previews.
_DEFAULT_256: dict[int, str] = {
    7:  "\x1b[38;5;7m",
    8:  "\x1b[38;5;8m",
    9:  "\x1b[38;5;9m",
    10: "\x1b[38;5;10m",
    11: "\x1b[38;5;11m",
    12: "\x1b[38;5;12m",
    13: "\x1b[38;5;13m",
    14: "\x1b[38;5;14m",
    15: "\x1b[38;5;15m",
}

_GRADIENT_FALLBACK: list[tuple[int, int, int, int]] = [
    (0,   95, 135, 255),
    (70, 255, 175,   0),
    (90, 255,   0,   0),
]


def _gradient_esc(pct: float, stops: list[tuple[int, int, int, int]]) -> str:
    """Truecolor escape for pct interpolated through gradient stops."""
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
            return (
                f"\x1b[38;2;{round(r0+t*(r1-r0))};"
                f"{round(g0+t*(g1-g0))};"
                f"{round(b0+t*(b1-b0))}m"
            )
    _, r, g, b = stops[-1]
    return f"\x1b[38;2;{r};{g};{b}m"


def show_color_preview() -> None:
    """Print terminal color swatches and the usage gradient bar."""
    RST = "\x1b[0m"
    palette = _tput_colors()
    esc = palette if palette is not None else _DEFAULT_256

    # Named color swatches (exclude duplicates like bar_bg/divider/border)
    slots = [
        ("border", 8), ("model", 13), ("git", 14), ("git_add", 10),
        ("bar_ok", 12), ("bar_warn", 11), ("bar_crit", 9),
    ]
    row = "  "
    for name, idx in slots:
        color = esc.get(idx, "")
        row += f"{color}██{RST} {name}  "
    print(row)

    # Gradient bar (40 chars, 0%→100%)
    color_slots = [(0, 12), (70, 11), (90, 9)]
    stops: list[tuple[int, int, int, int]] = []
    for pct, tput_idx in color_slots:
        rgb = _query_osc4_rgb(tput_idx)
        if rgb is None:
            stops = []
            break
        stops.append((pct, *rgb))
    if not stops:
        stops = _GRADIENT_FALLBACK
    source = "terminal" if len(stops) == len(color_slots) else "fallback"

    n = 40
    bar = "  "
    for i in range(n):
        pct = i / (n - 1) * 100
        bar += f"{_gradient_esc(pct, stops)}█"
    bar += f"{RST}  0% → 100%  ({source} colors)"
    print(bar)
    print()


def _patch_module_colors(mod) -> None:
    """Patch the loaded module's C class and _GRADIENT_RGB with terminal-queried values."""
    palette = _tput_colors()
    if palette is not None:
        for name, idx in _COLOR_MAP.items():
            setattr(mod.C, name, palette[idx])

    color_slots = [(0, 12), (70, 11), (90, 9)]
    stops: list[tuple[int, int, int, int]] = []
    for pct, tput_idx in color_slots:
        rgb = _query_osc4_rgb(tput_idx)
        if rgb is None:
            stops = []
            break
        stops.append((pct, *rgb))
    if stops:
        mod._GRADIENT_RGB = stops


def show_graph_preview(mod, bar: bool, width: int) -> None:
    ctx = _build_mock_ctx()
    try:
        lines = mod.render_graphs(ctx, safe_width=width, bar=bar)
        for line in lines:
            print("  " + line)
    except Exception as e:
        print(f"  (preview unavailable: {e})")


def prompt_graph_style(mod) -> bool:
    """Show color preview + both graph previews; return True for bar, False for line."""
    try:
        width_raw = subprocess.check_output(
            ["tput", "cols"], stderr=subprocess.DEVNULL
        ).decode().strip()
        width = int(width_raw)
    except Exception:
        width = 120
    preview_width = min(width - 6, 118)  # -2 extra to account for "  " indent
    if preview_width % 2 == 0:
        preview_width -= 1

    # Non-TTY: default to line, skip all prompts
    try:
        tty_fh = open("/dev/tty", "r+b", buffering=0)
        tty_fh.close()
        is_tty = True
    except Exception:
        is_tty = False

    if not is_tty:
        return False

    _step_header(2, 3, "Graph style")
    print("  Terminal colors:")
    show_color_preview()

    _patch_module_colors(mod)

    print("  Style 1 — line:")
    show_graph_preview(mod, bar=False, width=preview_width)
    print("\n  Style 2 — bar:")
    show_graph_preview(mod, bar=True, width=preview_width)
    print()

    # Single-keypress via raw /dev/tty.
    # Loop until 1, 2, ESC, Ctrl-C, or Ctrl-D. Any other key rings bell.
    try:
        choice: bytes = b""
        with open("/dev/tty", "r+b", buffering=0) as tty_fh:
            fd = tty_fh.fileno()
            old = termios.tcgetattr(fd)
            tty.setraw(fd)
            try:
                tty_fh.write(b"  Choose graph style [1/2] (Esc to cancel): ")
                tty_fh.flush()
                while True:
                    ch = tty_fh.read(1)
                    if ch in (b"\x1b", b"\x03", b"\x04"):
                        tty_fh.write(b"\r\n")
                        tty_fh.flush()
                        sys.exit("Installation cancelled.")
                    if ch in (b"1", b"2"):
                        tty_fh.write(b"\r\n")
                        tty_fh.flush()
                        choice = ch
                        break
                    tty_fh.write(b"\x07")  # BEL — invalid key
                    tty_fh.flush()
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
        return choice == b"2"
    except SystemExit:
        raise
    except Exception:
        pass

    # Fallback: input(); loop until valid choice or cancel
    while True:
        try:
            val = input("Choose graph style [1/2] (empty to cancel): ").strip()
        except (EOFError, KeyboardInterrupt):
            sys.exit("Installation cancelled.")
        if not val:
            sys.exit("Installation cancelled.")
        if val in ("1", "2"):
            return val == "2"
        print("  Please enter 1 or 2.")


def prompt_confirm(install_dir: Path, use_bar: bool) -> None:
    """Show install summary and ask for final confirmation."""
    _step_header(3, 3, "Confirm")

    style_name = "bar" if use_bar else "line"
    entry = statusline_entry(install_dir)
    snippet = json.dumps({"statusLine": entry}, indent=2)

    source = "local" if _local_root() is not None else "remote"
    dir_ok = _validate_install_dir(install_dir) is None
    dir_mark = f"{_OK}✓{_R}" if dir_ok else f"{_WN}⚠{_R}"
    print(f"  Install into:  {dir_mark} {install_dir}")
    print(f"  Source:        {source}")
    print(f"  Graph style:   {style_name}")
    print(f"\n  settings.json preview:")
    for line in snippet.splitlines():
        print(f"    {_D}{line}{_R}")
    print()

    # Non-TTY: proceed without prompting
    try:
        open("/dev/tty", "r+b", buffering=0).close()
    except Exception:
        return

    try:
        with open("/dev/tty", "r+b", buffering=0) as tty_fh:
            fd = tty_fh.fileno()
            old = termios.tcgetattr(fd)
            tty.setraw(fd)
            try:
                tty_fh.write(b"  Ready to install? [y/n] (Esc to cancel): ")
                tty_fh.flush()
                while True:
                    ch = tty_fh.read(1)
                    if ch in (b"\x1b", b"\x03", b"\x04", b"n", b"N"):
                        tty_fh.write(b"\r\n")
                        tty_fh.flush()
                        sys.exit("Installation cancelled.")
                    if ch in (b"y", b"Y"):
                        tty_fh.write(b"\r\n")
                        tty_fh.flush()
                        break
                    tty_fh.write(b"\x07")  # BEL on invalid key
                    tty_fh.flush()
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
    except SystemExit:
        raise
    except Exception:
        try:
            val = input("  Ready to install? [y/n]: ").strip().lower()
            if val != "y":
                sys.exit("Installation cancelled.")
        except (EOFError, KeyboardInterrupt):
            sys.exit("Installation cancelled.")


def show_settings_preview(install_dir: Path) -> None:
    entry = statusline_entry(install_dir)
    snippet = json.dumps({"statusLine": entry}, indent=2)
    print("\nThe following will be written to ~/.claude/settings.json:")
    print(snippet)
    print()


def bake_graph_type(install_dir: Path, bar: bool) -> None:
    """Stamp the chosen graph style into the installed statusline.py."""
    script = install_dir / "statusline.py"
    text = script.read_text()
    replacement = f"    graph_lines = render_graphs(ctx, safe_width=safe, bar={bar})"
    text = re.sub(
        r"(    # \[GRAPH_TYPE\]\n).*?(\n    # \[/GRAPH_TYPE\])",
        lambda m: m.group(1) + replacement + m.group(2),
        text,
        flags=re.DOTALL,
    )
    script.write_text(text)
    style = "bar" if bar else "line"
    print(f"  {_OK}✓{_R} baked graph style: {style}")


def statusline_entry(install_dir: Path) -> dict:
    return {
        "type": "command",
        "command": f"python3 {shlex.quote(str(install_dir))}/statusline.py",
        "padding": 0,
    }


# Semantic color slots → tput color index (0–15).
# These are the terminal's "named" colors — the ones themes actually remap.
_COLOR_MAP: dict[str, int] = {
    "border":   8,   # bright black / dark gray
    "label":    15,  # bright white
    "model":    13,  # bright magenta
    "effort":   7,   # white
    "git":      14,  # bright cyan
    "git_add":  10,  # bright green
    "bar_ok":   12,  # bright blue
    "bar_warn": 11,  # bright yellow
    "bar_crit": 9,   # bright red
    "bar_bg":   8,   # bright black / dark gray
    "bracket":  7,   # white
    "divider":  8,   # bright black / dark gray
    "cached":   6,   # cyan
    "total":    3,   # yellow
}


def _tput_colors() -> dict[int, str] | None:
    """Return a dict of tput index → ANSI escape string, or None if tput fails."""
    needed = sorted(set(_COLOR_MAP.values()))
    result: dict[int, str] = {}
    for idx in needed:
        try:
            raw = subprocess.check_output(
                ["tput", "setaf", str(idx)], stderr=subprocess.DEVNULL
            )
            result[idx] = raw.decode("latin-1")
        except Exception:
            return None
    return result


def _query_osc4_rgb(idx: int) -> tuple[int, int, int] | None:
    """Query the terminal for the actual RGB of color index idx via OSC 4."""
    try:
        with open("/dev/tty", "r+b", buffering=0) as tty_fh:
            fd = tty_fh.fileno()
            old = termios.tcgetattr(fd)
            tty.setraw(fd)
            try:
                tty_fh.write(f"\x1b]4;{idx};?\x07".encode())
                tty_fh.flush()
                if not select.select([tty_fh], [], [], 0.5)[0]:
                    return None
                resp = b""
                while select.select([tty_fh], [], [], 0.1)[0]:
                    ch = tty_fh.read(1)
                    if not ch:
                        break
                    resp += ch
                    if resp.endswith(b"\x07") or resp.endswith(b"\x1b\\"):
                        break
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
        m = re.search(rb"rgb:([0-9a-fA-F]+)/([0-9a-fA-F]+)/([0-9a-fA-F]+)", resp)
        if not m:
            return None
        def parse_ch(s: bytes) -> int:
            v = int(s, 16)
            return v >> 8 if len(s) == 4 else v  # 16-bit → 8-bit
        return parse_ch(m.group(1)), parse_ch(m.group(2)), parse_ch(m.group(3))
    except Exception:
        return None


def bake_gradient(install_dir: Path) -> None:
    """Query terminal RGB for bar_ok/warn/crit and bake _GRADIENT_RGB into the script."""
    color_slots = [(0, 12), (70, 11), (90, 9)]  # (pct, tput-index) matching _COLOR_MAP
    stops: list[tuple[int, int, int, int]] = []
    for pct, idx in color_slots:
        rgb = _query_osc4_rgb(idx)
        if rgb is None:
            print(f"  {_WN}⚠{_R} OSC 4 query failed — skipping gradient bake")
            return
        stops.append((pct, *rgb))

    script = install_dir / "statusline.py"
    text = script.read_text()
    text = re.sub(
        r"(# \[GRADIENT_RGB\]\n).*?(\n# \[/GRADIENT_RGB\])",
        lambda m: m.group(1) + f"_GRADIENT_RGB: list[tuple[int, int, int, int]] | None = {stops!r}" + m.group(2),
        text,
        flags=re.DOTALL,
    )
    script.write_text(text)
    print(f"  {_OK}✓{_R} baked gradient")


def bake_colors(install_dir: Path) -> None:
    """Stamp tput-resolved colors into the installed statusline.py."""
    palette = _tput_colors()
    if palette is None:
        print(f"  {_WN}⚠{_R} tput unavailable — keeping default 256-color codes")
        return

    script = install_dir / "statusline.py"
    text = script.read_text()

    new_lines: list[str] = []
    for name, idx in _COLOR_MAP.items():
        seq = palette[idx]
        literal = repr(seq)[1:-1]  # strip outer quotes; gives \\x1b[...
        new_lines.append(f'    {name} = "{literal}"')

    block = "\n".join(new_lines)
    text = re.sub(
        r"(# \[COLORS\]\n).*?(\n    # \[/COLORS\])",
        lambda m: m.group(1) + block + m.group(2),
        text,
        flags=re.DOTALL,
    )
    script.write_text(text)
    print(f"  {_OK}✓{_R} baked terminal colors")


def _local_root() -> Path | None:
    """Return the repo root if this script is running from a local checkout, else None."""
    try:
        here = Path(__file__).resolve().parent
        if all((here / f).exists() for f in SCRIPTS):
            return here
    except Exception:
        pass
    return None


def _download_scripts(dest_dir: Path) -> None:
    """Download SCRIPTS from remote into dest_dir (no output)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    for filename in SCRIPTS:
        url = f"{REMOTE_BASE}/{filename}"
        try:
            with urllib.request.urlopen(url) as r:
                (dest_dir / filename).write_bytes(r.read())
        except Exception as e:
            sys.exit(f"Failed to download {filename}: {e}")


def install_scripts(stage_dir: Path, install_dir: Path) -> None:
    """Copy staged scripts into install_dir and mark executable."""
    install_dir.mkdir(parents=True, exist_ok=True)
    for filename in SCRIPTS:
        dest = install_dir / filename
        shutil.copy2(stage_dir / filename, dest)
        dest.chmod(0o755)
        print(f"  {_OK}✓{_R} installed {filename}")


def patch_settings(install_dir: Path, settings_path: Path | None = None) -> None:
    if settings_path is None:
        settings_path = Path.home() / ".claude" / "settings.json"
    if settings_path.exists():
        with open(settings_path) as f:
            settings = json.load(f)
    else:
        settings = {}

    entry = statusline_entry(install_dir)

    if settings.get("statusLine") == entry:
        print(f"  {_D}· settings.json already up to date{_R}")
        return

    settings["statusLine"] = entry
    tmp = settings_path.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    os.replace(tmp, settings_path)
    print(f"  {_OK}✓{_R} patched ~/.claude/settings.json")


def main(argv: list[str] | None = None, _settings_path: Path | None = None) -> None:
    if sys.version_info < (3, 11):
        sys.exit("Python 3.11+ required")

    parser = argparse.ArgumentParser(description="Install Claude Code statusline")
    parser.add_argument(
        "--dir",
        default=None,
        help="Installation directory (default: ~/.claude/ccslgraphs)",
    )
    style_group = parser.add_mutually_exclusive_group()
    style_group.add_argument(
        "--bar", dest="style", action="store_const", const="bar",
        help="Use bar graph style (non-interactive)",
    )
    style_group.add_argument(
        "--line", dest="style", action="store_const", const="line",
        help="Use line graph style (non-interactive)",
    )
    args = parser.parse_args(argv)

    # Resolve install dir
    if args.dir is not None:
        install_dir = Path(args.dir).expanduser().resolve()
    else:
        install_dir = prompt_install_dir()

    # Stage scripts for preview without touching install_dir yet
    local = _local_root()
    tmp = None
    if local is not None:
        stage_dir = local
    else:
        print("\x1b[2J\x1b[H", end="", flush=True)
        print_header()
        print(f"  {_D}Fetching scripts for preview…{_R}\n")
        tmp = tempfile.TemporaryDirectory()
        stage_dir = Path(tmp.name)
        _download_scripts(stage_dir)

    # Load module for preview (best-effort)
    try:
        mod = _load_statusline_module(stage_dir / "statusline.py")
    except Exception:
        mod = None

    # Resolve graph style
    if args.style == "bar":
        use_bar = True
    elif args.style == "line":
        use_bar = False
    elif mod is not None:
        use_bar = prompt_graph_style(mod)
    else:
        use_bar = False

    prompt_confirm(install_dir, use_bar)

    print("\x1b[2J\x1b[H", end="", flush=True)
    print_header()
    print(f"  {_D}Installing…{_R}\n")
    install_scripts(stage_dir, install_dir)
    if tmp is not None:
        tmp.cleanup()
    bake_colors(install_dir)
    bake_gradient(install_dir)
    bake_graph_type(install_dir, bar=use_bar)
    patch_settings(install_dir, _settings_path)
    print(f"\n  {_OK}Done{_R} — restart Claude Code to activate.")


if __name__ == "__main__":
    main()
