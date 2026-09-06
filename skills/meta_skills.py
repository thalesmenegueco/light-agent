"""
skills/meta_skills.py
Skills about the agent itself: list what it can do, and read/edit its own
runtime config. `get_config`/`set_config` need the loaded config, so
__init__.py injects it via `bind_config()` (see skills/__init__.py).

`set_config` validates updates against DEFAULT_CONFIG and persists them with
config.save_config(), so the agent can change e.g. its router model at
runtime without hand-editing config.json.
"""

from config import DEFAULT_CONFIG, save_config

_CONFIG = None

_INT_KEYS = {"max_history_messages", "run_command_timeout", "run_command_max_output"}
_NUM_KEYS = {"router_temperature", "coder_temperature"}
_STR_KEYS = {"ollama_host", "router_model", "coder_model", "log_file", "run_command_cwd"}
_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}
_BOOL_KEYS = {"run_command_shell", "run_command_allow_network"}
_LIST_STR_KEYS = {"run_command_allowlist", "run_command_denylist"}
_RUN_COMMAND_MODES = {"off", "confirm", "allowlist", "auto"}


def bind_config(config: dict) -> None:
    global _CONFIG
    _CONFIG = config


def list_skills() -> dict:
    """List every registered tool/skill with its one-line description."""
    from skills import TOOLS  # lazy import to avoid a circular import at load time
    return {
        "skills": [
            {"name": schema["function"]["name"], "description": schema["function"]["description"]}
            for schema in TOOLS
        ],
        "count": len(TOOLS),
    }


def get_config() -> dict:
    """Return the current runtime configuration."""
    if _CONFIG is None:
        return {"error": "meta_skills not initialized with config"}
    return {"config": dict(_CONFIG)}


def set_config(updates: dict) -> dict:
    """Validate and apply config updates, then persist them to disk."""
    global _CONFIG
    if _CONFIG is None:
        return {"error": "meta_skills not initialized with config"}
    if not isinstance(updates, dict):
        return {"error": "updates must be an object of key/value pairs"}

    unknown = sorted(k for k in updates if k not in DEFAULT_CONFIG)
    if unknown:
        return {"error": f"Unknown config keys: {', '.join(unknown)}"}

    for key in _INT_KEYS & set(updates):
        value = updates[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            return {"error": f"{key} must be a positive integer"}
    for key in _NUM_KEYS & set(updates):
        value = updates[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return {"error": f"{key} must be a number"}
    for key in _STR_KEYS & set(updates):
        if not isinstance(updates[key], str):
            return {"error": f"{key} must be a string"}
    for key in _BOOL_KEYS & set(updates):
        if not isinstance(updates[key], bool):
            return {"error": f"{key} must be a boolean"}
    for key in _LIST_STR_KEYS & set(updates):
        value = updates[key]
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            return {"error": f"{key} must be a list of strings"}
    if "log_level" in updates and str(updates["log_level"]).upper() not in _LOG_LEVELS:
        return {"error": f"log_level must be one of {sorted(_LOG_LEVELS)}"}
    if "run_command_mode" in updates and updates["run_command_mode"] not in _RUN_COMMAND_MODES:
        return {"error": f"run_command_mode must be one of {sorted(_RUN_COMMAND_MODES)}"}

    _CONFIG.update(updates)
    save_config(_CONFIG)
    return {"config": dict(_CONFIG)}


SCHEMAS = [
    (
        {
            "type": "function",
            "function": {
                "name": "list_skills",
                "description": (
                    "List every tool/skill the assistant can use, with a one-line "
                    "description of each. Use this when asked what you can do."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
        list_skills,
    ),
    (
        {
            "type": "function",
            "function": {
                "name": "get_config",
                "description": (
                    "Read the agent's current configuration (Ollama host, model names, "
                    "temperatures, history limit, logging settings)."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
        get_config,
    ),
    (
        {
            "type": "function",
            "function": {
                "name": "set_config",
                "description": (
                    "Update one or more configuration values and persist them to disk. "
                    "Known keys: ollama_host, router_model, coder_model, "
                    "router_temperature, coder_temperature, max_history_messages, "
                    "log_level, log_file, and run_command_* safety settings."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "updates": {
                            "type": "object",
                            "description": "Map of config key to its new value (only known keys are allowed).",
                        },
                    },
                    "required": ["updates"],
                },
            },
        },
        set_config,
    ),
]
