"""
autopilot.py
Unattended planner-vs-executor loop with a session budget and crash-resume.

Two roles, two calls to the same router model:
  planner   -> router.plan_goal(goal) breaks the goal into an ordered step list
  executor  -> router.handle_message(config, history, step) runs ONE step
               through the normal tool-calling loop (the coder leaf and all
               deterministic skills are available to it as tools)

Guardrails:
  - session budget: max_session_steps / session_timeout_seconds /
    max_session_tokens are checked before every step; exceeding any of them
    stops the run and marks it blocked.
  - verification loop: when test_command is configured, run_tests() runs after
    each step and failures are fed back to the executor up to `verify_rounds`
    times, closing the code -> test -> fix loop.
  - persistence: session.py stores goal/plan/progress/budget/history after
    every step, so a crashed run resumes from the last completed step.

Executor / planner / test_runner / clock / on_progress are injectable so the
loop is fully testable offline (no Ollama, no subprocess).
"""

import logging
import time

import router
import session as session_mod
from skills import verify_skills

logger = logging.getLogger(__name__)

# A blocked run (budget exhausted) is resumable too: the user can raise the
# budget and re-run the same goal to continue from the first unfinished step.
_RESUMABLE = {"planning", "running", "paused", "blocked"}


def budget_exceeded(state: dict, config: dict, elapsed_seconds: float) -> str | None:
    """Return a reason string if the session budget is exhausted, else None."""
    max_steps = int(config.get("max_session_steps", 0) or 0)
    if max_steps > 0 and state.get("steps_used", 0) >= max_steps:
        return f"step budget exceeded ({max_steps})"
    timeout = int(config.get("session_timeout_seconds", 0) or 0)
    if timeout > 0 and elapsed_seconds >= timeout:
        return f"time budget exceeded ({timeout}s)"
    max_tokens = int(config.get("max_session_tokens", 0) or 0)
    if max_tokens > 0 and state.get("tokens_used", 0) >= max_tokens:
        return f"token budget exceeded ({max_tokens})"
    return None


def _default_executor(config, history, prompt):
    return router.handle_message(config, history, prompt)


def _default_planner(config, goal):
    return router.plan_goal(config, goal)


def _default_test_runner(config):
    return verify_skills.run_tests()


def _fix_prompt(result: dict) -> str:
    tail = ((result.get("stderr") or "") or (result.get("stdout") or ""))[-4000:]
    return (
        f"The test suite failed with exit code {result.get('exit_code')}. "
        f"Fix the code and re-run the tests. Test output (tail):\n{tail}"
    )


def run_autopilot(
    config: dict,
    goal: str,
    *,
    session_path=None,
    executor=None,
    planner=None,
    test_runner=None,
    clock=None,
    on_progress=None,
) -> dict:
    """Run (or resume) an unattended plan/execute loop to completion.

    Returns the final session state dict. Persists progress to session_path
    (defaults to config.get_base_dir()/session.json) after every step.
    """
    executor = executor or _default_executor
    planner = planner or _default_planner
    test_runner = test_runner or _default_test_runner
    clock = clock or time.time
    on_progress = on_progress or logger.info

    router.reset_token_counter()
    state = session_mod.load_session(session_path)

    if (
        state is not None
        and state.get("goal") == goal
        and state.get("status") in _RESUMABLE
        and state.get("plan")
    ):
        on_progress(f"Resuming session at step {state.get('current_step', 0) + 1}/{len(state['plan'])}")
    else:
        if state is not None:
            # A prior session exists but is finished/for a different goal: start fresh.
            on_progress("Starting a new session (previous one is finished or unrelated).")
        state = session_mod.new_session(goal)
        try:
            steps = planner(config, goal)
        except Exception as exc:  # transport/model failure -> degrade to one step
            logger.exception("Planner failed; falling back to a single-step plan")
            steps = [goal.strip()]
        state["plan"] = [{"step": s, "status": "pending", "result": None} for s in steps if s]
        if not state["plan"]:
            state["plan"] = [{"step": goal.strip(), "status": "pending", "result": None}]
        state["status"] = "running"
        session_mod.save_session(state, session_path)

    history = list(state.get("history") or [])
    started_at = state.get("started_at") or clock()
    max_history = int(config.get("max_history_messages", 12))

    while state["current_step"] < len(state["plan"]):
        reason = budget_exceeded(state, config, clock() - started_at)
        if reason:
            state["status"] = "blocked"
            state["blocked_reason"] = reason
            session_mod.save_session(state, session_path)
            on_progress(f"Stopped: {reason}.")
            return state

        idx = state["current_step"]
        step = state["plan"][idx]["step"]
        total = len(state["plan"])
        state["plan"][idx]["status"] = "in_progress"
        session_mod.save_session(state, session_path)
        on_progress(f"Step {idx + 1}/{total}: {step}")

        reply, history = executor(config, history, step)
        state["steps_used"] += 1
        state["tokens_used"] += router.get_token_count()
        router.reset_token_counter()

        # Verification loop: run tests, feed failures back to the executor.
        verify_rounds = int(config.get("verify_rounds", 0) or 0)
        if verify_rounds > 0 and (config.get("test_command") or "").strip():
            for round_no in range(verify_rounds):
                result = test_runner(config)
                if result.get("passed"):
                    break
                if "error" in result:
                    on_progress(f"Verification unavailable: {result.get('error')}")
                    break
                on_progress(f"Tests failed (exit {result.get('exit_code')}); fixing ({round_no + 1}/{verify_rounds})")
                reply, history = executor(config, history, _fix_prompt(result))
                state["steps_used"] += 1
                state["tokens_used"] += router.get_token_count()
                router.reset_token_counter()

        state["plan"][idx]["status"] = "done"
        state["plan"][idx]["result"] = reply
        state["current_step"] += 1
        state["history"] = history[-max_history:] if max_history > 0 else []
        session_mod.save_session(state, session_path)

    state["status"] = "done"
    session_mod.save_session(state, session_path)
    on_progress(f"Finished {len(state['plan'])} step(s).")
    return state
