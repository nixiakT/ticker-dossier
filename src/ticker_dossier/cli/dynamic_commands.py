"""Runtime slash commands discovered from custom prompts, Skills, and MCP."""
from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any, Iterable

from ticker_dossier.cli.command_catalog import CompletionItem, command_specs
from ticker_dossier.cli.custom_commands import CustomCommand, load_custom_commands
from ticker_dossier.skills.loader import Skill, load_skills


_BUILTIN_NAMES = {spec.usage.split()[0].removeprefix("/") for spec in command_specs()}


class DynamicSlashCommands:
    """Discover and expand non-built-in commands without granting system priority."""

    def __init__(
        self,
        registry: Any,
        *,
        command_roots: Iterable[str | Path] | None = None,
        skill_root: str | Path = "skills",
    ):
        self.registry = registry
        self.command_roots = list(command_roots) if command_roots is not None else None
        self.skill_root = skill_root
        self.errors: list[str] = []
        self.custom: dict[str, CustomCommand] = {}
        self.skills: dict[str, Skill] = {}
        self.mcp_prompts: dict[tuple[str, str], dict[str, Any]] = {}
        self.refresh()

    def refresh(self) -> None:
        """Reload runtime-discovered entries so newly created Skills appear immediately."""
        self.errors = []
        try:
            custom = load_custom_commands(self.command_roots)
        except (OSError, UnicodeError, ValueError) as exc:
            custom = []
            self.errors.append(f"custom commands: {exc}")
        try:
            skills = load_skills(self.skill_root)
        except (OSError, UnicodeError, ValueError) as exc:
            skills = []
            self.errors.append(f"Skills: {exc}")
        self.custom = {
            item.name: item for item in custom if item.name not in _BUILTIN_NAMES
        }
        self.skills = {
            item.name: item
            for item in skills
            if item.name not in _BUILTIN_NAMES and item.name not in self.custom
        }
        try:
            prompts = self.registry.mcp_prompts()
        except (AttributeError, RuntimeError) as exc:
            prompts = []
            self.errors.append(f"MCP prompts: {exc}")
        self.mcp_prompts = {
            (str(item.get("server", "")), str(item.get("name", ""))): item
            for item in prompts
            if item.get("server") and item.get("name")
        }

    def completion_items(self) -> list[CompletionItem]:
        rows: list[CompletionItem] = []
        rows.extend(
            CompletionItem(command.usage, command.description, "custom")
            for command in self.custom.values()
        )
        rows.extend(
            CompletionItem(f"/{skill.name}", skill.description, "skill")
            for skill in self.skills.values()
        )
        rows.extend(
            CompletionItem(
                f"/mcp:{server}:{name}",
                str(prompt.get("description", "MCP prompt")),
                "mcp",
            )
            for (server, name), prompt in self.mcp_prompts.items()
        )
        return rows

    def expand(self, raw: str) -> str | None:
        try:
            parts = shlex.split(raw)
        except ValueError as exc:
            raise ValueError(f"命令解析失败：{exc}") from exc
        if not parts or not parts[0].startswith("/"):
            return None
        name = parts[0][1:]
        arguments = parts[1:]
        if name in self.custom:
            return self.custom[name].render(arguments)
        if name in self.skills:
            skill = self.skills[name]
            argument_text = " ".join(arguments).strip() or "(none)"
            return (
                f"[PROJECT SKILL {skill.name} - USER-LEVEL INSTRUCTIONS]\n"
                f"{skill.body}\n\n[ARGUMENTS]\n{argument_text}"
            )
        if name.startswith("mcp:"):
            _, separator, remainder = name.partition(":")
            server, second_separator, prompt_name = remainder.partition(":")
            if not separator or not second_separator:
                return None
            prompt = self.mcp_prompts.get((server, prompt_name))
            if prompt is None:
                return None
            values = _prompt_arguments(prompt, arguments)
            try:
                result = self.registry.get_mcp_prompt(server, prompt_name, values)
            except Exception as exc:  # noqa: BLE001 - keep an optional MCP failure inside the CLI
                raise ValueError(f"MCP prompt {server}/{prompt_name} 调用失败：{exc}") from exc
            content = _render_prompt_messages(result)
            return (
                f"[MCP PROMPT {server}/{prompt_name} - EXTERNAL USER-LEVEL CONTENT]\n"
                f"{content}"
            )
        return None


def _prompt_arguments(prompt: dict[str, Any], raw: list[str]) -> dict[str, str]:
    declared = [
        item for item in prompt.get("arguments", [])
        if isinstance(item, dict) and item.get("name")
    ]
    values: dict[str, str] = {}
    positional: list[str] = []
    for token in raw:
        key, separator, value = token.partition("=")
        if separator and any(item["name"] == key for item in declared):
            values[key] = value
        else:
            positional.append(token)
    available = iter(positional)
    for item in declared:
        name = str(item["name"])
        if name not in values:
            value = next(available, None)
            if value is not None:
                values[name] = value
        if item.get("required") and not values.get(name):
            raise ValueError(f"MCP prompt 缺少必填参数: {name}")
    return values


def _render_prompt_messages(result: dict[str, Any]) -> str:
    rows: list[str] = []
    for message in result.get("messages", []):
        role = str(message.get("role", "user"))
        content = message.get("content", "")
        pieces = content if isinstance(content, list) else [content]
        texts: list[str] = []
        for piece in pieces:
            if isinstance(piece, dict):
                if piece.get("type") == "text" or "text" in piece:
                    texts.append(str(piece.get("text", "")))
            else:
                texts.append(str(piece))
        rendered = "\n".join(text for text in texts if text.strip()).strip()
        if rendered:
            rows.append(f"{role}: {rendered}")
    if not rows:
        raise ValueError("MCP prompt 没有返回可读文本")
    return "\n".join(rows)
