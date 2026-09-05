"""
router.py
Owns the conversation with the router model (phi4-mini) and the
tool-calling loop: send message + TOOLS -> if tool_calls, execute
locally via DISPATCH -> feed results back -> get final reply.
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
    "yourself. Otherwise, answer directly and concisely."
)


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
    return resp.json()


def handle_message(config: dict, history: list[dict], user_message: str) -> tuple[str, list[dict]]:
    """
    Run one turn of the agent loop.
    Returns (assistant_reply_text, updated_history).
    history is a list of {"role", "content"} dicts (trimmed by caller).
    """
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history
    messages.append({"role": "user", "content": user_message})

    data = _call_ollama(config, messages)
    message = data.get("message", {})
    tool_calls = message.get("tool_calls") or []

    if not tool_calls:
        reply = message.get("content", "").strip()
        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": reply})
        return reply, history

    # Execute each requested tool call locally, then ask the model to
    # produce a final natural-language answer using the results.
    messages.append(message)
    for call in tool_calls:
        fn = call.get("function", {})
        name = fn.get("name")
        raw_args = fn.get("arguments", {})

        if isinstance(raw_args, dict):
            args = raw_args
        else:
            try:
                args = json.loads(raw_args or "{}")
            except json.JSONDecodeError as exc:
                logger.error("Invalid tool arguments for %r: %s", name, exc)
                args = {}

        func = DISPATCH.get(name)
        if func is None:
            logger.error("Model requested unknown tool: %r", name)
            result = {"error": f"Unknown tool: {name}"}
        else:
            try:
                result = func(**args)
            except TypeError as exc:
                logger.error("Bad arguments for tool %r: %s (args=%r)", name, exc, args)
                result = {"error": f"Bad arguments for {name}: {exc}"}
            except Exception:
                logger.exception("Tool %r raised an error", name)
                result = {"error": f"Tool {name} failed; see the log file for details."}

        messages.append(
            {
                "role": "tool",
                "content": json.dumps(result),
            }
        )

    final = _call_ollama(config, messages, use_tools=False)
    reply = final.get("message", {}).get("content", "").strip()

    history.append({"role": "user", "content": user_message})
    history.append({"role": "assistant", "content": reply})
    return reply, history
