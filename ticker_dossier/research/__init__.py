"""Finance stock research helpers for TickerDossier.

The public agent is loaded lazily so integration adapters can depend on the
research value models without importing the orchestration graph in reverse.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .agent import FinanceResearchAgent

__all__ = ["FinanceResearchAgent"]


def __getattr__(name: str) -> Any:
    if name != "FinanceResearchAgent":
        raise AttributeError(name)
    from .agent import FinanceResearchAgent

    return FinanceResearchAgent
