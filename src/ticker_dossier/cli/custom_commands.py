"""Load user-defined slash commands from small Markdown prompt templates."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


_SUBSTITUTION = re.compile(r"\$(\$|ARGUMENTS|[0-9]+)")


@dataclass(frozen=True)
class CustomCommand:
    name: str
    description: str
    argument_hint: str
    body: str
    source: Path

    @property
    def usage(self) -> str:
        suffix = f" {self.argument_hint.strip()}" if self.argument_hint.strip() else ""
        return f"/{self.name}{suffix}"

    def render(self, arguments: list[str]) -> str:
        def replace(match: re.Match[str]) -> str:
            token = match.group(1)
            if token == "$":
                return "$"
            if token == "ARGUMENTS":
                return " ".join(arguments)
            index = int(token) - 1
            return arguments[index] if 0 <= index < len(arguments) else ""

        return _SUBSTITUTION.sub(replace, self.body).strip()


def default_command_roots() -> list[Path]:
    """Project commands override optional per-user commands with the same name."""
    return [Path.home() / ".finance-agent" / "commands", Path(".finance_agent") / "commands"]


def load_custom_commands(roots: Iterable[str | Path] | None = None) -> list[CustomCommand]:
    by_name: dict[str, CustomCommand] = {}
    for raw_root in roots if roots is not None else default_command_roots():
        root = Path(raw_root).expanduser()
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.md")):
            if not path.is_file():
                continue
            command = _parse_command(root, path)
            by_name[command.name] = command
    return [by_name[name] for name in sorted(by_name)]


def _parse_command(root: Path, path: Path) -> CustomCommand:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n").lstrip("\ufeff")
    metadata, body = _split_frontmatter(text)
    relative = path.relative_to(root).with_suffix("")
    name = ":".join(relative.parts)
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9:_-]*", name):
        raise ValueError(f"invalid custom command name: {name}")
    if not body.strip():
        raise ValueError(f"custom command has no body: {path}")
    return CustomCommand(
        name=name,
        description=metadata.get("description", "custom prompt"),
        argument_hint=metadata.get("argument-hint", ""),
        body=body.strip(),
        source=path,
    )


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    marker = text.find("\n---\n", 4)
    if marker < 0:
        raise ValueError("custom command frontmatter is not closed")
    metadata: dict[str, str] = {}
    for line in text[4:marker].splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip():
            metadata[key.strip().lower()] = value.strip().strip("'\"")
    return metadata, text[marker + 5:]
