#!/usr/bin/env python3
"""Background fetcher — reads OAuth token, calls usage API, writes cache.
Spawned detached by statusline.py; exits cleanly on any error.
"""

import json
import os
import subprocess
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
CACHE_PATH = Path(__file__).parent / "usage-cache.json"
FETCH_LOCK_PATH = Path(__file__).parent / "usage-fetch.lock"
CREDS_PATH = HOME / ".claude" / ".credentials.json"
FETCH_LOCK_SECONDS = 60

API_URL = "https://api.anthropic.com/api/oauth/usage"


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


def _claim_fetch_lock(token: str | None) -> str | None:
    if token:
        lock = _read_fetch_lock()
        if lock and lock.get("token") == token:
            return token
        return None

    token = f"manual-{os.getpid()}-{datetime.now(timezone.utc).timestamp()}"
    payload = json.dumps({"createdAt": datetime.now(timezone.utc).isoformat(), "token": token})

    for _ in range(2):
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


# ── Auth ──────────────────────────────────────────────────────────────────────


def _get_oauth_token() -> str | None:
    # 1. macOS Keychain
    try:
        raw = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if raw.returncode == 0:
            parsed = json.loads(raw.stdout.strip())
            token = (parsed.get("claudeAiOauth") or {}).get("accessToken")
            if token:
                return token
    except Exception:
        pass

    # 2. Credentials file fallback
    try:
        parsed = json.loads(CREDS_PATH.read_text())
        token = (parsed.get("claudeAiOauth") or {}).get("accessToken")
        if token:
            return token
    except Exception:
        pass

    return None


# ── Fetch ─────────────────────────────────────────────────────────────────────


def _fetch_usage(token: str) -> dict:
    req = urllib.request.Request(
        API_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "oauth-2025-04-20",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


# ── Cache builder ─────────────────────────────────────────────────────────────


def _build_cache(data: dict, existing: dict | None) -> dict:
    now = datetime.now(timezone.utc).isoformat()

    # Monthly extra usage
    extra = data.get("extra_usage") or {}
    monthly = (
        {
            "enabled": True,
            "usedCents": extra.get("used_credits", 0),
            "limitCents": extra.get("monthly_limit", 0),
            "utilization": extra.get("utilization", 0),
        }
        if extra.get("is_enabled")
        else None
    )

    # Session (5h) history
    five_hour = data.get("five_hour") or {}
    resets_at = five_hour.get("resets_at")
    sess_start_ms: float | None = None
    if resets_at:
        sess_start_ms = datetime.fromisoformat(resets_at.replace("Z", "+00:00")).timestamp() * 1000 - 5 * 3600 * 1000

    prev_history = (existing or {}).get("history") or []
    if sess_start_ms is not None:
        prev_history = [
            p
            for p in prev_history
            if datetime.fromisoformat(p["ts"].replace("Z", "+00:00")).timestamp() * 1000 >= sess_start_ms
        ]
    else:
        prev_history = []
    prev_history.append({"ts": now, "pct": five_hour.get("utilization", 0)})

    # Weekly (7d) history
    seven_day = data.get("seven_day") or {}
    weekly_resets_at = seven_day.get("resets_at")
    week_start_ms: float | None = None
    if weekly_resets_at:
        week_start_ms = (
            datetime.fromisoformat(weekly_resets_at.replace("Z", "+00:00")).timestamp() * 1000 - 7 * 24 * 3600 * 1000
        )

    prev_weekly = (existing or {}).get("weeklyHistory") or []
    if week_start_ms is not None:
        prev_weekly = [
            p
            for p in prev_weekly
            if datetime.fromisoformat(p["ts"].replace("Z", "+00:00")).timestamp() * 1000 >= week_start_ms
        ]
    else:
        prev_weekly = []
    prev_weekly.append({"ts": now, "pct": seven_day.get("utilization", 0)})

    return {
        "fetchedAt": now,
        "session": {
            "utilization": five_hour.get("utilization", 0),
            "resetsAt": resets_at,
        },
        "weekly": {
            "utilization": seven_day.get("utilization", 0),
            "resetsAt": weekly_resets_at,
        },
        "monthly": monthly,
        "history": prev_history,
        "weeklyHistory": prev_weekly,
    }


def _write_cache(cache: dict) -> None:
    with tempfile.NamedTemporaryFile(
        "w",
        dir=CACHE_PATH.parent,
        prefix=f"{CACHE_PATH.name}.",
        suffix=".tmp",
        delete=False,
    ) as tmp:
        json.dump(cache, tmp, indent=2)
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, CACHE_PATH)


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    lock_token = _claim_fetch_lock(os.environ.get("CCSL_FETCH_LOCK_TOKEN"))
    if not lock_token:
        return

    try:
        token = _get_oauth_token()
        if not token:
            return

        existing: dict | None = None
        try:
            existing = json.loads(CACHE_PATH.read_text())
        except Exception:
            pass

        data = _fetch_usage(token)
        cache = _build_cache(data, existing)
        _write_cache(cache)
    except Exception:
        pass  # Leave existing cache unchanged — stale data is fine
    finally:
        _clear_fetch_lock(lock_token)


if __name__ == "__main__":
    main()
