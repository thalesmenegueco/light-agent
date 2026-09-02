"""
coder.py
Wraps calls to the coder model (qwen2.5-coder:3b). This is a leaf node --
it does NOT get tool-calling or full conversation history, just the task
at hand, to stay fast and keep context small on limited hardware.
"""

import requests


def ask_coder(config: dict, instruction: str, file_content: str | None = None) -> str:
    """
    Ask the coder model to analyze/write code.
    instruction: what the user wants (e.g. "why isn't this working")
    file_content: optional code/file text to analyze
    """
    prompt_parts = [instruction]
    if file_content:
        prompt_parts.append("\n\n--- FILE CONTENT ---\n" + file_content)

    payload = {
        "model": config["coder_model"],
        "messages": [{"role": "user", "content": "\n".join(prompt_parts)}],
        "stream": False,
        "options": {"temperature": config.get("coder_temperature", 0.1)},
    }

    resp = requests.post(
        f"{config['ollama_host']}/api/chat",
        json=payload,
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("message", {}).get("content", "").strip()
