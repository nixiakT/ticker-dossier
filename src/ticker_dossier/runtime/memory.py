"""Persistent project memory for cross-session agent behavior."""
from __future__ import annotations

import json
import re
from pathlib import Path


DEFAULT_MEMORY_PATH = ".finance_agent/project_memory.md"
LEGACY_MEMORY_PATH = "MEMORY.md"
DEFAULT_KV_MEMORY_PATH = ".finance_agent/project_memory.json"
SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{8,}"),
)


class Memory:
    def __init__(self, path: str | Path = DEFAULT_MEMORY_PATH):
        self.path = Path(path)

    def write(self, note: str) -> Path:
        clean = sanitize_memory_note(note)
        if not clean:
            raise ValueError("memory note is empty")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        legacy = Path(LEGACY_MEMORY_PATH)
        if (
            self.path == Path(DEFAULT_MEMORY_PATH)
            and not self.path.exists()
            and legacy.exists()
        ):
            legacy_text = legacy.read_text(encoding="utf-8")
            if legacy_text.strip():
                self.path.write_text(legacy_text.rstrip() + "\n", encoding="utf-8")
        prefix = "" if self.path.exists() and self.path.read_text(encoding="utf-8").strip() else "# 项目记忆\n\n"
        with self.path.open("a", encoding="utf-8") as handle:
            if prefix:
                handle.write(prefix)
            handle.write("- " + clean + "\n")
        return self.path

    def recall(self, query: str = "") -> str:  # noqa: ARG002 - future filtered recall hook
        if self.path.exists():
            return self.path.read_text(encoding="utf-8")
        legacy = Path(LEGACY_MEMORY_PATH)
        if self.path == Path(DEFAULT_MEMORY_PATH) and legacy.exists():
            return legacy.read_text(encoding="utf-8")
        return ""


class KVMemory:
    def __init__(self, path: str | Path = DEFAULT_KV_MEMORY_PATH):
        self.path = Path(path)
        self.data = json.loads(self.path.read_text(encoding="utf-8") or "{}") if self.path.exists() else {}

    def remember(self, key: str, value: str) -> Path:
        clean_key = sanitize_memory_key(key)
        if not clean_key:
            raise ValueError("memory key is empty")
        self.data[clean_key] = sanitize_memory_note(value)
        return self._save()

    def forget(self, key: str) -> Path:
        self.data.pop(sanitize_memory_key(key), None)
        return self._save()

    def recall(self) -> str:
        return "\n".join(f"- {key}: {value}" for key, value in sorted(self.data.items()))

    def _save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return self.path


def sanitize_memory_note(note: str) -> str:
    clean = " ".join(str(note).strip().split())
    for pattern in SECRET_PATTERNS:
        clean = pattern.sub("[REDACTED_SECRET]", clean)
    return clean


def sanitize_memory_key(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.:-]+", "-", str(key).strip()).strip("-")


def recall_project_memory(path: str | Path = DEFAULT_MEMORY_PATH) -> str:
    return Memory(path).recall().strip()
