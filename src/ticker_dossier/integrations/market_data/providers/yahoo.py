"""Yahoo Finance public-endpoint adapter."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ticker_dossier.integrations.http import client as http_client, proxy_url
from ticker_dossier.research.models import Candle, Financials, NewsItem, Quote, utc_now_iso
from ticker_dossier.research.symbols import normalize_symbol, to_yahoo_symbol

from .._normalization import (
    _compact_provider_error,
    _financials_have_data,
    _list_float,
    _list_int,
    _news_keywords,
    _news_matches,
    _raw,
    _text_value,
    _to_float,
    _to_int,
    _unix_date,
)
from ..base import ProviderError


class YahooFinanceProvider:
    name = "Yahoo Finance public endpoints"
    news_is_symbol_scoped = True

    def __init__(self, timeout: float = 20.0):
        self.client = http_client(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Safari/537.36",
                "Accept": "application/json,text/plain,*/*",
            },
        )

    def close(self) -> None:
        self.client.close()

    def _chart(self, symbol: str, period: str, interval: str) -> dict[str, Any]:
        normalized = normalize_symbol(symbol)
        query_symbol = to_yahoo_symbol(normalized)
        response = self.client.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{query_symbol}",
            params={"range": period, "interval": interval, "includePrePost": "false"},
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ProviderError("invalid Yahoo chart response")
        chart = data.get("chart", {})
        if not isinstance(chart, dict):
            raise ProviderError("invalid Yahoo chart payload")
        if chart.get("error"):
            raise ProviderError(str(chart["error"]))
        result = (chart.get("result") or [None])[0]
        if not isinstance(result, dict) or not result:
            raise ProviderError("empty Yahoo chart")
        return result

    def get_quote(self, symbol: str) -> Quote:
        normalized = normalize_symbol(symbol)
        query_symbol = to_yahoo_symbol(normalized)
        result = self._chart(normalized, "1d", "1m")
        meta = result.get("meta", {})
        timestamp = result.get("timestamp") or []
        quote_rows = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        closes = [c for c in quote_rows.get("close", []) if c is not None]
        volumes = [v for v in quote_rows.get("volume", []) if v is not None]
        price = _to_float(meta.get("regularMarketPrice"))
        if price is None and closes:
            price = _to_float(closes[-1])
        previous = _to_float(meta.get("previousClose") or meta.get("chartPreviousClose"))
        change = price - previous if price is not None and previous not in (None, 0) else None
        change_percent = change / previous * 100 if change is not None and previous else None
        as_of = utc_now_iso()
        is_realtime = True
        if timestamp:
            as_of_dt = datetime.fromtimestamp(timestamp[-1], UTC).replace(microsecond=0)
            as_of = as_of_dt.isoformat().replace("+00:00", "Z")
            is_realtime = datetime.now(UTC) - as_of_dt <= timedelta(hours=36)
        notes = ["Yahoo public endpoints may be delayed or rate limited."]
        if query_symbol != normalized:
            notes.append(f"Yahoo 查询代码: {query_symbol}；展示代码按常见港股页面保留为 {normalized}。")
        if not is_realtime:
            notes.append("Latest Yahoo timestamp is older than 36 hours; treat it as delayed historical data.")
        return Quote(
            symbol=normalized,
            name=meta.get("longName") or meta.get("shortName") or "",
            currency=meta.get("currency", ""),
            price=price,
            previous_close=previous,
            change=change,
            change_percent=change_percent,
            volume=_to_int(meta.get("regularMarketVolume")) or (sum(_to_int(v) or 0 for v in volumes) if volumes else None),
            source=self.name,
            as_of=as_of,
            is_realtime=is_realtime,
            notes=notes,
        )

    def get_history(self, symbol: str, period: str = "1y", interval: str = "1d") -> list[Candle]:
        normalized = normalize_symbol(symbol)
        result = self._chart(normalized, period, interval)
        timestamps = result.get("timestamp") or []
        quote_rows = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        candles: list[Candle] = []
        for index, ts in enumerate(timestamps):
            candles.append(Candle(
                date=datetime.fromtimestamp(ts, UTC).date().isoformat(),
                open=_list_float(quote_rows.get("open"), index),
                high=_list_float(quote_rows.get("high"), index),
                low=_list_float(quote_rows.get("low"), index),
                close=_list_float(quote_rows.get("close"), index),
                volume=_list_int(quote_rows.get("volume"), index),
            ))
        return [c for c in candles if c.close is not None]

    def get_financials(self, symbol: str) -> Financials:
        normalized = normalize_symbol(symbol)
        query_symbol = to_yahoo_symbol(normalized)
        modules = "summaryDetail,defaultKeyStatistics,financialData"
        primary_error = ""
        try:
            response = self.client.get(
                f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{query_symbol}",
                params={"modules": modules},
            )
            response.raise_for_status()
            data = response.json()
            result = (((data.get("quoteSummary") or {}).get("result") or [None])[0]) or {}
            if not result:
                raise ProviderError("empty Yahoo quote summary")
        except Exception as exc:
            primary_error = _compact_provider_error(exc)
            try:
                result = self._yfinance_info(query_symbol)
            except Exception as fallback_exc:
                raise ProviderError(
                    f"Yahoo quoteSummary failed for {query_symbol}: {primary_error}; "
                    f"yfinance fallback failed: {_compact_provider_error(fallback_exc)}"
                ) from fallback_exc
        summary = result.get("summaryDetail") or result
        stats = result.get("defaultKeyStatistics") or result
        financial = result.get("financialData") or result
        most_recent_quarter = _raw(financial.get("mostRecentQuarter") or stats.get("mostRecentQuarter"))
        financials = Financials(
            symbol=normalized,
            source=self.name,
            as_of=_unix_date(most_recent_quarter),
            currency=_text_value(financial.get("financialCurrency") or summary.get("currency")),
            period_type="TTM",
            fetched_at=utc_now_iso(),
            market_cap=_raw(summary.get("marketCap") or stats.get("enterpriseValue")),
            pe_ratio=_raw(summary.get("trailingPE")),
            forward_pe=_raw(summary.get("forwardPE")),
            eps=_raw(stats.get("trailingEps")),
            revenue=_raw(financial.get("totalRevenue")),
            gross_profit=_raw(financial.get("grossProfits")),
            net_income=_raw(financial.get("netIncomeToCommon")),
            free_cash_flow=_raw(financial.get("freeCashflow")),
            debt_to_equity=_raw(financial.get("debtToEquity")),
            return_on_equity=_raw(financial.get("returnOnEquity")),
            profit_margin=_raw(summary.get("profitMargins")),
            notes=["Yahoo quote summary fields may be incomplete for some markets."],
        )
        if not _financials_have_data(financials):
            detail = (
                f" after quoteSummary failed: {primary_error}"
                if primary_error
                else ""
            )
            raise ProviderError(f"Yahoo fundamentals returned no supported fields{detail}")
        if primary_error:
            financials.notes.append(
                f"Yahoo quoteSummary failed ({primary_error}); fields came from yfinance fallback."
            )
        return financials

    def get_news(self, symbol: str, limit: int = 5) -> list[NewsItem]:
        normalized = normalize_symbol(symbol)
        query_symbol = to_yahoo_symbol(normalized)
        quote_name = ""
        try:
            quote_name = self.get_quote(normalized).name
        except Exception:
            quote_name = ""
        response = self.client.get(
            "https://query2.finance.yahoo.com/v1/finance/search",
            params={"q": query_symbol, "quotesCount": "1", "newsCount": str(max(limit * 4, 10))},
        )
        response.raise_for_status()
        data = response.json()
        items: list[NewsItem] = []
        keywords = _news_keywords(normalized, query_symbol, quote_name)
        for row in data.get("news") or []:
            if not _news_matches(row, keywords):
                continue
            published = row.get("providerPublishTime")
            items.append(NewsItem(
                title=row.get("title", ""),
                publisher=row.get("publisher", ""),
                link=row.get("link", ""),
                published_at=(
                    datetime.fromtimestamp(published, UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
                    if published else ""
                ),
                summary=row.get("summary", ""),
                source=self.name,
            ))
            if len(items) >= limit:
                break
        return items

    def _yfinance_info(self, query_symbol: str) -> dict[str, Any]:
        try:
            import yfinance as yf
        except ImportError as exc:
            raise ProviderError("yfinance package is not installed") from exc
        proxy = proxy_url()
        if proxy:
            configure = getattr(yf, "set_config", None)
            if callable(configure):
                try:
                    configure(proxy=proxy)
                except Exception as exc:
                    raise ProviderError(f"yfinance proxy configuration failed: {exc.__class__.__name__}") from exc
        try:
            info = yf.Ticker(query_symbol).info
        except Exception as exc:
            raise ProviderError(f"yfinance info request failed: {exc}") from exc
        if not isinstance(info, dict) or not info:
            raise ProviderError("yfinance info returned an empty payload")
        result = {
            "marketCap": info.get("marketCap") or info.get("enterpriseValue"),
            "trailingPE": info.get("trailingPE"),
            "forwardPE": info.get("forwardPE"),
            "trailingEps": info.get("trailingEps"),
            "totalRevenue": info.get("totalRevenue"),
            "grossProfits": info.get("grossProfits"),
            "netIncomeToCommon": info.get("netIncomeToCommon"),
            "freeCashflow": info.get("freeCashflow"),
            "debtToEquity": info.get("debtToEquity"),
            "returnOnEquity": info.get("returnOnEquity"),
            "profitMargins": info.get("profitMargins"),
            "financialCurrency": info.get("financialCurrency") or info.get("currency"),
            "currency": info.get("currency"),
            "mostRecentQuarter": info.get("mostRecentQuarter"),
        }
        if not any(value is not None and value != "" for value in result.values()):
            raise ProviderError("yfinance info returned no supported fundamental fields")
        return result
