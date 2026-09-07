"""
tests/test_path_confinement.py
Unit tests for the path-confinement layer (platform_utils.normalize_path /
confined_path / set_project_root) and for the file skills' refusal to touch
anything outside the configured project root. No Ollama needed:

    python -m unittest            # from the project root
    python -m unittest discover -s tests -v
"""

import sys
import tempfile
import unittest
from pathlib import Path

# Make the project root importable regardless of how unittest is invoked.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from platform_utils import confined_path, get_project_root, normalize_path, set_project_root, PathOutsideRootError
from skills import DISPATCH


class TestPathConfinement(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._outside = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.outside = Path(self._outside.name)
        set_project_root(str(self.root))

    def tearDown(self):
        set_project_root("")  # reset so no state leaks into other suites
        self._tmp.cleanup()
        self._outside.cleanup()

    # --- the low-level resolver ---

    def test_relative_path_resolves_under_root(self):
        self.assertEqual(normalize_path("src/main.py"), (self.root / "src" / "main.py").resolve())

    def test_absolute_path_inside_root_allowed(self):
        p = normalize_path(str(self.root / "sub" / "x.txt"))
        self.assertTrue(p.is_relative_to(self.root))

    def test_absolute_path_outside_root_raises(self):
        with self.assertRaises(PathOutsideRootError):
            normalize_path(str(self.outside / "x.txt"))

    def test_parent_traversal_raises(self):
        with self.assertRaises(PathOutsideRootError):
            normalize_path("../escape.txt")

    def test_dotdot_that_stays_inside_root(self):
        (self.root / "sub").mkdir()
        self.assertEqual(normalize_path("sub/../ok.txt"), (self.root / "ok.txt").resolve())

    def test_empty_root_disables_confinement(self):
        set_project_root("")
        self.assertIsNone(get_project_root())
        target = self.outside / "x.txt"
        self.assertEqual(normalize_path(str(target)), target.resolve())

    # --- the ergonomic wrapper skills use ---

    def test_confined_path_returns_error_dict_on_escape(self):
        p, err = confined_path("../nope.txt")
        self.assertIsNone(p)
        self.assertIsNotNone(err)
        self.assertIn("outside the project root", err["error"])

    def test_confined_path_returns_path_on_success(self):
        p, err = confined_path("a.txt")
        self.assertIsNone(err)
        self.assertEqual(p, (self.root / "a.txt").resolve())

    def test_symlink_pointing_outside_is_rejected(self):
        target = self.outside / "secret.txt"
        target.write_text("secret", encoding="utf-8")
        link = self.root / "link.txt"
        try:
            link.symlink_to(target)
        except OSError:
            self.skipTest("symlinks not available on this platform")
        p, err = confined_path("link.txt")
        self.assertIsNone(p)
        self.assertIsNotNone(err)

    # --- skills return error dicts instead of raising ---

    def test_list_files_refuses_escape(self):
        result = DISPATCH["list_files"](path="..")
        self.assertIn("error", result)
        self.assertIn("outside the project root", result["error"])

    def test_read_file_inside_root_works(self):
        f = self.root / "a.txt"
        f.write_text("hello\n", encoding="utf-8")
        result = DISPATCH["read_file"](path="a.txt")
        self.assertEqual(result["content"], "hello\n")

    def test_write_file_refuses_escape(self):
        result = DISPATCH["write_file"](path="../outside.txt", content="x")
        self.assertIn("error", result)

    def test_search_files_refuses_escape(self):
        result = DISPATCH["search_files"](path="..", query="x", mode="name")
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
