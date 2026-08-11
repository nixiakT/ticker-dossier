"""Create reusable Skills from successful execution traces."""
from __future__ import annotations

from ticker_dossier.runtime.tools import Tool
from ticker_dossier.skills import generate_skill


def _trace2skill_generate(
    task: str,
    trace: str,
    skill_name: str = "",
    description: str = "",
    overwrite: bool = False,
) -> str:
    result = generate_skill(
        task=task,
        trace=trace,
        skill_name=skill_name or None,
        description=description or None,
        overwrite=overwrite,
    )
    return "\n".join([
        f"已生成 Skill: {result.name}",
        f"路径: {result.path}",
        "",
        result.content,
    ])


trace2skill_generate_tool = Tool(
    name="trace2skill_generate",
    description="从成功任务轨迹、复盘记录或工具调用日志中生成项目内可复用 Skill。",
    parameters={
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "成功任务的目标"},
            "trace": {"type": "string", "description": "成功执行轨迹、关键步骤、验证命令和踩坑记录"},
            "skill_name": {"type": "string", "description": "可选 Skill 名称，小写短横线"},
            "description": {"type": "string", "description": "可选触发描述"},
            "overwrite": {"type": "boolean", "description": "是否覆盖同名 Skill"},
        },
        "required": ["task", "trace"],
    },
    run=_trace2skill_generate,
)


trace2skill_tools = [trace2skill_generate_tool]
