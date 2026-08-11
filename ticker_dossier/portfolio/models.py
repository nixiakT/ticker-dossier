"""Data contracts shared by paper-portfolio storage and analysis."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


CURRENT_SCHEMA_VERSION = 2


@dataclass
class Holding:
    symbol: str
    shares: float
    avg_cost: float
    last_price: float
    market_value: float
    weight: float
    thesis: str = ""


@dataclass
class PortfolioAccount:
    name: str
    initial_cash: float
    cash: float
    holdings: list[Holding] = field(default_factory=list)
    transactions: list[dict[str, Any]] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    schema_version: int = CURRENT_SCHEMA_VERSION
    account_id: str = ""
    origin: str = ""
    storage_path: str = field(default="", repr=False, compare=False)
    storage_warnings: list[str] = field(default_factory=list, repr=False, compare=False)


@dataclass
class CandidateScore:
    symbol: str
    score: float
    target_weight: float
    price: float | None
    source: str
    thesis: str
    warnings: list[str] = field(default_factory=list)
    components: dict[str, float] = field(default_factory=dict)
    verdict: str = ""


@dataclass(frozen=True)
class AccountLocations:
    name: str
    user_path: Path
    workspace_path: Path
    user_exists: bool
    workspace_exists: bool

    @property
    def conflict(self) -> bool:
        return self.user_exists and self.workspace_exists and self.user_path != self.workspace_path

    @property
    def active_path(self) -> Path:
        if self.user_exists:
            return self.user_path
        if self.workspace_exists:
            return self.workspace_path
        return self.user_path


@dataclass(frozen=True)
class PortfolioMigration:
    name: str
    source: Path
    destination: Path
    recovery_backup: Path


@dataclass(frozen=True)
class PortfolioValuation:
    """A transient mark assembled from snapshots; never persisted by review."""

    account: PortfolioAccount
    as_of: str
    fresh_symbols: tuple[str, ...]
    stale_symbols: tuple[str, ...]
    price_as_of: dict[str, str]
    price_sources: dict[str, str]
