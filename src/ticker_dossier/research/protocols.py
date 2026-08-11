"""Narrow application ports consumed by the research layer."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, TypeAlias


class DebateBackend(Protocol):
    """Normalized chat contract required by the evidence debate workflow."""

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        """Return one normalized assistant message."""


class ManagedDebateBackend(DebateBackend, Protocol):
    """Debate backend whose lifecycle is owned by the factory caller."""

    def close(self) -> None:
        """Release backend-owned network resources."""


DebateBackendFactory: TypeAlias = Callable[[], ManagedDebateBackend | None]


__all__ = ["DebateBackend", "DebateBackendFactory", "ManagedDebateBackend"]
