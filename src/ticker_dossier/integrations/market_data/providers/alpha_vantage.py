"""Alpha Vantage market-data adapter."""

from __future__ import annotations

import os
from typing import Any

from ticker_dossier.integrations.http import client as http_client
from ticker_dossier.research.models import Candle, Financials, NewsItem, Quote, utc_now_iso
from ticker_dossier.research.symbols import normalize_symbol

from .._normalization import _percent_to_float, _to_float, _to_int, _trim_period
from ..base import ProviderError


class AlphaVantageProvider:
    name = "Alpha Vantage"
    news_is_symbol_scoped = True

    def __init__(self, api_key: str | None = None, timeout: float = 20.0):
        self.api_key = api_key or os.environ.get("ALPHAVANTAGE_API_KEY", "")
        self.client = http_client(timeout=timeout, follow_redirects=True)

    def available(self) -> bool:
        return bool(self.api_key)

    def _get(self, params: dict[str, str]) -> dict[str, Any]:
        if not self.api_key:
            raise ProviderError("missing ALPHAVANTAGE_API_KEY")
        params = dict(params)
        params["apikey"] = self.api_key
        response = self.client.get("https://www.alphavantage.co/query", params=params)
        response.raise_for_status()
        data = response.json()
        if "Error Message" in data:
            raise ProviderError(data["Error Message"])
        if "Note" in data or "Information" in data:
            raise ProviderError(data.get("Note") or data.get("Information") or "rate limited")
        return data

    def get_quote(self, symbol: str) -> Quote:
        normalized = normalize_symbol(symbol)
        data = self._get({"function": "GLOBAL_QUOTE", "symbol": normalized})
        raw = data.get("Global Quote") or {}
        if not raw:
            raise ProviderError("empty Alpha Vantage quote")
        price = _to_float(raw.get("05. price"))
        previous = _to_float(raw.get("08. previous close"))
        change = _to_float(raw.get("09. change"))
        change_percent = _percent_to_float(raw.get("10. change percent"))
        return Quote(
            symbol=normalized,
            price=price,
            previous_close=previous,
            change=change,
            change_percent=change_percent,
            volume=_to_int(raw.get("06. volume")),
            source=self.name,
            as_of=raw.get("07. latest trading day") or "",
            is_realtime=False,
            notes=["Alpha Vantage GLOBAL_QUOTE provides a trading date, not an exchange timestamp; treat it as delayed/EOD."],
        )

    def get_history(self, symbol: str, period: str = "1y", interval: str = "1d") -> list[Candle]:
        normalized = normalize_symbol(symbol)
        output_size = "full" if period in {"2y", "5y", "max"} else "compact"
        data = self._get({
            "function": "TIME_SERIES_DAILY_ADJUSTED",
            "symbol": normalized,
            "outputsize": output_size,
        })
        series = data.get("Time Series (Daily)") or {}
        candles = [
            Candle(
                date=date,
                open=_to_float(row.get("1. open")),
                high=_to_float(row.get("2. high")),
                low=_to_float(row.get("3. low")),
                close=_to_float(row.get("4. close")),
                volume=_to_int(row.get("6. volume")),
            )
            for date, row in series.items()
        ]
        candles.sort(key=lambda c: c.date)
        return _trim_period(candles, period)

    def get_financials(self, symbol: str) -> Financials:
        normalized = normalize_symbol(symbol)
        data = self._get({"function": "OVERVIEW", "symbol": normalized})
        if not data or "Symbol" not in data:
            raise ProviderError("empty Alpha Vantage overview")
        debt_to_equity_ratio = _to_float(data.get("DebtToEquityRatio"))
        return Financials(
            symbol=normalized,
            source=self.name,
            as_of=str(data.get("LatestQuarter") or data.get("FiscalYearEnd") or ""),
            currency=str(data.get("Currency") or ""),
            period_type="TTM",
            fetched_at=utc_now_iso(),
            market_cap=_to_float(data.get("MarketCapitalization")),
            pe_ratio=_to_float(data.get("PERatio")),
            forward_pe=_to_float(data.get("ForwardPE")),
            eps=_to_float(data.get("EPS")),
            revenue=_to_float(data.get("RevenueTTM")),
            gross_profit=_to_float(data.get("GrossProfitTTM")),
            net_income=_to_float(data.get("NetIncomeTTM")),
            debt_to_equity=debt_to_equity_ratio * 100 if debt_to_equity_ratio is not None else None,
            return_on_equity=_to_float(data.get("ReturnOnEquityTTM")),
            profit_margin=_to_float(data.get("ProfitMargin")),
            notes=["Alpha Vantage OVERVIEW mixes current valuation with TTM fundamentals; report period is labeled separately from fetch time."],
        )

    def get_news(self, symbol: str, limit: int = 5) -> list[NewsItem]:
        normalized = normalize_symbol(symbol)
        data = self._get({"function": "NEWS_SENTIMENT", "tickers": normalized, "limit": str(limit)})
        items = []
        for row in (data.get("feed") or [])[:limit]:
            items.append(NewsItem(
                title=row.get("title", ""),
                publisher=row.get("source", ""),
                link=row.get("url", ""),
                published_at=row.get("time_published", ""),
                summary=row.get("summary", ""),
                source=self.name,
            ))
        return items
