"""
tests/test_skills.py
Assertion-based unit tests for the deterministic skills and the fast-path
matcher. Standard library only -- no Ollama needed:

    python -m unittest            # from the project root
    python -m unittest discover -s tests -v
"""

import sys
import tempfile
import unittest
from pathlib import Path

# Make the project root importable regardless of how unittest is invoked.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main
from skills import DISPATCH, TOOLS

EXPECTED_TOOLS = {
    "append_file",
    "get_config",
    "git_diff",
    "git_log",
    "git_status",
    "list_files",
    "list_skills",
    "move_file",
    "open_file",
    "read_file",
    "replace_in_file",
    "run_coder",
    "search_files",
    "set_config",
    "write_file",
}


class TestRegistry(unittest.TestCase):
    def test_tools_and_dispatch_align(self):
        names = [schema["function"]["name"] for schema in TOOLS]
        self.assertEqual(len(names), len(set(names)), "duplicate tool names")
        self.assertEqual(set(names), EXPECTED_TOOLS)
        self.assertEqual(set(names), set(DISPATCH))

    def test_schema_shape(self):
        for schema in TOOLS:
            self.assertEqual(schema["type"], "function")
            self.assertIn("name", schema["function"])
            self.assertIn("description", schema["function"])
            self.assertIn("parameters", schema["function"])


