"""Stable contracts shared by market-data providers and orchestration."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ticker_dossier.market_data.models import Candle, Financials, NewsItem, Quote


@runtime_checkable
class MarketDataProvider(Protocol):
    """Structural contract implemented by every market-data adapter."""

    name: str

    def get_quote(self, symbol: str) -> Quote:
        ...

    def get_history(
        self,
        symbol: str,
        period: str = "1y",
        interval: str = "1d",
    ) -> list[Candle]:
        ...

    def get_financials(self, symbol: str) -> Financials:
        ...

    def get_news(self, symbol: str, limit: int = 5) -> list[NewsItem]:
        ...


class ProviderError(RuntimeError):
    """A provider could not return a usable market-data result."""


class ProviderTimeoutError(ProviderError):
    """A provider exceeded the operation or snapshot deadline."""
