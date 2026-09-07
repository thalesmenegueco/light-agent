"""
tests/test_eval_coding.py
Offline unit tests for the coding-skill eval harness (eval_coding.py): the
check rules, task loading, and task evaluation with a scripted asker. No Ollama
needed -- the real model call is injected/replaced.

    python -m unittest            # from the project root
    python -m unittest discover -s tests -v
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

# Make the project root importable regardless of how unittest is invoked.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import eval_coding


class TestRunChecks(unittest.TestCase):
    def test_contains_pass(self):
        self.assertEqual(
            eval_coding.run_checks("def foo():\n pass", [{"type": "contains", "value": "def foo"}]),
            [],
        )

    def test_contains_fail(self):
        failures = eval_coding.run_checks("x = 1", [{"type": "contains", "value": "def"}])
        self.assertEqual(len(failures), 1)
        self.assertIn("missing substring", failures[0]["error"])

    def test_not_contains(self):
        self.assertEqual(
            eval_coding.run_checks("code", [{"type": "not_contains", "value": "```"}]), []
        )
        failures = eval_coding.run_checks("```x```", [{"type": "not_contains", "value": "```"}])
        self.assertEqual(len(failures), 1)

    def test_regex(self):
        self.assertEqual(
            eval_coding.run_checks("s[::-1]", [{"type": "regex", "value": r"s\[::-1\]|reversed"}]), []
        )
        failures = eval_coding.run_checks("abc", [{"type": "regex", "value": r"\d+"}])
        self.assertEqual(len(failures), 1)

    def test_case_insensitive_contains(self):
        self.assertEqual(
            eval_coding.run_checks(
                "Hello", [{"type": "contains", "value": "hello", "case_sensitive": False}]
            ),
            [],
        )
        self.assertEqual(
            len(eval_coding.run_checks("Hello", [{"type": "contains", "value": "hello"}])), 1
        )

    def test_unknown_type(self):
        failures = eval_coding.run_checks("x", [{"type": "bogus", "value": "x"}])
        self.assertIn("unknown check type", failures[0]["error"])

    def test_bad_regex_reports_error(self):
        failures = eval_coding.run_checks("x", [{"type": "regex", "value": "("}])
        self.assertIn("bad regex", failures[0]["error"])


class TestLoadTasks(unittest.TestCase):
    def test_loads_and_skips_empty_prompts_and_junk(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.json"
            p.write_text(
                json.dumps(
                    [
                        {"id": "a", "prompt": "write x"},
                        {"id": "b", "prompt": "   "},
                        "junk",
                    ]
                ),
                encoding="utf-8",
            )
            tasks = eval_coding.load_tasks(p)
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0]["id"], "a")

    def test_non_list_raises(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.json"
            p.write_text('{"not": "a list"}', encoding="utf-8")
            with self.assertRaises(ValueError):
                eval_coding.load_tasks(p)


class TestEvaluateTask(unittest.TestCase):
    def test_passes_with_fake_asker(self):
        def ask(config, prompt, context=None):
            return "def fizzbuzz(n): pass"

        result = eval_coding.evaluate_task(
            {},
            {"id": "x", "prompt": "write", "checks": [{"type": "contains", "value": "def fizzbuzz"}]},
            ask=ask,
        )
        self.assertTrue(result["passed"])

    def test_fails_with_fake_asker(self):
        def ask(config, prompt, context=None):
            return "nope"

        result = eval_coding.evaluate_task(
            {},
            {"id": "x", "prompt": "write", "checks": [{"type": "contains", "value": "def"}]},
            ask=ask,
        )
        self.assertFalse(result["passed"])

    def test_transport_error_is_a_fail_not_a_crash(self):
        def ask(config, prompt, context=None):
            raise RuntimeError("boom")

        result = eval_coding.evaluate_task(
            {},
            {"id": "x", "prompt": "write", "checks": [{"type": "contains", "value": "def"}]},
            ask=ask,
        )
        self.assertFalse(result["passed"])
        self.assertEqual(result["failures"][0]["type"], "transport")


if __name__ == "__main__":
    unittest.main()