class TestFsSkills(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "dir").mkdir()
        (self.root / "a.txt").write_text("hello\nworld\n", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    # list_files
    def test_list_files_separates_and_sorts(self):
        result = DISPATCH["list_files"](path=str(self.root))
        self.assertEqual(result["files"], ["a.txt"])
        self.assertEqual(result["folders"], ["dir"])

    def test_list_files_missing_path(self):
        result = DISPATCH["list_files"](path=str(self.root / "nope"))
        self.assertIn("error", result)

    def test_list_files_not_a_directory(self):
        result = DISPATCH["list_files"](path=str(self.root / "a.txt"))
        self.assertIn("error", result)

    # read_file
    def test_read_file(self):
        result = DISPATCH["read_file"](path=str(self.root / "a.txt"))
        self.assertEqual(result["content"], "hello\nworld\n")
        self.assertFalse(result["truncated"])

    def test_read_file_truncates(self):
        result = DISPATCH["read_file"](path=str(self.root / "a.txt"), max_chars=5)
        self.assertEqual(result["content"], "hello")
        self.assertTrue(result["truncated"])

    def test_read_file_missing(self):
        result = DISPATCH["read_file"](path=str(self.root / "nope.txt"))
        self.assertIn("error", result)

    # move_file
    def test_move_file(self):
        dest = self.root / "dir" / "moved.txt"
        result = DISPATCH["move_file"](source=str(self.root / "a.txt"), destination=str(dest))
        self.assertIn("moved_to", result)
        self.assertTrue(dest.exists())
        self.assertFalse((self.root / "a.txt").exists())

    def test_move_file_missing_source(self):
        result = DISPATCH["move_file"](
            source=str(self.root / "nope.txt"),
            destination=str(self.root / "x.txt"),
        )
        self.assertIn("error", result)

    # write_file
    def test_write_file_creates_parent_dirs(self):
        target = self.root / "deep" / "nested" / "out.txt"
        result = DISPATCH["write_file"](path=str(target), content="data")
        self.assertIn("written_to", result)
        self.assertEqual(target.read_text(encoding="utf-8"), "data")

    def test_write_file_overwrite_guard(self):
        target = self.root / "a.txt"
        result = DISPATCH["write_file"](path=str(target), content="changed")
        self.assertIn("error", result)
        self.assertEqual(target.read_text(encoding="utf-8"), "hello\nworld\n")

    def test_write_file_overwrite_true(self):
        target = self.root / "a.txt"
        result = DISPATCH["write_file"](path=str(target), content="changed", overwrite=True)
        self.assertIn("written_to", result)
        self.assertEqual(target.read_text(encoding="utf-8"), "changed")

    # append_file
    def test_append_file(self):
        result = DISPATCH["append_file"](path=str(self.root / "a.txt"), content="!\n")
        self.assertIn("appended_to", result)
        self.assertEqual((self.root / "a.txt").read_text(encoding="utf-8"), "hello\nworld\n!\n")

    def test_append_file_creates_file(self):
        target = self.root / "new.txt"
        result = DISPATCH["append_file"](path=str(target), content="x")
        self.assertIn("appended_to", result)
        self.assertEqual(target.read_text(encoding="utf-8"), "x")

    # open_file -- only the error path, so tests never launch an OS app
    def test_open_file_missing(self):
        result = DISPATCH["open_file"](path=str(self.root / "nope.txt"))
        self.assertIn("error", result)


class TestReplaceFile(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.file = self.root / "code.py"
        self.file.write_text("x = 1\ny = 2\nx = 3\n", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_replaces_single_occurrence(self):
        result = DISPATCH["replace_in_file"](path=str(self.file), old="y = 2", new="y = 22")
        self.assertEqual(result.get("replacements"), 1)
        self.assertEqual(self.file.read_text(encoding="utf-8"), "x = 1\ny = 22\nx = 3\n")

    def test_replace_all(self):
        result = DISPATCH["replace_in_file"](
            path=str(self.file), old="x =", new="xx =", replace_all=True
        )
        self.assertEqual(result.get("replacements"), 2)
        self.assertEqual(self.file.read_text(encoding="utf-8"), "xx = 1\ny = 2\nxx = 3\n")

    def test_ambiguous_without_replace_all(self):
        result = DISPATCH["replace_in_file"](path=str(self.file), old="x =", new="xx =")
        self.assertIn("error", result)
        self.assertEqual(self.file.read_text(encoding="utf-8"), "x = 1\ny = 2\nx = 3\n")

    def test_missing_text(self):
        result = DISPATCH["replace_in_file"](path=str(self.file), old="nope", new="x")
        self.assertIn("error", result)

    def test_missing_file(self):
        result = DISPATCH["replace_in_file"](
            path=str(self.root / "nope.py"), old="a", new="b"
        )
        self.assertIn("error", result)

    def test_empty_old(self):
        result = DISPATCH["replace_in_file"](path=str(self.file), old="", new="b")
        self.assertIn("error", result)


class TestSearchSkills(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "sub").mkdir()
        (self.root / "a.md").write_text("hello world\nnote here\n", encoding="utf-8")
        (self.root / "sub" / "Config_Foo.py").write_text(
            "def foo():\n    return 1\n", encoding="utf-8"
        )
        (self.root / "binary.bin").write_bytes(b"\x00\xff\xfe not text \x80")

    def tearDown(self):
        self._tmp.cleanup()

    def test_name_mode_case_insensitive(self):
        result = DISPATCH["search_files"](path=str(self.root), query="foo", mode="name")
        self.assertEqual(result["count"], 1)
        self.assertTrue(any("Config_Foo.py" in f for f in result["files"]))

    def test_content_mode_with_line_numbers(self):
        result = DISPATCH["search_files"](path=str(self.root), query="foo", mode="content")
        self.assertEqual(result["count"], 1)
        match = result["matches"][0]
        self.assertEqual(match["line"], 1)
        self.assertTrue(match["file"].endswith("Config_Foo.py"))

    def test_content_hello_in_markdown(self):
        result = DISPATCH["search_files"](path=str(self.root), query="hello", mode="content")
        self.assertEqual(result["count"], 1)
        self.assertTrue(result["matches"][0]["file"].endswith("a.md"))

    def test_both_mode(self):
        result = DISPATCH["search_files"](path=str(self.root), query="foo", mode="both")
        self.assertEqual(result["count"], 2)  # 1 filename + 1 content line
        self.assertTrue(any("Config_Foo.py" in f for f in result["files"]))
        self.assertTrue(any(m["text"].startswith("def foo") for m in result["matches"]))

    def test_no_match(self):
        result = DISPATCH["search_files"](path=str(self.root), query="zzzz", mode="content")
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["matches"], [])

    def test_invalid_mode(self):
        result = DISPATCH["search_files"](path=str(self.root), query="x", mode="bogus")
        self.assertIn("error", result)

    def test_empty_query(self):
        result = DISPATCH["search_files"](path=str(self.root), query="   ", mode="content")
        self.assertIn("error", result)

    def test_missing_path(self):
        result = DISPATCH["search_files"](path=str(self.root / "nope"), query="x")
        self.assertIn("error", result)

    def test_binary_files_skipped(self):
        result = DISPATCH["search_files"](path=str(self.root), query="not text", mode="content")
        self.assertEqual(result["count"], 0)

    def test_max_results_truncates(self):
        many = self.root / "many.txt"
        many.write_text("".join(f"match {i}\n" for i in range(10)), encoding="utf-8")
        result = DISPATCH["search_files"](
            path=str(self.root), query="match", mode="content", max_results=3
        )
        self.assertEqual(len(result["matches"]), 3)
        self.assertTrue(result["truncated"])

    def test_skips_pycache_dir(self):
        (self.root / "__pycache__").mkdir()
        (self.root / "__pycache__" / "junk.py").write_text("needle\n", encoding="utf-8")
        result = DISPATCH["search_files"](path=str(self.root), query="needle", mode="content")
        self.assertEqual(result["count"], 0)


class TestFastPath(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "a.txt").write_text("hello\n", encoding="utf-8")
        (self.root / "b.py").write_text("def hello(): pass\n", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_list_files_phrase(self):
        result = main.try_fast_path(f"list files in {self.root}")
        self.assertIsNotNone(result)
        self.assertIn("a.txt", result)
        self.assertIn("b.py", result)

    def test_search_content_phrase(self):
        result = main.try_fast_path(f"search for hello in {self.root}")
        self.assertIsNotNone(result)
        self.assertIn("a.txt:1", result)

    def test_find_name_phrase(self):
        result = main.try_fast_path(f"find files named a in {self.root}")
        self.assertIsNotNone(result)
        self.assertIn("a.txt", result)

    def test_open_missing_falls_through(self):
        result = main.try_fast_path(f"open {self.root / 'missing.txt'}")
        self.assertIsNone(result)

    def test_unrelated_text_returns_none(self):
        self.assertIsNone(main.try_fast_path("tell me a joke"))


class TestCoderGuard(unittest.TestCase):
    def test_run_coder_without_config(self):
        # init_skills() is never called in this suite, so run_coder must
        # fail cleanly instead of crashing or hitting the network.
        result = DISPATCH["run_coder"](instruction="explain this code")
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
