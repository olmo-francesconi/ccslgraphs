#!/usr/bin/env python3
"""Claude Code statusline installer (Python version)."""

import argparse
import json
import os
import re
import select
import shutil
import subprocess
import sys
import termios
import tty
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
SCRIPTS = ["statusline.py", "usage_fetch.py"]


def statusline_entry(install_dir: Path) -> dict:
    return {
        "type": "command",
        "command": f"python3 {install_dir}/statusline.py",
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
            print("  OSC 4 query failed — skipping gradient bake")
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
    r0, g0, b0 = stops[0][1:]
    r1, g1, b1 = stops[1][1:]
    r2, g2, b2 = stops[2][1:]
    print(f"  baked gradient RGB: ok=#{r0:02x}{g0:02x}{b0:02x} warn=#{r1:02x}{g1:02x}{b1:02x} crit=#{r2:02x}{g2:02x}{b2:02x}")


def bake_colors(install_dir: Path) -> None:
    """Stamp tput-resolved colors into the installed statusline.py."""
    palette = _tput_colors()
    if palette is None:
        print("  tput unavailable — keeping default 256-color codes")
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
    print("  baked terminal colors into statusline.py")


def copy_scripts(install_dir: Path) -> None:
    install_dir.mkdir(parents=True, exist_ok=True)
    for filename in SCRIPTS:
        src = ROOT_DIR / filename
        if not src.exists():
            sys.exit(f"Missing {src}")
        dest = install_dir / filename
        shutil.copy2(src, dest)
        dest.chmod(0o755)
        print(f"  installed {filename}")


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
        print("  settings.json already up to date")
        return

    settings["statusLine"] = entry
    tmp = settings_path.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    os.replace(tmp, settings_path)
    print("  patched ~/.claude/settings.json")


def main(argv: list[str] | None = None, _settings_path: Path | None = None) -> None:
    if sys.version_info < (3, 11):
        sys.exit("Python 3.11+ required")

    parser = argparse.ArgumentParser(description="Install Claude Code statusline")
    parser.add_argument(
        "--dir",
        default=str(Path.home() / ".claude" / "ccslgraphs"),
        help="Installation directory (default: ~/.claude/ccslgraphs)",
    )
    args = parser.parse_args(argv)
    install_dir = Path(args.dir).expanduser().resolve()

    print("Installing claude-code-statusline (Python):")
    copy_scripts(install_dir)
    bake_colors(install_dir)
    bake_gradient(install_dir)
    patch_settings(install_dir, _settings_path)
    print("Done — restart Claude Code to activate.")


if __name__ == "__main__":
    main()
