"""
coder.py
Wraps calls to the coder model (qwen2.5-coder:3b). This is a leaf node --
it does NOT get tool-calling or full conversation history, just the task
at hand, to stay fast and keep context small on limited hardware.
"""

import logging

import requests

logger = logging.getLogger(__name__)

# The coder is warmed lazily on first actual use (not at startup), so read-only
# sessions don't pay the ~30-45s cold-load cost. Fail-once: a failed warm-up is
# logged and not retried -- the real request still runs (possibly slowly).
_WARMED = False

# Output contract for the coder leaf. It is asked both to *explain* and to
# *produce* code; this prompt pins down which shape to return so a generated
# file can be written verbatim (via `write_code`) without the router having to
# peel off prose or markdown fences.
SYSTEM_PROMPT = (
    "You are a coding assistant for a local coding agent. "
    "When asked to write, generate, or fix a file, output ONLY the code for "
    "that file -- no markdown fences, no explanations, and no preamble. "
    "When asked to explain, review, or debug, answer concisely in plain text."
)


def ask_coder(config: dict, instruction: str, file_content: str | None = None) -> str:
    """
    Ask the coder model to analyze/write code.
    instruction: what the user wants (e.g. "why isn't this working")
    file_content: optional code/file text to analyze
    """
    global _WARMED
    # Lazy import to avoid a circular import (router -> skills -> code_skills
    # -> coder). The helpers are only needed at call time, when router is
    # fully loaded.
    from router import accumulate_tokens, model_timeout, read_streamed_response, warm_up

    if not _WARMED:
        _WARMED = True
        try:
            warm_up(config, config["coder_model"])
        except requests.RequestException as exc:
            logger.warning("Coder warm-up failed: %s", exc)

    prompt_parts = [instruction]
    if file_content:
        prompt_parts.append("\n\n--- FILE CONTENT ---\n" + file_content)

    payload = {
        "model": config["coder_model"],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "\n".join(prompt_parts)},
        ],
        "stream": True,
        "options": {"temperature": config.get("coder_temperature", 0.1)},
    }

    resp = requests.post(
        f"{config['ollama_host']}/api/chat",
        json=payload,
        stream=True,
        timeout=model_timeout(config),
    )
    resp.raise_for_status()
    data = read_streamed_response(resp)
    # Accumulate the coder's tokens into the session budget so an autopilot's
    # token cap bounds the WHOLE run, not just the router.
    accumulate_tokens(data)
    return data.get("message", {}).get("content", "").strip()
