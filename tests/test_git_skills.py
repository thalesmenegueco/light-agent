"""
tests/test_git_skills.py
Unit tests for the read-only git skills. Skip gracefully when git is not
installed. No Ollama needed:

    python -m unittest            # from the project root
    python -m unittest discover -s tests -v
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Make the project root importable regardless of how unittest is invoked.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skills import DISPATCH


def _git_available() -> bool:
    return shutil.which("git") is not None


def _make_repo(root: Path) -> None:
    """Create a repo with one committed file, using an isolated identity."""
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    (root / "file.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "file.txt"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "initial"], check=True)


@unittest.skipUnless(_git_available(), "git not installed")
class TestGitSkills(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_not_a_repo_returns_error(self):
        result = DISPATCH["git_status"](path=str(self.root))
        self.assertIn("error", result)

    def test_status_after_commit(self):
        _make_repo(self.root)
        result = DISPATCH["git_status"](path=str(self.root))
        self.assertNotIn("error", result)
        self.assertIn("status", result)

    def test_diff_shows_uncommitted_change(self):
        _make_repo(self.root)
        (self.root / "file.txt").write_text("hello\nworld\n", encoding="utf-8")
        result = DISPATCH["git_diff"](path=str(self.root))
        self.assertNotIn("error", result)
        self.assertIn("world", result["diff"])

    def test_diff_clean(self):
        _make_repo(self.root)
        result = DISPATCH["git_diff"](path=str(self.root))
        self.assertNotIn("error", result)
        self.assertEqual(result["diff"], "")

    def test_diff_staged(self):
        _make_repo(self.root)
        (self.root / "file.txt").write_text("hello\nworld\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", "file.txt"], check=True)
        result = DISPATCH["git_diff"](path=str(self.root), staged=True)
        self.assertNotIn("error", result)
        self.assertIn("world", result["diff"])

    def test_log_has_commit(self):
        _make_repo(self.root)
        result = DISPATCH["git_log"](path=str(self.root))
        self.assertNotIn("error", result)
        self.assertIn("initial", result["log"])


if __name__ == "__main__":
    unittest.main()
