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

import os
import re
from dataclasses import dataclass
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import TypeAlias

from ticker_dossier.resources import bundled_skills_root


SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SKILLS_DIR_ENV = "TICKER_DOSSIER_SKILLS_DIR"
SkillRoot: TypeAlias = str | Path | Traversable


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


def load_skills(root: SkillRoot | None = None) -> list[Skill]:
    """Load validated Skills from an explicit root or the default overlay.

    The default catalog always starts with the read-only Skills bundled in the
    distribution.  A project ``skills/`` directory (or
    ``TICKER_DOSSIER_SKILLS_DIR``) can add Skills and override a bundled Skill
    by declared name.  Passing ``root`` explicitly performs an isolated scan
    and never falls back to package data.
    """
    if root is not None:
        return _load_skill_root(_coerce_root(root))

    bundled = _load_skill_root(bundled_skills_root())
    project_root = _default_project_skill_root()
    if not project_root.is_dir():
        return bundled

    project = _load_skill_root(project_root)
    merged = {skill.name: skill for skill in bundled}
    merged.update({skill.name: skill for skill in project})
    return sorted(merged.values(), key=lambda skill: skill.name)


def _load_skill_root(root: Path | Traversable) -> list[Skill]:
    """Scan one root without applying the bundled/project overlay."""
    skills: list[Skill] = []
    if not root.is_dir():
        return skills
    entries = sorted(root.iterdir(), key=lambda item: item.name)
    for folder in entries:
        if not folder.is_dir():
            continue
        md = folder.joinpath("SKILL.md")
        if not md.is_file():
            continue
        path = _display_path(md)
        try:
            text = md.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise SkillFormatError(f"{path}: unable to read Skill: {exc}") from exc
        skills.append(parse_skill_md(text, path))

    skills.sort(key=lambda skill: skill.name)
    for previous, current in zip(skills, skills[1:]):
        if previous.name == current.name:
            raise SkillFormatError(
                f"duplicate Skill name '{current.name}': {previous.path} and {current.path}"
            )
    return skills


def read_skill(name: str, root: SkillRoot | None = None) -> Skill:
    """Return one validated Skill by its declared frontmatter name."""
    _validate_skill_name(name)
    skills = load_skills(root)
    for skill in skills:
        if skill.name == name:
            return skill
    available = ", ".join(skill.name for skill in skills) or "none"
    raise SkillNotFoundError(f"unknown Skill '{name}'; available Skills: {available}")


def _default_project_skill_root() -> Path:
    configured = os.environ.get(SKILLS_DIR_ENV, "").strip()
    return Path(configured).expanduser() if configured else Path.cwd() / "skills"


def _coerce_root(root: SkillRoot) -> Path | Traversable:
    return Path(root) if isinstance(root, (str, Path)) else root


def _display_path(resource: Traversable) -> Path:
    """Return a stable diagnostic path for filesystem-backed wheel resources."""
    return resource if isinstance(resource, Path) else Path(str(resource))


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
