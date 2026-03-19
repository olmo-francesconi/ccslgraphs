#!/usr/bin/env python3
"""
Claude Code statusline uninstaller.

Removes the install directory and cleans up settings.json.
"""

import json
import os
import shlex
import shutil
import sys
from pathlib import Path

SETTINGS_PATH = Path.home() / ".claude" / "settings.json"


def detect_install_dir(settings: dict) -> Path | None:
    """Extract and resolve the install dir from the statusLine command in settings."""
    cmd = settings.get("statusLine", {}).get("command", "")
    try:
        parts = shlex.split(cmd)
    except ValueError:
        return None
    if len(parts) == 2 and parts[1].endswith("ccslgraphs/statusline.py"):
        return Path(parts[1]).parent.expanduser()
    return None


def statusline_entry(install_dir: Path) -> dict:
    return {
        "type": "command",
        "command": f"python3 {shlex.quote(str(install_dir))}/statusline.py",
        "padding": 0,
    }


def remove_install_dir(install_dir: Path) -> None:
    if install_dir.exists():
        shutil.rmtree(install_dir)
        print(f"  removed {install_dir}")
    else:
        print(f"  {install_dir} not found, skipping")


def unpatch_settings() -> None:
    if not SETTINGS_PATH.exists():
        print("  settings.json not found, skipping")
        return
    with open(SETTINGS_PATH) as f:
        settings = json.load(f)
    if "statusLine" not in settings:
        print("  no statusLine entry in settings.json, skipping")
        return

    install_dir = detect_install_dir(settings)
    if install_dir is None:
        print("  statusLine does not match this install, leaving it unchanged")
        return

    if settings["statusLine"] != statusline_entry(install_dir):
        print("  statusLine does not match this install, leaving it unchanged")
        return

    del settings["statusLine"]
    tmp = SETTINGS_PATH.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    os.replace(tmp, SETTINGS_PATH)
    print("  removed statusLine from ~/.claude/settings.json")


def main() -> None:
    if sys.version_info < (3, 11):
        sys.exit("Python 3.11+ required")

    print("Uninstalling claude-code-statusline:")

    # Detect install dir from settings before removing anything
    install_dir = Path.home() / ".claude" / "ccslgraphs"  # fallback default
    if SETTINGS_PATH.exists():
        try:
            with open(SETTINGS_PATH) as f:
                settings = json.load(f)
            detected = detect_install_dir(settings)
            if detected:
                install_dir = detected
        except Exception:
            pass

    remove_install_dir(install_dir)
    unpatch_settings()
    print("Done — restart Claude Code to deactivate.")


if __name__ == "__main__":
    main()
