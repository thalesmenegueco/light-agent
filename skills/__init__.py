"""
skills/__init__.py
Skill registry. To add a new skill:
  1. Create skills/your_skill.py
  2. Define your function(s) and a module-level SCHEMAS list of
     (schema_dict, function) pairs -- see fs_skills.py for the pattern.
  3. Import the module below and add it to _SKILL_MODULES.

That's it -- TOOLS and DISPATCH update automatically, and router.py
never needs to change.
"""

from . import code_skills, fs_skills, git_skills, meta_skills, run_command_skills, search_skills, verify_skills
from platform_utils import set_project_root

_SKILL_MODULES = [fs_skills, search_skills, git_skills, code_skills, meta_skills, run_command_skills, verify_skills]

TOOLS: list[dict] = []
DISPATCH: dict[str, callable] = {}

for _module in _SKILL_MODULES:
    for _schema, _func in _module.SCHEMAS:
        TOOLS.append(_schema)
        DISPATCH[_schema["function"]["name"]] = _func


def init_skills(config: dict) -> None:
    """Call once at startup to hand config to skills that need it (coder, meta)."""
    code_skills.bind_config(config)
    meta_skills.bind_config(config)
    run_command_skills.bind_config(config)
    fs_skills.bind_config(config)
    git_skills.bind_config(config)
    verify_skills.bind_config(config)
    set_project_root(config.get("project_root", ""))


def exclude_skills(names) -> None:
    """Remove skills by function name from TOOLS and DISPATCH, in place.

    Unattended (autopilot) runs shouldn't be offered skills that are pointless
    or unwanted without a human -- e.g. open_file, which launches the OS GUI
    app. Mutates the module-level containers in place so modules that imported
    TOOLS/DISPATCH by reference (router.py, main.py) see the change too.
    """
    excluded = set(names)
    TOOLS[:] = [schema for schema in TOOLS if schema["function"]["name"] not in excluded]
    for name in excluded:
        DISPATCH.pop(name, None)
