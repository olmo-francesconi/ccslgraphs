#!/usr/bin/env python3
"""Render the statusline graphs with a synthetic cache to preview the projection line.

Usage: python scripts/preview.py [scenario]
Scenarios: steep (default), flat, saturated, gentle
"""
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
import statusline as s

SCENARIOS = {
    "steep":     (70, lambda m: max(0, 70 - m * 50 / 180)),
    "flat":      (40, lambda m: 40),
    "saturated": (100, lambda m: min(100, 120 - m * 120 / 180)),
    "gentle":    (30, lambda m: max(0, 30 - m * 8 / 180)),
}

name = sys.argv[1] if len(sys.argv) > 1 else "steep"
current_pct, pct_fn = SCENARIOS[name]

now = datetime.now(timezone.utc)
resets = (now + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
history = [
    {"ts": (now - timedelta(minutes=m)).strftime("%Y-%m-%dT%H:%M:%S.000Z"), "pct": pct_fn(m)}
    for m in range(180, -1, -5)
]
cache = {
    "session": {"utilization": current_pct, "resetsAt": resets},
    "weekly":  {"utilization": current_pct, "resetsAt": resets},
    "history": history,
    "weeklyHistory": history,
}
print(f"\n--- scenario: {name} ---")
for r in s.render_graphs({"usage_cache": cache, "usage_stale": False}, safe_width=100):
    print("\x1b[0m" + r)
