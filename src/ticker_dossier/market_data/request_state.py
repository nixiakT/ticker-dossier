"""Per-request deadline and provenance state."""

from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class RequestState:
    deadline: float | None = None
    coverage: dict[str, dict[str, Any]] = field(default_factory=dict)


class RequestStateStore:
    """Lazily allocate one copy-on-write state value per execution context."""

    def __init__(self, name: str) -> None:
        self._current: ContextVar[RequestState | None] = ContextVar(name, default=None)

    def current(self) -> RequestState:
        state = self._current.get()
        if state is None:
            state = RequestState()
            self._current.set(state)
        return state

    def replace_coverage(self, coverage: dict[str, dict[str, Any]]) -> None:
        state = self.current()
        self._current.set(
            RequestState(
                deadline=state.deadline,
                coverage=deepcopy(coverage),
            )
        )

    def mutable_coverage_copy(self) -> dict[str, dict[str, Any]]:
        return deepcopy(self.current().coverage)

    @contextmanager
    def isolated(self, deadline: float) -> Iterator[None]:
        token = self._current.set(RequestState(deadline=deadline))
        try:
            yield
        finally:
            self._current.reset(token)
