"""
router.py
Owns the conversation with the router model and the tool-calling loop:
send message + TOOLS -> if tool_calls, execute locally via DISPATCH -> feed
results back -> ask again (multi-round) -> get the final reply.
"""

import json
import logging

import requests

from skills import DISPATCH, TOOLS

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a lightweight local assistant running on limited hardware. "
    "You have tools for filesystem operations and for delegating coding "
    "questions to a specialized coder model. Use a tool whenever the "
    "request maps to one -- don't try to do file listing or code analysis "
    "yourself. You may call tools across several rounds to gather information "
    "before answering, but stop as soon as you have enough. Otherwise, answer "
    "directly and concisely."
)


_WARMUP_TIMEOUT = 300  # generous: cold model load on CPU can exceed the 120s per-turn cap
_DEFAULT_MAX_TOOL_ROUNDS = 4  # tool-calling rounds before forcing a final answer

# Session-wide token accounting, used by the autopilot's budget. `_call_ollama`
# and `plan_goal` accumulate `eval_count` + `prompt_eval_count` here; the
# autopilot resets/reads it between steps so a token cap can bound a whole run.
_SESSION_TOKENS = 0


def reset_token_counter() -> None:
    global _SESSION_TOKENS
    _SESSION_TOKENS = 0


def get_token_count() -> int:
    return _SESSION_TOKENS


def _accumulate_tokens(data: dict) -> None:
    global _SESSION_TOKENS
    _SESSION_TOKENS += int(data.get("eval_count", 0) or 0) + int(data.get("prompt_eval_count", 0) or 0)


def warm_up(config: dict) -> None:
    """Pre-load the router model into RAM so the first turn isn't cold.

    Generation is capped at one token -- the cost here is dominated by the
    model load, not by output. Raises requests.RequestException on failure;
    callers report it (non-fatal: the first real turn would just be slow).
    """
    payload = {
        "model": config["router_model"],
        "messages": [{"role": "user", "content": "ping"}],
        "stream": False,
        "options": {"temperature": 0.0, "num_predict": 1},
    }
    resp = requests.post(
        f"{config['ollama_host']}/api/chat",
        json=payload,
        timeout=_WARMUP_TIMEOUT,
    )
    resp.raise_for_status()


def _call_ollama(config: dict, messages: list[dict], use_tools: bool = True) -> dict:
    payload = {
        "model": config["router_model"],
        "messages": messages,
        "stream": False,
        "options": {"temperature": config.get("router_temperature", 0.2)},
    }
    if use_tools:
        payload["tools"] = TOOLS

    resp = requests.post(
        f"{config['ollama_host']}/api/chat",
        json=payload,
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    _accumulate_tokens(data)
    return data


def _parse_tool_args(raw_args) -> dict:
    """Normalize tool arguments, which Ollama may return as a dict or a JSON string."""
    if isinstance(raw_args, dict):
        return raw_args
    try:
        return json.loads(raw_args or "{}")
    except (json.JSONDecodeError, TypeError) as exc:
        logger.error("Invalid tool arguments: %s", exc)
        return {}


def _run_tool_call(name: str, args: dict) -> dict:
    """Execute one tool call locally, returning a result dict (never raises)."""
    func = DISPATCH.get(name)
    if func is None:
        logger.error("Model requested unknown tool: %r", name)
        return {"error": f"Unknown tool: {name}"}
    try:
        return func(**args)
    except TypeError as exc:
        logger.error("Bad arguments for tool %r: %s (args=%r)", name, exc, args)
        return {"error": f"Bad arguments for {name}: {exc}"}
    except Exception:
        logger.exception("Tool %r raised an error", name)
        return {"error": f"Tool {name} failed; see the log file for details."}


def handle_message(config: dict, history: list[dict], user_message: str) -> tuple[str, list[dict]]:
    """
    Run one turn of the agent loop.

    The tool-calling loop is recursive (multi-round): each round sends the
    conversation + tools, executes any requested tool calls locally, feeds the
    results back, and asks again -- so the router can chain e.g. list -> read
    -> diagnose without a fresh user prompt. A per-turn round cap bounds the
    token cost on limited hardware; hitting it forces a final plain-text answer.

    Returns (assistant_reply_text, updated_history).
    history is a list of {"role", "content"} dicts (trimmed by caller); the
    intermediate tool messages stay inside the turn and never enter history.
    """
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history
    messages.append({"role": "user", "content": user_message})

    max_rounds = config.get("max_tool_rounds", _DEFAULT_MAX_TOOL_ROUNDS)
    reply = ""

    for _ in range(max_rounds):
        data = _call_ollama(config, messages)
        message = data.get("message", {})
        tool_calls = message.get("tool_calls") or []

        if not tool_calls:
            reply = message.get("content", "").strip()
            break

        messages.append(message)
        for call in tool_calls:
            fn = call.get("function", {})
            name = fn.get("name")
            args = _parse_tool_args(fn.get("arguments", {}))
            result = _run_tool_call(name, args)
            messages.append({"role": "tool", "content": json.dumps(result)})
    else:
        # Exhausted the round cap while the model was still asking for tools.
        # Ask for a final plain-text answer using whatever it has gathered.
        messages.append(
            {
                "role": "user",
                "content": (
                    "You have reached the tool-call limit for this request. "
                    "Answer the user's original request using the information "
                    "gathered so far."
                ),
            }
        )
        final = _call_ollama(config, messages, use_tools=False)
        reply = final.get("message", {}).get("content", "").strip()

    history.append({"role": "user", "content": user_message})
    history.append({"role": "assistant", "content": reply})
    return reply, history


_PLAN_SYSTEM = (
    "You are a planning assistant. Break the user's goal into a short ordered "
    "list of concrete, small steps, each of which can be accomplished with local "
    "filesystem, git, search, or code tools. Respond with ONLY a JSON array of "
    "strings -- no commentary, no markdown fences."
)


def _parse_plan(content: str) -> list[str]:
    """Parse a planner response into a list of step strings (never raises)."""
    text = (content or "").strip()
    if not text:
        return []
    # Tolerate markdown fences the model sometimes emits despite instructions.
    if text.startswith("```"):
        text = text.strip("`")
        text = text.removeprefix("json").strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Planner returned non-JSON; treating as a single step: %r", text[:200])
        return [text]
    if isinstance(parsed, list):
        return [str(step).strip() for step in parsed if str(step).strip()]
    if isinstance(parsed, dict) and isinstance(parsed.get("steps"), list):
        return [str(step).strip() for step in parsed["steps"] if str(step).strip()]
    return [text]


def plan_goal(config: dict, goal: str) -> list[str]:
    """Break a goal into an ordered list of steps via the router model.

    Uses JSON-mode output and counts the call against the session token budget.
    Raises requests.RequestException on transport failure; callers decide how
    to degrade (e.g. fall back to a single-step plan).
    """
    payload = {
        "model": config["router_model"],
        "messages": [
            {"role": "system", "content": _PLAN_SYSTEM},
            {"role": "user", "content": goal},
        ],
        "stream": False,
        "format": "json",
        "options": {"temperature": config.get("router_temperature", 0.2)},
    }
    resp = requests.post(
        f"{config['ollama_host']}/api/chat",
        json=payload,
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    _accumulate_tokens(data)
    steps = _parse_plan(data.get("message", {}).get("content", ""))
    return steps or [goal.strip()]
