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

from . import code_skills, fs_skills, git_skills, meta_skills, search_skills

_SKILL_MODULES = [fs_skills, search_skills, git_skills, code_skills, meta_skills]

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
