"""Generate a reusable Skill from a successful trace."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class GeneratedSkill:
    name: str
    path: Path
    content: str


SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?[^'\"\s]+"),
]


def generate_skill(
    task: str,
    trace: str,
    skill_name: str | None = None,
    description: str | None = None,
    root: str = "skills",
    overwrite: bool = False,
) -> GeneratedSkill:
    clean_task = _sanitize(task.strip())
    clean_trace = _sanitize(trace.strip())
    name = _slugify(skill_name or _derive_name(clean_task))
    desc = description or _derive_description(clean_task)
    desc = _sanitize(desc.strip())
    path = Path(root) / name / "SKILL.md"
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} already exists; set overwrite=True to replace it")
    path.parent.mkdir(parents=True, exist_ok=True)
    content = _render_skill(name, desc, clean_task, clean_trace)
    path.write_text(content, encoding="utf-8")
    return GeneratedSkill(name=name, path=path, content=content)


def _render_skill(name: str, description: str, task: str, trace: str) -> str:
    steps, pitfalls, validation = _extract_sections(trace)
    return f"""---
name: {name}
description: {description}
---

# {name}

## 适用场景

当用户需要复用以下任务模式时使用：{task}

## 输入要求

- 明确任务目标和约束。
- 提供必要文件、代码位置、数据源或运行环境。
- 如涉及外部服务，只使用环境变量或本地忽略文件保存密钥。

## 操作步骤

{_bullet_block(steps)}

## 验证方法

{_bullet_block(validation)}

## 注意事项

{_bullet_block(pitfalls)}
"""


def _extract_sections(trace: str) -> tuple[list[str], list[str], list[str]]:
    lines = [line.strip(" -\t") for line in trace.splitlines() if line.strip()]
    steps: list[str] = []
    pitfalls: list[str] = []
    validation: list[str] = []
    for line in lines:
        lowered = line.lower()
        if any(word in lowered for word in ("error", "fail", "失败", "报错", "坑", "注意", "不要")):
            pitfalls.append(line)
        elif any(word in lowered for word in ("test", "verify", "验证", "自检", "检查", "运行")):
            validation.append(line)
        else:
            steps.append(line)
    if not steps:
        steps = ["先读取相关文件和上下文，确认现有模式。", "按最小可行变更实现复用流程。", "把结果写入项目约定位置。"]
    if not validation:
        validation = ["运行相关自检、测试或示例命令。", "检查输出是否满足任务目标。"]
    if not pitfalls:
        pitfalls = ["不要提交密钥、token、cookie 或一次性本地配置。", "不要把一次性路径或临时输出写成通用流程。"]
    return _dedupe(steps), _dedupe(pitfalls), _dedupe(validation)


def _bullet_block(items: list[str]) -> str:
    return "\n".join(f"{index}. {item}" for index, item in enumerate(items, start=1))


def _derive_name(task: str) -> str:
    ascii_words = re.findall(r"[A-Za-z0-9]+", task.lower())
    if ascii_words:
        return "-".join(ascii_words[:5])
    if "金融" in task or "股票" in task:
        return "finance-workflow"
    if "skill" in task.lower():
        return "skill-workflow"
    return "learned-workflow"


def _derive_description(task: str) -> str:
    return f"Use when Codex needs to repeat this learned workflow: {task[:160]}"


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)
    if not slug:
        slug = "learned-workflow"
    return slug[:63]


def _sanitize(text: str) -> str:
    clean = text
    for pattern in SECRET_PATTERNS:
        clean = pattern.sub("[REDACTED_SECRET]", clean)
    return clean


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            out.append(item)
            seen.add(item)
    return out[:12]
