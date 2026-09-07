"""
tests/test_run_command_path_confinement.py
Unit tests for argv-level path confinement in run_command (skills/
run_command_skills.py). When a project root is configured, `run_command`
refuses commands whose argument paths escape it (absolute paths, `..`, `~`,
or symlinks), and defaults its working directory to the root when none is
given. No Ollama needed:

    python -m unittest            # from the project root
    python -m unittest discover -s tests -v
"""

import shlex
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# Make the project root importable regardless of how unittest is invoked.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import DEFAULT_CONFIG
from platform_utils import set_project_root
from skills import run_command_skills as rc


def _config(mode: str = "off", **overrides) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    cfg["run_command_mode"] = mode
    cfg.update(overrides)
    return cfg


def _confirmer(answer: str):
    return lambda command, cwd, program: answer


class TestArgvPathConfinement(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._outside = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.outside = Path(self._outside.name)
        set_project_root(str(self.root))
        self._saved_config = rc._CONFIG
        self._saved_confirmer = rc._CONFIRMER

    def tearDown(self):
        set_project_root("")  # reset so no state leaks into other suites
        rc._CONFIG = self._saved_config
        rc._CONFIRMER = self._saved_confirmer
        self._tmp.cleanup()
        self._outside.cleanup()

    def _bind(self, mode: str = "auto", confirmer=None, **overrides) -> None:
        rc.bind_config(_config(mode, **overrides))
        rc.bind_confirmer(confirmer)

    # --- pure helpers ---

    def test_no_root_returns_none(self):
        set_project_root("")
        self.assertIsNone(rc._argv_escapes_root(["cat", "/etc/passwd"], None))

    def test_absolute_outside_flagged(self):
        token = str(self.outside / "x.txt")
        self.assertEqual(rc._argv_escapes_root(["cat", token], None), [token])

    def test_absolute_inside_not_flagged(self):
        self.assertIsNone(rc._argv_escapes_root(["cat", str(self.root / "x.txt")], str(self.root)))

    def test_parent_traversal_flagged(self):
        self.assertEqual(rc._argv_escapes_root(["cat", "../secret.txt"], str(self.root)), ["../secret.txt"])

    def test_dotdot_staying_inside_not_flagged(self):
        (self.root / "sub").mkdir()
        self.assertIsNone(rc._argv_escapes_root(["cat", "sub/../ok.txt"], str(self.root)))

    def test_flags_and_plain_words_skipped(self):
        self.assertIsNone(rc._argv_escapes_root(["grep", "-r", "needle"], str(self.root)))
        self.assertIsNone(rc._argv_escapes_root(["python", "-m", "unittest"], str(self.root)))

    def test_symlink_pointing_outside_flagged(self):
        target = self.outside / "secret.txt"
        target.write_text("secret", encoding="utf-8")
        link = self.root / "link.txt"
        try:
            link.symlink_to(target)
        except OSError:
            self.skipTest("symlinks not available on this platform")
        self.assertEqual(rc._argv_escapes_root(["cat", str(link)], str(self.root)), [str(link)])

    def test_looks_like_path(self):
        base = self.root
        (base / "exists.txt").write_text("x", encoding="utf-8")
        self.assertTrue(rc._looks_like_path("/etc/passwd", base))
        self.assertTrue(rc._looks_like_path("~/x", base))
        self.assertTrue(rc._looks_like_path("a/b", base))
        self.assertTrue(rc._looks_like_path("..", base))
        self.assertTrue(rc._looks_like_path("exists.txt", base))
        self.assertFalse(rc._looks_like_path("unittest", base))
        self.assertFalse(rc._looks_like_path("", base))

    # --- end-to-end through run_command ---

    def test_run_command_refuses_outside_argument(self):
        script = self.outside / "s.py"
        script.write_text("print('x')\n", encoding="utf-8")
        self._bind("confirm", _confirmer("allow"))
        command = f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}"
        result = rc.run_command(command)
        self.assertEqual(result["reason"], "argv_outside_root")
        self.assertIn(str(script), result["paths"])

    def test_run_command_allows_inside_argument(self):
        script = self.root / "s.py"
        script.write_text("print('x')\n", encoding="utf-8")
        self._bind("confirm", _confirmer("allow"))
        command = f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}"
        result = rc.run_command(command)
        self.assertNotIn("error", result)
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("x", result["stdout"])

    @unittest.skipUnless(shutil.which("pwd"), "pwd not available")
    def test_cwd_defaults_to_project_root(self):
        self._bind("auto")
        result = rc.run_command("pwd")
        self.assertNotIn("error", result)
        self.assertEqual(result["cwd"], str(self.root.resolve()))


if __name__ == "__main__":
    unittest.main()
