import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import install
import uninstall


class InstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp()
        self.install_dir = Path(self._tmpdir) / "ccslgraphs"
        self.settings_path = Path(self._tmpdir) / "settings.json"

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _install(self, install_dir: Path | None = None) -> str:
        d = install_dir or self.install_dir
        out = io.StringIO()
        with redirect_stdout(out):
            install.install_scripts(install._local_root(), d)
            install.patch_settings(d, self.settings_path)
        return out.getvalue()

    def test_installs_scripts_to_custom_dir(self) -> None:
        self._install()
        for name in ["statusline.py"]:
            f = self.install_dir / name
            self.assertTrue(f.exists(), f"{name} not found")
            self.assertTrue(os.access(f, os.X_OK), f"{name} not executable")

    def test_patches_settings_with_correct_command(self) -> None:
        self._install()
        settings = json.loads(self.settings_path.read_text())
        expected_cmd = f"python3 {self.install_dir}/statusline.py"
        self.assertEqual(settings["statusLine"]["command"], expected_cmd)
        self.assertEqual(settings["statusLine"]["type"], "command")
        self.assertEqual(settings["statusLine"]["padding"], 0)

    def test_idempotent_settings(self) -> None:
        self._install()
        content_first = self.settings_path.read_text()
        self._install()
        content_second = self.settings_path.read_text()
        self.assertEqual(content_first, content_second)

    def test_idempotent_no_duplicate_key(self) -> None:
        self._install()
        self._install()
        settings = json.loads(self.settings_path.read_text())
        self.assertIn("statusLine", settings)
        # JSON parse means at most one statusLine key
        self.assertEqual(sum(1 for k in settings if k == "statusLine"), 1)

    def test_main_with_dir_arg(self) -> None:
        custom = Path(self._tmpdir) / "via-main"
        out = io.StringIO()
        with redirect_stdout(out):
            install.main(["--dir", str(custom)], _settings_path=self.settings_path)
        self.assertTrue((custom / "statusline.py").exists())

    def test_preserves_existing_settings_keys(self) -> None:
        self.settings_path.write_text(json.dumps({"theme": "dark"}))
        self._install()
        settings = json.loads(self.settings_path.read_text())
        self.assertEqual(settings.get("theme"), "dark")
        self.assertIn("statusLine", settings)


class UninstallIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp()
        self.install_dir = Path(self._tmpdir) / "ccslgraphs"
        self.settings_path = Path(self._tmpdir) / "settings.json"

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _install(self, install_dir: Path | None = None) -> None:
        d = install_dir or self.install_dir
        with redirect_stdout(io.StringIO()):
            install.install_scripts(install._local_root(), d)
            install.patch_settings(d, self.settings_path)

    def _uninstall(self) -> str:
        out = io.StringIO()
        with mock.patch.object(uninstall, "SETTINGS_PATH", self.settings_path), redirect_stdout(out):
            settings = json.loads(self.settings_path.read_text())
            detected = uninstall.detect_install_dir(settings)
            install_dir = detected or self.install_dir
            uninstall.remove_install_dir(install_dir)
            uninstall.unpatch_settings()
        return out.getvalue()

    def test_removes_install_dir(self) -> None:
        self._install()
        self.assertTrue(self.install_dir.exists())
        self._uninstall()
        self.assertFalse(self.install_dir.exists())

    def test_removes_settings_entry(self) -> None:
        self._install()
        self._uninstall()
        settings = json.loads(self.settings_path.read_text())
        self.assertNotIn("statusLine", settings)

    def test_preserves_other_settings_keys(self) -> None:
        self.settings_path.write_text(json.dumps({"theme": "dark"}))
        self._install()
        self._uninstall()
        settings = json.loads(self.settings_path.read_text())
        self.assertEqual(settings.get("theme"), "dark")
        self.assertNotIn("statusLine", settings)

    def test_uninstall_auto_detects_custom_dir(self) -> None:
        custom = Path(self._tmpdir) / "custom" / "ccslgraphs"
        self._install(custom)

        out = io.StringIO()
        with mock.patch.object(uninstall, "SETTINGS_PATH", self.settings_path), redirect_stdout(out):
            settings = json.loads(self.settings_path.read_text())
            detected = uninstall.detect_install_dir(settings)
            self.assertIsNotNone(detected)
            assert detected is not None
            self.assertEqual(detected, custom)
            uninstall.remove_install_dir(detected)
            uninstall.unpatch_settings()

        self.assertFalse(custom.exists())
        settings = json.loads(self.settings_path.read_text())
        self.assertNotIn("statusLine", settings)

    def test_detect_install_dir_returns_none_for_non_python_command(self) -> None:
        settings = {"statusLine": {"type": "command", "command": "/usr/local/bin/other-tool", "padding": 0}}
        self.assertIsNone(uninstall.detect_install_dir(settings))

    def test_detect_install_dir_returns_none_for_missing_statusline(self) -> None:
        self.assertIsNone(uninstall.detect_install_dir({}))


if __name__ == "__main__":
    unittest.main()
