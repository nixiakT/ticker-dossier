"""Shared data models for the finance research agent."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class Candle:
    date: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: int | None = None


@dataclass
class Quote:
    symbol: str
    name: str = ""
    currency: str = ""
    price: float | None = None
    previous_close: float | None = None
    change: float | None = None
    change_percent: float | None = None
    volume: int | None = None
    market_cap: float | None = None
    pe_ratio: float | None = None
    eps: float | None = None
    source: str = ""
    as_of: str = ""
    is_realtime: bool = False
    source_spread_pct: float | None = None
    field_sources: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


@dataclass
class Financials:
    symbol: str
    source: str = ""
    as_of: str = ""
    currency: str = ""
    period_type: str = ""
    fetched_at: str = ""
    market_cap: float | None = None
    pe_ratio: float | None = None
    forward_pe: float | None = None
    eps: float | None = None
    revenue: float | None = None
    gross_profit: float | None = None
    net_income: float | None = None
    free_cash_flow: float | None = None
    debt_to_equity: float | None = None
    return_on_equity: float | None = None
    profit_margin: float | None = None
    field_sources: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


@dataclass
class NewsItem:
    title: str
    publisher: str = ""
    link: str = ""
    published_at: str = ""
    summary: str = ""
    source: str = ""


@dataclass
class StockSnapshot:
    symbol: str
    quote: Quote
    history: list[Candle]
    financials: Financials
    news: list[NewsItem]
    indicators: dict[str, Any]
    fetched_at: str
    source_coverage: dict[str, dict[str, Any]] = field(default_factory=dict)


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
