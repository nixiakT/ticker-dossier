"""Small structural protocols used by the core runtime.

The runtime depends on behavior instead of concrete LLM adapters.  Keeping the
protocol here makes that dependency executable for type checkers without
pulling provider implementations into the agent loop.
"""
from __future__ import annotations

from typing import Any, Protocol


class ModelBackend(Protocol):
    """Backend contract consumed by :class:`AgentLoop`."""

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Return one assistant message in the normalized runtime shape."""


__all__ = ["ModelBackend"]
