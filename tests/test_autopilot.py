"""
tests/test_autopilot.py
Unit tests for the planner/executor autopilot loop (autopilot.py) and the
planner response parser (router._parse_plan). Everything runs offline: the
executor, planner, and test runner are injected fakes, so no Ollama and no
subprocess is needed:

    python -m unittest            # from the project root
    python -m unittest discover -s tests -v
"""

import sys
import tempfile
import unittest
from pathlib import Path

# Make the project root importable regardless of how unittest is invoked.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

import autopilot
import router
from config import DEFAULT_CONFIG


def _config(**overrides) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(overrides)
    return cfg


def _planner(steps):
    return lambda config, goal: list(steps)


def _raising_planner(exc):
    def planner(config, goal):
        raise exc
    return planner


def _passing_tests(config):
    return {"passed": True, "exit_code": 0, "stdout": "", "stderr": ""}


class RecordingExecutor:
    def __init__(self, replies=None):
        self.replies = list(replies) if replies is not None else ["ok"]
        self.default = "ok"
        self.calls = []

    def __call__(self, config, history, prompt):
        self.calls.append(prompt)
        reply = self.replies.pop(0) if self.replies else self.default
        history = list(history) + [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": reply},
        ]
        return reply, history


class FlakyExecutor:
    """Raises a model error on the first call, then behaves normally."""

    def __init__(self, exc):
        self.exc = exc
        self.calls = 0

    def __call__(self, config, history, prompt):
        self.calls += 1
        if self.calls == 1:
            raise self.exc
        history = list(history) + [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": "ok"},
        ]
        return "ok", history


class TestBudget(unittest.TestCase):
    def test_no_budget_configured(self):
        state = {"steps_used": 1000, "tokens_used": 1000}
        self.assertIsNone(autopilot.budget_exceeded(state, _config(), 999999))

    def test_step_budget(self):
        state = {"steps_used": 3, "tokens_used": 0}
        self.assertIsNotNone(autopilot.budget_exceeded(state, _config(max_session_steps=3), 0))
        self.assertIsNone(autopilot.budget_exceeded(state, _config(max_session_steps=4), 0))

    def test_time_budget(self):
        state = {"steps_used": 0, "tokens_used": 0}
        self.assertIsNotNone(autopilot.budget_exceeded(state, _config(session_timeout_seconds=60), 60))
        self.assertIsNone(autopilot.budget_exceeded(state, _config(session_timeout_seconds=60), 59))

    def test_token_budget(self):
        state = {"steps_used": 0, "tokens_used": 500}
        self.assertIsNotNone(autopilot.budget_exceeded(state, _config(max_session_tokens=500), 0))
        self.assertIsNone(autopilot.budget_exceeded(state, _config(max_session_tokens=501), 0))


class TestParsePlan(unittest.TestCase):
    def test_json_array(self):
        self.assertEqual(router._parse_plan('["a", "b"]'), ["a", "b"])

    def test_json_object_with_steps(self):
        self.assertEqual(router._parse_plan('{"steps": ["a", "b"]}'), ["a", "b"])

    def test_markdown_fenced_array(self):
        self.assertEqual(router._parse_plan('```json\n["a", "b"]\n```'), ["a", "b"])

    def test_non_json_falls_back_to_single_step(self):
        self.assertEqual(router._parse_plan("just do the thing"), ["just do the thing"])

    def test_empty(self):
        self.assertEqual(router._parse_plan(""), [])


