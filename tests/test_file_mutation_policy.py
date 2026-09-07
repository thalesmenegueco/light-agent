"""
tests/test_file_mutation_policy.py
Unit tests for the file-mutation safety policy (skills/fs_skills.py). No Ollama
needed -- everything is exercised offline with a scripted confirmer:

    python -m unittest            # from the project root
    python -m unittest discover -s tests -v
"""

import sys
import tempfile
import unittest
from pathlib import Path

# Make the project root importable regardless of how unittest is invoked.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import DEFAULT_CONFIG
from skills import fs_skills as fs


def _config(mode: str = "allow", **overrides) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    cfg["file_mutation_mode"] = mode
    cfg.update(overrides)
    return cfg


def _confirmer(answer: str):
    return lambda op, path, detail: answer


class TestFileMutationPolicy(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._saved_config = fs._CONFIG
        self._saved_confirmer = fs._FILE_CONFIRMER

    def tearDown(self):
        fs._CONFIG = self._saved_config
        fs._FILE_CONFIRMER = self._saved_confirmer
        self._tmp.cleanup()

    def _bind(self, mode="allow", confirmer=None, **overrides) -> None:
        fs.bind_config(_config(mode, **overrides))
        fs.bind_file_confirmer(confirmer)

    # --- master gate ---

    def test_off_mode_refuses_all_mutations(self):
        self._bind("off")
        # Create inputs directly with Path (bypassing the gated skills).
        a = self.root / "a.txt"
        a.write_text("x", encoding="utf-8")

        self.assertEqual(fs.write_file(str(self.root / "new.txt"), "y")["reason"], "mutation_disabled")
        self.assertEqual(fs.append_file(str(a), "y")["reason"], "mutation_disabled")
        self.assertEqual(fs.replace_in_file(str(a), "x", "y")["reason"], "mutation_disabled")
        self.assertEqual(fs.write_code(str(self.root / "c.txt"), "y")["reason"], "mutation_disabled")
        self.assertEqual(
            fs.move_file(str(a), str(self.root / "b.txt"))["reason"], "mutation_disabled"
        )

    def test_allow_mode_proceeds_without_confirmer(self):
        self._bind("allow")
        result = fs.write_file(str(self.root / "a.txt"), "hello\n")
        self.assertIn("written_to", result)

    def test_unknown_mode_refuses(self):
        self._bind("bogus")
        result = fs.write_file(str(self.root / "a.txt"), "x")
        self.assertEqual(result["reason"], "config")

    # --- confirm mode ---

    def test_confirm_without_confirmer_fails_closed(self):
        self._bind("confirm")
        result = fs.write_file(str(self.root / "a.txt"), "x")
        self.assertEqual(result["reason"], "confirm_unavailable")

    def test_confirm_allow_writes(self):
        self._bind("confirm", _confirmer("allow"))
        result = fs.write_file(str(self.root / "a.txt"), "hello\n")
        self.assertIn("written_to", result)

    def test_confirm_deny_writes_refused_and_leaves_no_file(self):
        self._bind("confirm", _confirmer("deny"))
        target = self.root / "a.txt"
        result = fs.write_file(str(target), "x")
        self.assertEqual(result["reason"], "user_denied")
        self.assertFalse(target.exists())

    def test_confirm_allow_appends(self):
        self._bind("confirm", _confirmer("allow"))
        f = self.root / "a.txt"
        f.write_text("a\n", encoding="utf-8")
        result = fs.append_file(str(f), "b\n")
        self.assertIn("appended_to", result)
        self.assertEqual(f.read_text(encoding="utf-8"), "a\nb\n")

    def test_confirm_allow_writes_code_stripped(self):
        self._bind("confirm", _confirmer("allow"))
        target = self.root / "a.py"
        result = fs.write_code(str(target), '```python\nprint(1)\n```')
        self.assertIn("written_to", result)
        self.assertEqual(target.read_text(encoding="utf-8"), "print(1)")

    def test_confirm_deny_replace_leaves_file(self):
        self._bind("confirm", _confirmer("deny"))
        f = self.root / "a.txt"
        f.write_text("original\n", encoding="utf-8")
        result = fs.replace_in_file(str(f), "original", "changed")
        self.assertEqual(result["reason"], "user_denied")
        self.assertEqual(f.read_text(encoding="utf-8"), "original\n")

    def test_confirm_deny_move_leaves_source(self):
        self._bind("confirm", _confirmer("deny"))
        src = self.root / "a.txt"
        src.write_text("x", encoding="utf-8")
        result = fs.move_file(str(src), str(self.root / "b.txt"))
        self.assertEqual(result["reason"], "user_denied")
        self.assertTrue(src.exists())
        self.assertFalse((self.root / "b.txt").exists())

    # --- non-mutating skills are unaffected ---

    def test_read_skills_unaffected_by_off(self):
        self._bind("off")
        f = self.root / "a.txt"
        f.write_text("hello\n", encoding="utf-8")
        self.assertIn("content", fs.read_file(str(f)))
        self.assertIn("files", fs.list_files(str(self.root)))


if __name__ == "__main__":
    unittest.main()
