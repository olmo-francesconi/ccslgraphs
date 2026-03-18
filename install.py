#!/usr/bin/env python3
"""Claude Code statusline installer (Python version)."""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
SCRIPTS = ["statusline.py", "usage_fetch.py"]


def statusline_entry(install_dir: Path) -> dict:
    return {
        "type": "command",
        "command": f"python3 {install_dir}/statusline.py",
        "padding": 0,
    }


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
    patch_settings(install_dir, _settings_path)
    print("Done — restart Claude Code to activate.")


if __name__ == "__main__":
    main()