class TestRunAutopilot(unittest.TestCase):
    def test_fresh_run_completes_all_steps(self):
        with tempfile.TemporaryDirectory() as tmp:
            sp = Path(tmp) / "session.json"
            executor = RecordingExecutor()
            state = autopilot.run_autopilot(
                _config(),
                "do three things",
                session_path=sp,
                planner=_planner(["a", "b", "c"]),
                executor=executor,
                test_runner=_passing_tests,
            )
            self.assertEqual(state["status"], "done")
            self.assertEqual(state["current_step"], 3)
            self.assertEqual(state["steps_used"], 3)
            self.assertTrue(all(s["status"] == "done" for s in state["plan"]))

    def test_step_budget_blocks_and_marks_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            sp = Path(tmp) / "session.json"
            state = autopilot.run_autopilot(
                _config(max_session_steps=1),
                "goal",
                session_path=sp,
                planner=_planner(["a", "b", "c"]),
                executor=RecordingExecutor(),
                test_runner=_passing_tests,
            )
            self.assertEqual(state["status"], "blocked")
            self.assertIn("step budget", state["blocked_reason"])
            self.assertEqual(state["steps_used"], 1)
            self.assertEqual(state["current_step"], 1)

    def test_resume_continues_from_last_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            sp = Path(tmp) / "session.json"
            executor = RecordingExecutor()
            # First run blocked by a 1-step budget after step 1.
            first = autopilot.run_autopilot(
                _config(max_session_steps=1),
                "goal",
                session_path=sp,
                planner=_planner(["a", "b", "c"]),
                executor=executor,
                test_runner=_passing_tests,
            )
            self.assertEqual(first["status"], "blocked")
            # Resume with no budget: should finish steps b and c (total 3).
            second = autopilot.run_autopilot(
                _config(),
                "goal",
                session_path=sp,
                planner=_planner(["a", "b", "c"]),
                executor=executor,
                test_runner=_passing_tests,
            )
            self.assertEqual(second["status"], "done")
            self.assertEqual(second["steps_used"], 3)
            self.assertEqual(second["current_step"], 3)

    def test_planner_failure_falls_back_to_single_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            sp = Path(tmp) / "session.json"
            state = autopilot.run_autopilot(
                _config(),
                "the whole goal",
                session_path=sp,
                planner=_raising_planner(RuntimeError("boom")),
                executor=RecordingExecutor(),
                test_runner=_passing_tests,
            )
            self.assertEqual(state["status"], "done")
            self.assertEqual(state["plan"], [{"step": "the whole goal", "status": "done", "result": "ok"}])

    def test_verification_failure_feeds_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            sp = Path(tmp) / "session.json"
            executor = RecordingExecutor()
            results = iter([
                {"passed": False, "exit_code": 1, "stdout": "", "stderr": "AssertionError"},
                {"passed": True, "exit_code": 0, "stdout": "", "stderr": ""},
            ])
            state = autopilot.run_autopilot(
                _config(test_command="pytest", verify_rounds=3),
                "goal",
                session_path=sp,
                planner=_planner(["a"]),
                executor=executor,
                test_runner=lambda config: next(results),
            )
            # One execution for the step + one fix round.
            self.assertEqual(state["status"], "done")
            self.assertEqual(state["steps_used"], 2)
            self.assertIn("AssertionError", executor.calls[1])

    def test_completed_session_starts_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            sp = Path(tmp) / "session.json"
            autopilot.run_autopilot(
                _config(), "goal", session_path=sp,
                planner=_planner(["a"]), executor=RecordingExecutor(), test_runner=_passing_tests,
            )
            # A second run with a DIFFERENT plan must not resume the "done" session.
            state = autopilot.run_autopilot(
                _config(), "goal", session_path=sp,
                planner=_planner(["x", "y"]), executor=RecordingExecutor(), test_runner=_passing_tests,
            )
            self.assertEqual(state["status"], "done")
            self.assertEqual(state["current_step"], 2)
            self.assertEqual([s["step"] for s in state["plan"]], ["x", "y"])

    def test_model_timeout_blocks_and_resumes(self):
        with tempfile.TemporaryDirectory() as tmp:
            sp = Path(tmp) / "session.json"
            executor = FlakyExecutor(requests.exceptions.ReadTimeout("Read timed out."))
            # First run: step 1 times out -> blocked, step not advanced.
            first = autopilot.run_autopilot(
                _config(), "goal", session_path=sp,
                planner=_planner(["a", "b"]), executor=executor, test_runner=_passing_tests,
            )
            self.assertEqual(first["status"], "blocked")
            self.assertIn("timed out", first["blocked_reason"])
            self.assertEqual(first["current_step"], 0)
            self.assertEqual(first["steps_used"], 0)
            # Resume (same goal): the executor now succeeds for both steps.
            second = autopilot.run_autopilot(
                _config(), "goal", session_path=sp,
                planner=_planner(["a", "b"]), executor=executor, test_runner=_passing_tests,
            )
            self.assertEqual(second["status"], "done")
            self.assertEqual(second["current_step"], 2)
            self.assertEqual(second["steps_used"], 2)

    def test_model_connection_error_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            sp = Path(tmp) / "session.json"
            executor = FlakyExecutor(requests.exceptions.ConnectionError("boom"))
            state = autopilot.run_autopilot(
                _config(), "goal", session_path=sp,
                planner=_planner(["a"]), executor=executor, test_runner=_passing_tests,
            )
            self.assertEqual(state["status"], "blocked")
            self.assertIn("model request failed", state["blocked_reason"])

    def test_model_error_reason_distinguishes_timeout(self):
        self.assertIn("timed out", autopilot._model_error_reason(
            requests.exceptions.ReadTimeout("x"), _config()))
        self.assertIn("ollama_timeout", autopilot._model_error_reason(
            requests.exceptions.ReadTimeout("x"), _config()))
        self.assertIn("model request failed", autopilot._model_error_reason(
            requests.exceptions.ConnectionError("x"), _config()))


if __name__ == "__main__":
    unittest.main()
