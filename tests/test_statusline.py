import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import statusline
import uninstall
import usage_fetch


class GitInfoTests(unittest.TestCase):
    def test_git_info_returns_none_outside_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertIsNone(statusline._git_info(tmpdir))

    def test_git_info_reports_clean_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)

            info = statusline._git_info(str(repo))

            self.assertEqual(info, {"branch": "main", "dirty": False, "summary": "clean"})

    def test_git_info_reports_dirty_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
            (repo / "tracked.txt").write_text("hello\n")

            info = statusline._git_info(str(repo))

            assert info is not None
            self.assertEqual(info["branch"], "main")
            self.assertTrue(info["dirty"])
            self.assertIn("untracked", info["summary"])


class FetchLockTests(unittest.TestCase):
    def test_claim_fetch_lock_deduplicates_and_clears(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = Path(tmpdir) / "usage-fetch.lock"
            with mock.patch.object(statusline, "FETCH_LOCK_PATH", lock_path):
                token = statusline._claim_fetch_lock()

                self.assertIsNotNone(token)
                self.assertIsNone(statusline._claim_fetch_lock())

                assert token is not None
                statusline._clear_fetch_lock(token)
                self.assertFalse(lock_path.exists())

    def test_claim_fetch_lock_replaces_stale_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = Path(tmpdir) / "usage-fetch.lock"
            stale_payload = {
                "createdAt": (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat(),
                "token": "stale-token",
            }
            lock_path.write_text(json.dumps(stale_payload))

            with mock.patch.object(statusline, "FETCH_LOCK_PATH", lock_path):
                token = statusline._claim_fetch_lock()

            self.assertIsNotNone(token)
            self.assertNotEqual(token, "stale-token")


class UsageFetchTests(unittest.TestCase):
    def test_write_cache_replaces_target_without_fixed_temp_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "usage-cache.json"
            lock_path = Path(tmpdir) / "usage-fetch.lock"
            with (
                mock.patch.object(usage_fetch, "CACHE_PATH", cache_path),
                mock.patch.object(usage_fetch, "FETCH_LOCK_PATH", lock_path),
            ):
                usage_fetch._write_cache({"fetchedAt": "now", "history": []})

                self.assertEqual(
                    json.loads(cache_path.read_text()),
                    {"fetchedAt": "now", "history": []},
                )
                self.assertEqual(list(Path(tmpdir).glob("usage-cache.json.*.tmp")), [])


class SpanTests(unittest.TestCase):
    def test_width_arithmetic(self) -> None:
        a = statusline.Span("hello", statusline.C.model)
        b = statusline.Span(" world")
        c = a + b
        self.assertEqual(c.width, 11)

    def test_vlen_strips_ansi(self) -> None:
        colored = f"\x1b[38;5;183mhello\x1b[0m"
        self.assertEqual(statusline.vlen(colored), 5)

    def test_plain_span_no_ansi_escape(self) -> None:
        s = statusline.Span("text")
        self.assertEqual(s.colored, "text")
        self.assertEqual(s.width, 4)

    def test_truncate_shortens_plain(self) -> None:
        s = statusline.Span("hello world")
        t = s.truncate(7)
        self.assertEqual(t.width, 7)
        self.assertTrue(t.plain.endswith("…"))

    def test_truncate_noop_when_fits(self) -> None:
        s = statusline.Span("hi")
        self.assertIs(s.truncate(10), s)


class RenderLineTests(unittest.TestCase):
    def _ctx(self, **kwargs) -> dict:
        base: dict = {
            "model": "claude-opus-4-5",
            "effort": None,
            "used_pct": 42.0,
            "ctx_size": 200_000,
            "input_tokens": 50_000,
            "output_tokens": 10_000,
            "git": {"branch": "main", "dirty": False, "summary": "clean"},
        }
        base.update(kwargs)
        return base

    def test_returns_single_line(self) -> None:
        result = statusline.render_line(self._ctx(), safe_width=120)
        self.assertNotIn("\n", result)

    def test_no_trailing_newline(self) -> None:
        result = statusline.render_line(self._ctx(), safe_width=120)
        self.assertFalse(result.endswith("\n"))

    def test_plain_width_fits_safe_width(self) -> None:
        result = statusline.render_line(self._ctx(), safe_width=80)
        self.assertLessEqual(statusline.vlen(result), 80)

    def test_no_context_data(self) -> None:
        result = statusline.render_line(self._ctx(used_pct=None), safe_width=120)
        self.assertNotIn("\n", result)

    def test_no_git(self) -> None:
        result = statusline.render_line(self._ctx(git=None), safe_width=120)
        self.assertIn("no repo", statusline.ANSI_RE.sub("", result))

    def test_dirty_git(self) -> None:
        ctx = self._ctx(git={"branch": "feat/foo", "dirty": True, "summary": "+12, -3"})
        result = statusline.render_line(ctx, safe_width=120)
        plain = statusline.ANSI_RE.sub("", result)
        self.assertIn("feat/foo", plain)


class PctToRowTests(unittest.TestCase):
    """Verify pct_to_row: endpoints land on row 0/6, midpoint on row 3."""

    ROWS = statusline.GRAPH_ROWS  # 7

    def _dot_row(self, pct: float) -> int:
        plain_rows, _ = statusline._render_line_graph([pct], current_pct=round(pct))
        for i, row in enumerate(plain_rows):
            if "●" in row:
                return i
        self.fail(f"No dot found in graph for pct={pct}")

    def test_100pct_lands_on_top_row(self) -> None:
        self.assertEqual(self._dot_row(100), 0)

    def test_0pct_lands_on_bottom_row(self) -> None:
        self.assertEqual(self._dot_row(0), self.ROWS - 1)

    def test_50pct_lands_on_middle_row(self) -> None:
        self.assertEqual(self._dot_row(50), (self.ROWS - 1) // 2)

    def test_each_row_is_reachable(self) -> None:
        """Every row 0–6 must be reachable by some percentage."""
        reachable = set()
        for pct in range(101):
            row = round((100 - pct) * (self.ROWS - 1) / 100)
            reachable.add(row)
        self.assertEqual(reachable, set(range(self.ROWS)))

    def test_formula_is_monotone(self) -> None:
        """Higher pct → same or lower row index (closer to top)."""
        prev = round((100 - 0) * (self.ROWS - 1) / 100)
        for pct in range(1, 101):
            curr = round((100 - pct) * (self.ROWS - 1) / 100)
            self.assertGreaterEqual(prev, curr, f"monotone violated at pct={pct}")
            prev = curr


class GraphRenderTests(unittest.TestCase):
    def _cache(self) -> dict:
        now = datetime.now(timezone.utc)
        return {
            "fetchedAt": now.isoformat(),
            "session": {"utilization": 30, "resetsAt": (now + timedelta(hours=2)).isoformat()},
            "weekly": {"utilization": 50, "resetsAt": (now + timedelta(days=3)).isoformat()},
            "history": [{"ts": now.isoformat(), "pct": 30}],
            "weeklyHistory": [{"ts": now.isoformat(), "pct": 50}],
        }

    def test_returns_correct_row_count(self) -> None:
        ctx = {"usage_cache": self._cache(), "usage_stale": False}
        rows = statusline.render_graphs(ctx, safe_width=81)
        self.assertEqual(len(rows), statusline.GRAPH_ROWS)

    def test_rows_fit_safe_width(self) -> None:
        safe_width = 81
        ctx = {"usage_cache": self._cache(), "usage_stale": False}
        rows = statusline.render_graphs(ctx, safe_width=safe_width)
        for row in rows:
            plain = statusline.ANSI_RE.sub("", row)
            self.assertLessEqual(len(plain), safe_width + 3)  # +3 for " │ " separator

    def test_empty_cache(self) -> None:
        ctx = {"usage_cache": {}, "usage_stale": False}
        rows = statusline.render_graphs(ctx, safe_width=81)
        self.assertEqual(len(rows), statusline.GRAPH_ROWS)

    def test_stale_suffix_appears(self) -> None:
        ctx = {"usage_cache": self._cache(), "usage_stale": True}
        rows = statusline.render_graphs(ctx, safe_width=81)
        header = statusline.ANSI_RE.sub("", rows[0])
        self.assertIn("stale", header)


class UninstallTests(unittest.TestCase):
    def test_unpatch_settings_preserves_unrelated_statusline(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_path = Path(tmpdir) / "settings.json"
            original = {
                "statusLine": {
                    "type": "command",
                    "command": "python3 ~/.claude/custom/statusline.py",
                    "padding": 0,
                }
            }
            settings_path.write_text(json.dumps(original))

            stdout = io.StringIO()
            with mock.patch.object(uninstall, "SETTINGS_PATH", settings_path), redirect_stdout(stdout):
                uninstall.unpatch_settings()

            self.assertEqual(json.loads(settings_path.read_text()), original)
            self.assertIn("leaving it unchanged", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
