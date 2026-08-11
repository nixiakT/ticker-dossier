"""Read-only tools for loading project Skills."""
from __future__ import annotations

from ticker_dossier.skills.loader import read_skill

from ticker_dossier.runtime.tools import Tool


def _read_skill(name: str) -> str:
    return read_skill(name).body


read_skill_tool = Tool(
    name="read_skill",
    description="按名称只读加载项目 Skill 的完整正文；先从系统提示中的 Skills 清单选择名称。",
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Skill frontmatter 中声明的小写短横线名称",
            },
        },
        "required": ["name"],
    },
    run=_read_skill,
)
