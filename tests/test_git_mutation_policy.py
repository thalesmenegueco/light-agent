"""
tests/test_git_mutation_policy.py
Unit tests for the mutating git skills (commit/checkpoint/rollback) and their
git_mutation_mode gate. Skip gracefully when git is not installed. No Ollama
needed:

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

from config import DEFAULT_CONFIG
from skills import git_skills as git


def _git_available() -> bool:
    return shutil.which("git") is not None


def _make_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    (root / "file.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "file.txt"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "initial"], check=True)


def _config(mode: str = "off", **overrides) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    cfg["git_mutation_mode"] = mode
    cfg.update(overrides)
    return cfg


def _confirmer(answer: str):
    return lambda op, path, detail: answer


@unittest.skipUnless(_git_available(), "git not installed")
class TestGitMutationPolicy(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _make_repo(self.root)
        self._saved_config = git._CONFIG
        self._saved_confirmer = git._GIT_CONFIRMER

    def tearDown(self):
        git._CONFIG = self._saved_config
        git._GIT_CONFIRMER = self._saved_confirmer
        self._tmp.cleanup()

    def _bind(self, mode="off", confirmer=None, **overrides) -> None:
        git.bind_config(_config(mode, **overrides))
        git.bind_git_confirmer(confirmer)

    # --- master gate ---

    def test_off_mode_refuses_all_mutations(self):
        self._bind("off")
        (self.root / "file.txt").write_text("changed\n", encoding="utf-8")
        self.assertEqual(git.git_commit(path=str(self.root), message="x")["reason"], "mutation_disabled")
        self.assertEqual(git.git_checkpoint(path=str(self.root))["reason"], "mutation_disabled")
        self.assertEqual(
            git.git_rollback(path=str(self.root), checkpoint="HEAD")["reason"], "mutation_disabled"
        )

    def test_unknown_mode_refuses(self):
        self._bind("bogus")
        result = git.git_commit(path=str(self.root), message="x")
        self.assertEqual(result["reason"], "config")

    def test_confirm_without_confirmer_fails_closed(self):
        self._bind("confirm")
        result = git.git_checkpoint(path=str(self.root))
        self.assertEqual(result["reason"], "confirm_unavailable")

    # --- allow mode ---

    def test_commit_records_change(self):
        self._bind("allow")
        (self.root / "file.txt").write_text("changed\n", encoding="utf-8")
        result = git.git_commit(path=str(self.root), message="update")
        self.assertNotIn("error", result)
        self.assertTrue(result.get("commit"))
        log = git.git_log(path=str(self.root))
        self.assertIn("update", log["log"])

    def test_commit_requires_message(self):
        self._bind("allow")
        result = git.git_commit(path=str(self.root), message="")
        self.assertIn("error", result)

    def test_checkpoint_returns_hash(self):
        self._bind("allow")
        (self.root / "file.txt").write_text("changed\n", encoding="utf-8")
        result = git.git_checkpoint(path=str(self.root))
        self.assertNotIn("error", result)
        self.assertIn("checkpoint", result)
        self.assertTrue(result["checkpoint"])

    # --- confirm mode ---

    def test_confirm_allow_commits(self):
        self._bind("confirm", _confirmer("allow"))
        (self.root / "file.txt").write_text("changed\n", encoding="utf-8")
        result = git.git_commit(path=str(self.root), message="update")
        self.assertNotIn("error", result)

    def test_confirm_deny_refuses(self):
        self._bind("confirm", _confirmer("deny"))
        (self.root / "file.txt").write_text("changed\n", encoding="utf-8")
        result = git.git_commit(path=str(self.root), message="update")
        self.assertEqual(result["reason"], "user_denied")

    # --- rollback semantics ---

    def test_rollback_restores_checkpoint(self):
        self._bind("allow")
        # Establish a checkpoint on the clean tree.
        cp = git.git_checkpoint(path=str(self.root))
        checkpoint = cp["checkpoint"]
        # Make a tracked change and add an untracked file after the checkpoint.
        (self.root / "file.txt").write_text("changed\n", encoding="utf-8")
        (self.root / "new.txt").write_text("new\n", encoding="utf-8")
        # Roll back to the checkpoint.
        result = git.git_rollback(path=str(self.root), checkpoint=checkpoint)
        self.assertNotIn("error", result)
        self.assertEqual((self.root / "file.txt").read_text(encoding="utf-8"), "hello\n")
        self.assertFalse((self.root / "new.txt").exists())

    def test_rollback_invalid_commit_errors(self):
        self._bind("allow")
        result = git.git_rollback(path=str(self.root), checkpoint="deadbeef")
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
