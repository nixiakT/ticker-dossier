"""Project Skill discovery and validation.

Skill 与 Tool 的区别：
  - Tool 是一次函数调用（read 一个文件）。
  - Skill 是一包"领域知识 + 操作流程 + 可选脚本/资源"，用一个 SKILL.md 描述，
    在合适的时候被加载进上下文，告诉模型"面对这类任务该怎么一步步做"。

SKILL.md 结构（约定）：
  ---
  name: pdf-report
  description: 一句话说明何时该用这个 skill（用于召回判断）
  ---
  正文：步骤、注意事项、可调用的脚本路径、示例。

The loader scans ``skills/*/SKILL.md``, validates frontmatter, and returns an
on-demand catalog without granting any additional permissions.
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path


SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class SkillError(ValueError):
    """Base exception for project Skill discovery and loading."""


class SkillFormatError(SkillError):
    """Raised when a SKILL.md file does not follow the project contract."""


class SkillNotFoundError(SkillError):
    """Raised when a requested Skill is not present in the catalog."""


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str
    path: Path


def parse_skill_md(text: str, path: Path) -> Skill:
    """Parse and validate one project ``SKILL.md`` file."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillFormatError(f"{path}: missing opening frontmatter delimiter '---'")
    try:
        frontmatter_end = next(
            index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"
        )
    except StopIteration as exc:
        raise SkillFormatError(f"{path}: frontmatter is not closed with '---'") from exc

    fields: dict[str, str] = {}
    for line in lines[1:frontmatter_end]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in line:
            raise SkillFormatError(f"{path}: invalid frontmatter line: {stripped}")
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip().strip('"').strip("'")

    name = fields.get("name", "")
    _validate_skill_name(name, path)
    body = "\n".join(lines[frontmatter_end + 1:]).strip()
    if not body:
        raise SkillFormatError(f"{path}: Skill body is empty")
    return Skill(
        name=name,
        description=fields.get("description", ""),
        body=body,
        path=path,
    )


def load_skills(root: str | Path = "skills") -> list[Skill]:
    """扫描 root 下所有 SKILL.md。"""
    skills: list[Skill] = []
    for md in sorted(Path(root).glob("*/SKILL.md"), key=lambda path: str(path)):
        try:
            text = md.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise SkillFormatError(f"{md}: unable to read Skill: {exc}") from exc
        skills.append(parse_skill_md(text, md))

    skills.sort(key=lambda skill: skill.name)
    for previous, current in zip(skills, skills[1:]):
        if previous.name == current.name:
            raise SkillFormatError(
                f"duplicate Skill name '{current.name}': {previous.path} and {current.path}"
            )
    return skills


def read_skill(name: str, root: str | Path = "skills") -> Skill:
    """Return one validated Skill by its declared frontmatter name."""
    _validate_skill_name(name)
    skills = load_skills(root)
    for skill in skills:
        if skill.name == name:
            return skill
    available = ", ".join(skill.name for skill in skills) or "none"
    raise SkillNotFoundError(f"unknown Skill '{name}'; available Skills: {available}")


def skills_catalog(skills: list[Skill]) -> str:
    """生成给模型看的可用 skill 清单（name + description），用于按需召回。"""
    return "\n".join(f"- {s.name}: {s.description}" for s in sorted(skills, key=lambda s: s.name))


def _validate_skill_name(name: str, path: Path | None = None) -> None:
    if SKILL_NAME_RE.fullmatch(name):
        return
    location = f"{path}: " if path else ""
    raise SkillFormatError(
        f"{location}invalid Skill name '{name}'; use lowercase letters, digits, and single hyphens"
    )
