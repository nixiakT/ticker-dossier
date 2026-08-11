"""Market data providers.

The provider chain tries real data sources first and falls back to clearly
marked sample data when the network/API is unavailable.
"""
from __future__ import annotations

import csv
from copy import deepcopy
from contextlib import contextmanager
import math
import os
import queue
import re
import threading
import time
from datetime import UTC, datetime, timedelta
from io import StringIO
from typing import Any, Protocol

import httpx

from ticker_dossier.config import load_local_env
from ticker_dossier.integrations.http import client as http_client, proxy_url
from .models import Candle, Financials, NewsItem, Quote, utc_now_iso
from .symbols import CHINESE_SYMBOLS, is_a_share, normalize_symbol, to_akshare_symbol, to_tushare_symbol, to_yahoo_symbol


class MarketDataProvider(Protocol):
    name: str

    def get_quote(self, symbol: str) -> Quote:
        ...

    def get_history(self, symbol: str, period: str = "1y", interval: str = "1d") -> list[Candle]:
        ...

    def get_financials(self, symbol: str) -> Financials:
        ...

    def get_news(self, symbol: str, limit: int = 5) -> list[NewsItem]:
        ...


class ProviderError(RuntimeError):
    pass


class ProviderTimeoutError(ProviderError):
    pass


_COVERAGE_LABELS = {
    "get_quote": "行情",
    "get_history": "历史价格",
    "get_financials": "基本面",
    "get_news": "新闻",
}

_FINANCIAL_FIELDS = (
    "market_cap",
    "pe_ratio",
    "forward_pe",
    "eps",
    "revenue",
    "gross_profit",
    "net_income",
    "free_cash_flow",
    "debt_to_equity",
    "return_on_equity",
    "profit_margin",
)

_FINANCIAL_MONETARY_FIELDS = {
    "market_cap", "eps", "revenue", "gross_profit", "net_income", "free_cash_flow",
}
_FINANCIAL_FLOW_FIELDS = {"eps", "revenue", "gross_profit", "net_income", "free_cash_flow"}
_FINANCIAL_PERIOD_FIELDS = _FINANCIAL_FLOW_FIELDS | {
    "debt_to_equity", "return_on_equity", "profit_margin",
}

_FINANCIAL_FIELD_LABELS = {
    "market_cap": "市值",
    "pe_ratio": "PE",
    "forward_pe": "Forward PE",
    "eps": "EPS",
    "revenue": "营收",
    "gross_profit": "毛利",
    "net_income": "净利润",
    "free_cash_flow": "自由现金流",
    "debt_to_equity": "杠杆",
    "return_on_equity": "ROE",
    "profit_margin": "利润率",
}

_QUOTE_PRIMARY_FIELDS = (
    "name",
    "currency",
    "price",
    "previous_close",
    "change",
    "change_percent",
    "volume",
    "market_cap",
    "pe_ratio",
    "eps",
)
_QUOTE_SUPPLEMENT_FIELDS = ("name", "currency", "market_cap", "pe_ratio", "eps")
_QUOTE_FIELD_LABELS = {
    "name": "名称",
    "currency": "币种",
    "market_cap": "市值",
    "pe_ratio": "PE",
    "eps": "EPS",
}


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

    def _chart(self, symbol: str, period: str, interval: str) -> dict[str, Any]:
        normalized = normalize_symbol(symbol)
        query_symbol = to_yahoo_symbol(normalized)
        response = self.client.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{query_symbol}",
            params={"range": period, "interval": interval, "includePrePost": "false"},
        )
        response.raise_for_status()
        data = response.json()
        chart = data.get("chart", {})
        if chart.get("error"):
            raise ProviderError(str(chart["error"]))
        result = (chart.get("result") or [None])[0]
        if not result:
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
            import yfinance as yf  # type: ignore
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


class TushareProvider:
    name = "Tushare Pro"

    def __init__(self, token: str | None = None, timeout: float = 20.0):
        self.token = token or os.environ.get("TUSHARE_TOKEN", "")
        self.timeout = timeout
        self._pro = None

    def available(self) -> bool:
        return bool(self.token)

    def supports(self, method: str, symbol: str, *args: Any) -> bool:
        return method != "get_news" and is_a_share(symbol)

    def _client(self) -> Any:
        if not self.token:
            raise ProviderError("missing TUSHARE_TOKEN")
        if self._pro is None:
            try:
                import tushare as ts  # type: ignore
            except ImportError as exc:
                raise ProviderError("tushare package is not installed") from exc
            self._pro = ts.pro_api(self.token, timeout=self.timeout)
        return self._pro

    def _require_a_share(self, symbol: str) -> tuple[str, str]:
        normalized = normalize_symbol(symbol)
        if not is_a_share(normalized):
            raise ProviderError("TushareProvider supports A-share symbols only")
        return normalized, to_tushare_symbol(normalized)

    def get_quote(self, symbol: str) -> Quote:
        normalized, ts_code = self._require_a_share(symbol)
        pro = self._client()
        start_date, end_date = _date_window("1mo")
        daily = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if daily is None or daily.empty:
            raise ProviderError("empty Tushare daily quote")
        daily = daily.sort_values("trade_date")
        latest = daily.iloc[-1]
        previous = daily.iloc[-2] if len(daily) > 1 else None
        basics, basics_error = self._daily_basic(pro, ts_code)
        name = self._stock_name(pro, ts_code)
        price = _to_float(latest.get("close"))
        previous_close = _to_float(previous.get("close")) if previous is not None else _to_float(latest.get("pre_close"))
        change = price - previous_close if price is not None and previous_close not in (None, 0) else _to_float(latest.get("change"))
        change_percent = (
            change / previous_close * 100
            if change is not None and previous_close
            else _to_float(latest.get("pct_chg"))
        )
        trade_date = str(latest.get("trade_date", ""))
        notes = ["Tushare daily data is end-of-day data; volume was converted from lots (100 shares) to shares."]
        if basics_error:
            notes.append(f"Tushare daily_basic enrichment unavailable: {basics_error}")
        return Quote(
            symbol=normalized,
            name=name,
            currency="CNY",
            price=price,
            previous_close=previous_close,
            change=change,
            change_percent=change_percent,
            volume=_lots_to_shares(latest.get("vol")),
            market_cap=_to_float(basics.get("total_mv")) * 10_000 if basics.get("total_mv") is not None else None,
            pe_ratio=_to_float(basics.get("pe_ttm") or basics.get("pe")),
            source=self.name,
            as_of=_format_trade_date(trade_date),
            is_realtime=False,
            notes=notes,
        )

    def get_history(self, symbol: str, period: str = "1y", interval: str = "1d") -> list[Candle]:
        normalized, ts_code = self._require_a_share(symbol)
        pro = self._client()
        start_date, end_date = _date_window(period)
        data = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if data is None or data.empty:
            raise ProviderError("empty Tushare history")
        data = data.sort_values("trade_date")
        candles: list[Candle] = []
        for _, row in data.iterrows():
            candles.append(Candle(
                date=_format_trade_date(str(row.get("trade_date", ""))),
                open=_to_float(row.get("open")),
                high=_to_float(row.get("high")),
                low=_to_float(row.get("low")),
                close=_to_float(row.get("close")),
                volume=_lots_to_shares(row.get("vol")),
            ))
        return candles

    def get_financials(self, symbol: str) -> Financials:
        normalized, ts_code = self._require_a_share(symbol)
        pro = self._client()
        basics, basics_error = self._daily_basic(pro, ts_code)
        income, cashflow, fina_indicator, report_date = self._aligned_reports(pro, ts_code)
        revenue = _to_float(income.get("total_revenue") or income.get("revenue"))
        net_income = _to_float(income.get("n_income_attr_p") or income.get("net_profit"))
        return_on_equity = _to_float(fina_indicator.get("roe"))
        explicit_fcf = _to_float(cashflow.get("free_cashflow"))
        operating_cash_flow = _to_float(cashflow.get("n_cashflow_act"))
        capital_expenditure = _to_float(cashflow.get("c_pay_acq_const_fiolta"))
        free_cash_flow = explicit_fcf
        if free_cash_flow is None and operating_cash_flow is not None and capital_expenditure is not None:
            free_cash_flow = operating_cash_flow - capital_expenditure
        return Financials(
            symbol=normalized,
            source=self.name,
            as_of=report_date,
            currency="CNY",
            period_type="REPORTED",
            fetched_at=utc_now_iso(),
            market_cap=_to_float(basics.get("total_mv")) * 10_000 if basics.get("total_mv") is not None else None,
            pe_ratio=_to_float(basics.get("pe_ttm") or basics.get("pe")),
            eps=_to_float(fina_indicator.get("eps")),
            revenue=revenue,
            gross_profit=_to_float(income.get("grossprofit")),
            net_income=net_income,
            free_cash_flow=free_cash_flow,
            debt_to_equity=_debt_to_equity_from_debt_to_assets(fina_indicator.get("debt_to_assets")),
            return_on_equity=return_on_equity / 100 if return_on_equity is not None else None,
            profit_margin=(net_income / revenue if net_income is not None and revenue else None),
            notes=[
                "Tushare fundamentals depend on token permission and reporting availability.",
                "Tushare ROE 已从百分数归一化为比率；资产负债率已换算为债务/权益百分比。",
                "自由现金流仅使用明确 FCF，或由经营现金流减资本开支计算；缺少资本开支时保留为空。",
            ] + ([f"Tushare daily_basic enrichment unavailable: {basics_error}"] if basics_error else []),
        )

    def get_news(self, symbol: str, limit: int = 5) -> list[NewsItem]:
        raise ProviderError("Tushare news is not enabled in this provider")

    def _daily_basic(self, pro: Any, ts_code: str) -> tuple[dict[str, Any], str]:
        try:
            start_date, end_date = _date_window("1mo")
            data = pro.daily_basic(ts_code=ts_code, start_date=start_date, end_date=end_date)
            if data is not None and not data.empty:
                return data.sort_values("trade_date").iloc[-1].to_dict(), ""
        except Exception as exc:
            return {}, _compact_provider_error(exc)
        return {}, "empty result"

    def _stock_name(self, pro: Any, ts_code: str) -> str:
        try:
            data = pro.stock_basic(fields="ts_code,name")
            if data is not None and not data.empty:
                row = data.loc[data["ts_code"].astype(str) == ts_code]
                if not row.empty:
                    return str(row.iloc[0].get("name", ""))
        except Exception:
            return ""
        return ""

    def _aligned_reports(
        self,
        pro: Any,
        ts_code: str,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
        tables: list[dict[str, dict[str, Any]]] = []
        for method in ("income", "cashflow", "fina_indicator"):
            rows: dict[str, dict[str, Any]] = {}
            try:
                data = getattr(pro, method)(ts_code=ts_code)
                if data is not None and not data.empty:
                    date_column = "end_date" if "end_date" in data.columns else data.columns[0]
                    for _, row in data.iterrows():
                        values = row.to_dict()
                        date = _format_trade_date(str(values.get(date_column, "")))
                        if date:
                            rows[date] = values
            except Exception:
                pass
            tables.append(rows)
        dates = set().union(*(table.keys() for table in tables))
        if not dates:
            return {}, {}, {}, ""
        report_date = max(dates, key=lambda date: (sum(date in table for table in tables), date))
        income, cashflow, indicator = (table.get(report_date, {}) for table in tables)
        return income, cashflow, indicator, report_date


class AKShareProvider:
    name = "AKShare"
    news_is_symbol_scoped = True

    def __init__(self, timeout: float = 20.0):
        self.timeout = timeout
        self._ak = None

    def supports(self, method: str, symbol: str, *args: Any) -> bool:
        normalized = normalize_symbol(symbol)
        if method == "get_financials":
            return is_a_share(normalized) or normalized.endswith(".HK") or "." not in normalized
        return is_a_share(normalized)

    def _client(self) -> Any:
        if self._ak is None:
            try:
                import akshare as ak  # type: ignore
            except ImportError as exc:
                raise ProviderError("akshare package is not installed") from exc
            self._ak = ak
        return self._ak

    def _require_a_share(self, symbol: str) -> tuple[str, str]:
        normalized = normalize_symbol(symbol)
        if not is_a_share(normalized):
            raise ProviderError("AKShareProvider supports A-share symbols only")
        return normalized, to_akshare_symbol(normalized)

    def get_quote(self, symbol: str) -> Quote:
        normalized, code = self._require_a_share(symbol)
        ak = self._client()
        data = ak.stock_zh_a_spot_em()
        if data is None or data.empty:
            raise ProviderError("empty AKShare spot data")
        row = data.loc[data["代码"].astype(str) == code]
        if row.empty:
            raise ProviderError(f"{code} not found in AKShare spot data")
        item = row.iloc[0].to_dict()
        return Quote(
            symbol=normalized,
            name=str(item.get("名称", "")),
            currency="CNY",
            price=_to_float(item.get("最新价")),
            previous_close=None,
            change=_to_float(item.get("涨跌额")),
            change_percent=_to_float(item.get("涨跌幅")),
            volume=_lots_to_shares(item.get("成交量")),
            market_cap=_to_float(item.get("总市值")),
            pe_ratio=_to_float(item.get("市盈率-动态") or item.get("市盈率")),
            source=self.name,
            as_of="",
            is_realtime=False,
            notes=[
                "AKShare/Eastmoney spot data exposes no exchange timestamp here; fetch time is not used as market time.",
                "A-share volume was converted from lots (100 shares) to shares.",
            ],
        )

    def get_history(self, symbol: str, period: str = "1y", interval: str = "1d") -> list[Candle]:
        normalized, code = self._require_a_share(symbol)
        ak = self._client()
        start_date, end_date = _date_window(period)
        data = ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust="",
            timeout=self.timeout,
        )
        if data is None or data.empty:
            raise ProviderError("empty AKShare history")
        candles: list[Candle] = []
        for _, row in data.iterrows():
            candles.append(Candle(
                date=str(row.get("日期", "")),
                open=_to_float(row.get("开盘")),
                high=_to_float(row.get("最高")),
                low=_to_float(row.get("最低")),
                close=_to_float(row.get("收盘")),
                volume=_lots_to_shares(row.get("成交量")),
            ))
        return candles

    def get_financials(self, symbol: str) -> Financials:
        normalized = normalize_symbol(symbol)
        ak = self._client()
        if is_a_share(normalized):
            query_symbol = to_tushare_symbol(normalized)
            data = ak.stock_financial_analysis_indicator_em(symbol=query_symbol, indicator="按报告期")
            fields = {
                "eps": "EPSJB",
                "revenue": "TOTALOPERATEREVE",
                "gross_profit": "MLR",
                "net_income": "PARENTNETPROFIT",
                "free_cash_flow": "FCFF_BACK",
                "debt_to_equity": "CQBL",
                "debt_to_assets": "ZCFZL",
                "return_on_equity": "ROEJQ",
                "profit_margin": "XSJLL",
            }
        elif normalized.endswith(".HK"):
            query_symbol = normalized[:-3].zfill(5)
            data = ak.stock_financial_hk_analysis_indicator_em(symbol=query_symbol, indicator="年度")
            fields = {
                "eps": "BASIC_EPS",
                "revenue": "OPERATE_INCOME",
                "gross_profit": "GROSS_PROFIT",
                "net_income": "HOLDER_PROFIT",
                "debt_to_assets": "DEBT_ASSET_RATIO",
                "return_on_equity": "ROE_AVG",
                "profit_margin": "NET_PROFIT_RATIO",
            }
        elif "." not in normalized:
            query_symbol = normalized
            data = ak.stock_financial_us_analysis_indicator_em(symbol=query_symbol, indicator="年报")
            fields = {
                "eps": ("BASIC_EPS", "BASIC_EPS_CS"),
                "revenue": ("OPERATE_INCOME", "TOTAL_INCOME"),
                "gross_profit": "GROSS_PROFIT",
                "net_income": "PARENT_HOLDER_NETPROFIT",
                "debt_to_assets": ("DEBT_ASSET_RATIO", "DEBT_RATIO"),
                "return_on_equity": ("ROE_AVG", "ROE"),
                "profit_margin": "NET_PROFIT_RATIO",
            }
        else:
            raise ProviderError(f"AKShare financial indicators do not support {normalized}")

        if data is None or data.empty:
            raise ProviderError(f"empty AKShare financial indicators for {query_symbol}")
        if "REPORT_DATE" in data.columns:
            data = data.sort_values("REPORT_DATE", ascending=False)
        row = data.iloc[0].to_dict()
        report_date = str(row.get("REPORT_DATE") or "").split(" ", 1)[0]
        return_on_equity = _to_float(_field_value(row, fields["return_on_equity"]))
        profit_margin = _to_float(_field_value(row, fields["profit_margin"]))
        debt_to_equity = _to_float(_field_value(row, fields.get("debt_to_equity", "")))
        if debt_to_equity is None:
            debt_to_equity = _debt_to_equity_from_debt_to_assets(
                _field_value(row, fields.get("debt_to_assets", ""))
            )
        return Financials(
            symbol=normalized,
            source=self.name,
            as_of=report_date,
            currency=str(row.get("CURRENCY") or ("CNY" if is_a_share(normalized) else "")),
            period_type="REPORTED" if is_a_share(normalized) else "ANNUAL",
            fetched_at=utc_now_iso(),
            eps=_to_float(_field_value(row, fields["eps"])),
            revenue=_to_float(_field_value(row, fields["revenue"])),
            gross_profit=_to_float(_field_value(row, fields["gross_profit"])),
            net_income=_to_float(_field_value(row, fields["net_income"])),
            free_cash_flow=_to_float(_field_value(row, fields.get("free_cash_flow", ""))),
            debt_to_equity=debt_to_equity,
            return_on_equity=return_on_equity / 100 if return_on_equity is not None else None,
            profit_margin=profit_margin / 100 if profit_margin is not None else None,
            notes=[
                f"AKShare 真实财务指标，报告期 {report_date or '未知'}；估值字段需由行情源补充。",
                "杠杆统一为债务/权益百分比；负权益情形标记为极高风险。",
            ],
        )

    def get_news(self, symbol: str, limit: int = 5) -> list[NewsItem]:
        normalized, code = self._require_a_share(symbol)
        ak = self._client()
        if not hasattr(ak, "stock_news_em"):
            raise ProviderError("AKShare stock_news_em is unavailable")
        data = ak.stock_news_em(symbol=code)
        if data is None or data.empty:
            raise ProviderError("empty AKShare news")
        items: list[NewsItem] = []
        for _, row in data.head(limit).iterrows():
            items.append(NewsItem(
                title=str(row.get("新闻标题") or row.get("标题") or ""),
                publisher=str(row.get("文章来源") or row.get("来源") or "Eastmoney"),
                link=str(row.get("新闻链接") or row.get("链接") or ""),
                published_at=str(row.get("发布时间") or row.get("时间") or ""),
                source=self.name,
            ))
        return items


class SampleDataProvider:
    name = "SAMPLE_FALLBACK"

    def get_quote(self, symbol: str) -> Quote:
        normalized = normalize_symbol(symbol)
        profile = _sample_profile(normalized)
        history = self.get_history(normalized, "1y", "1d")
        last = history[-1].close if history else profile["price"]
        prev = history[-2].close if len(history) > 1 else profile["price"] * 0.99
        change = last - prev if last is not None and prev is not None else None
        change_percent = change / prev * 100 if change is not None and prev else None
        return Quote(
            symbol=normalized,
            name=profile["name"],
            currency=profile["currency"],
            price=last,
            previous_close=prev,
            change=change,
            change_percent=change_percent,
            volume=profile["volume"],
            market_cap=profile["market_cap"],
            pe_ratio=profile["pe_ratio"],
            eps=profile["eps"],
            source=self.name,
            as_of=utc_now_iso(),
            is_realtime=False,
            notes=["样例 fallback 数据，仅用于离线演示；请勿当作真实行情。"],
        )

    def get_history(self, symbol: str, period: str = "1y", interval: str = "1d") -> list[Candle]:
        normalized = normalize_symbol(symbol)
        profile = _sample_profile(normalized)
        days = _period_to_days(period)
        base_price = profile["price"]
        trend = profile["trend"]
        volatility = profile["volatility"]
        candles: list[Candle] = []
        start = datetime.now(UTC).date() - timedelta(days=days * 7 // 5 + 10)
        trading_day = 0
        current = start
        while len(candles) < days:
            current += timedelta(days=1)
            if current.weekday() >= 5:
                continue
            progress = trading_day / max(days - 1, 1)
            seasonal = math.sin(trading_day / 9.0) * volatility + math.cos(trading_day / 23.0) * volatility * 0.6
            close = base_price * (1 + trend * (progress - 1) + seasonal)
            open_price = close * (1 - math.sin(trading_day / 5.0) * volatility * 0.25)
            high = max(open_price, close) * (1 + volatility * 0.4)
            low = min(open_price, close) * (1 - volatility * 0.4)
            candles.append(Candle(
                date=current.isoformat(),
                open=round(open_price, 2),
                high=round(high, 2),
                low=round(low, 2),
                close=round(close, 2),
                volume=int(profile["volume"] * (0.7 + 0.3 * (1 + math.sin(trading_day / 11.0)))),
            ))
            trading_day += 1
        return candles

    def get_financials(self, symbol: str) -> Financials:
        normalized = normalize_symbol(symbol)
        profile = _sample_profile(normalized)
        return Financials(
            symbol=normalized,
            source=self.name,
            as_of=utc_now_iso(),
            currency=str(profile.get("currency") or ""),
            period_type="sample",
            fetched_at=utc_now_iso(),
            market_cap=profile["market_cap"],
            pe_ratio=profile["pe_ratio"],
            forward_pe=profile["forward_pe"],
            eps=profile["eps"],
            revenue=profile["revenue"],
            gross_profit=profile["gross_profit"],
            net_income=profile["net_income"],
            free_cash_flow=profile["free_cash_flow"],
            debt_to_equity=profile["debt_to_equity"],
            return_on_equity=profile["return_on_equity"],
            profit_margin=profile["profit_margin"],
            notes=["样例 fallback 数据，仅用于离线演示；请接入真实数据源后再做研究。"],
        )

    def get_news(self, symbol: str, limit: int = 5) -> list[NewsItem]:
        normalized = normalize_symbol(symbol)
        profile = _sample_profile(normalized)
        rows = [
            f"{profile['name']} 发布最新经营数据，市场关注收入增长和利润率变化",
            f"分析师讨论 {profile['name']} 的估值水平与行业竞争格局",
            f"宏观利率和风险偏好变化可能影响 {profile['name']} 的估值倍数",
        ]
        return [
            NewsItem(
                title=row,
                publisher="Sample News",
                published_at=utc_now_iso(),
                source=self.name,
                summary="样例新闻，仅用于离线演示。",
            )
            for row in rows[:limit]
        ]


class ProviderChain:
    def __init__(self, providers: list[MarketDataProvider] | None = None):
        load_local_env()
        configured_sample_fallback = _env_truthy("FINANCE_ALLOW_SAMPLE_FALLBACK", default=False)
        self.allow_sample_fallback = configured_sample_fallback or bool(
            providers and any(_is_sample_provider(provider) for provider in providers)
        )
        default: list[MarketDataProvider] = []
        alpha = AlphaVantageProvider()
        tushare = TushareProvider()
        self._default_diagnostics = [
            {
                "name": alpha.name,
                "status": "enabled" if alpha.available() else "disabled",
                "detail": "" if alpha.available() else "requires ALPHAVANTAGE_API_KEY",
            },
            {
                "name": tushare.name,
                "status": "enabled" if tushare.available() else "disabled",
                "detail": "" if tushare.available() else "requires TUSHARE_TOKEN",
            },
            {"name": "AKShare", "status": "enabled", "detail": "A/HK/US public financial indicators; A-share quote/history/news"},
            {"name": "Yahoo Finance public endpoints", "status": "enabled", "detail": "public endpoints may be delayed"},
            {
                "name": "SAMPLE_FALLBACK",
                "status": "enabled" if configured_sample_fallback else "disabled",
                "detail": "demo-only fallback" if configured_sample_fallback else "FINANCE_ALLOW_SAMPLE_FALLBACK=0",
            },
        ]
        if alpha.available():
            default.append(alpha)
        if tushare.available():
            default.append(tushare)
        default.extend([AKShareProvider(), YahooFinanceProvider()])
        if configured_sample_fallback:
            default.append(SampleDataProvider())
        self._using_default_providers = providers is None
        self.providers = default if providers is None else providers
        self.provider_timeout = _positive_env_float("FINANCE_PROVIDER_TIMEOUT_SECONDS", 25.0)
        self.snapshot_timeout = _positive_env_float("FINANCE_SNAPSHOT_TIMEOUT_SECONDS", 45.0)
        self.provider_cooldown = _positive_env_float("FINANCE_PROVIDER_COOLDOWN_SECONDS", 60.0)
        self._provider_circuit_until: dict[int, float] = {}
        self._provider_inflight: set[int] = set()
        self._provider_inflight_lock = threading.Lock()
        self._request_deadline: float | None = None
        self._coverage: dict[str, dict[str, Any]] = {}
        self._cache_ttls = {
            "get_quote": _nonnegative_env_float("FINANCE_QUOTE_CACHE_TTL_SECONDS", 60.0),
            "get_history": _nonnegative_env_float("FINANCE_HISTORY_CACHE_TTL_SECONDS", 900.0),
            "get_financials": _nonnegative_env_float("FINANCE_FINANCIALS_CACHE_TTL_SECONDS", 21_600.0),
            "get_news": _nonnegative_env_float("FINANCE_NEWS_CACHE_TTL_SECONDS", 600.0),
        }
        self._cache: dict[tuple[Any, ...], tuple[float, Any, dict[str, Any]]] = {}
        self._cache_lock = threading.Lock()

    def get_quote(self, symbol: str) -> Quote:
        cache_key = (normalize_symbol(symbol),)
        cached = self._cache_get("get_quote", cache_key)
        if cached is not None:
            return cached
        operation_timeout = self._operation_timeout_or_raise("get_quote")
        successful: list[tuple[str, Quote]] = []
        failures: list[dict[str, str]] = []
        sample_providers: list[MarketDataProvider] = []
        real_providers: list[MarketDataProvider] = []
        for provider in self.providers:
            if not _provider_supports(provider, "get_quote", symbol):
                continue
            if _is_sample_provider(provider):
                sample_providers.append(provider)
                continue
            if blocked := self._circuit_error(provider):
                failures.append({"name": provider.name, "error": blocked})
                continue
            real_providers.append(provider)
        for provider, ok, value in _collect_provider_calls(
            real_providers, "get_quote", (symbol,), operation_timeout,
            self._provider_inflight, self._provider_inflight_lock,
        ):
            try:
                if not ok:
                    if isinstance(value, ProviderTimeoutError):
                        self._trip_circuit(provider)
                    raise value
                quote = value
                if quote.price is None:
                    raise ProviderError("无可用价格字段")
                successful.append((provider.name, quote))
            except Exception as exc:  # noqa: BLE001 - provider errors are reported as data coverage
                failures.append({"name": provider.name, "error": _compact_provider_error(exc)})

        if successful:
            selected_name, selected = max(
                successful,
                key=lambda row: (row[1].is_realtime, row[1].as_of or ""),
            )
            selected = deepcopy(selected)
            selected.field_sources = {
                field_name: selected.field_sources.get(field_name, selected_name)
                for field_name in _QUOTE_PRIMARY_FIELDS
                if _quote_value_present(getattr(selected, field_name))
            }
            supplemental_fields: list[str] = []
            for provider_name, candidate in successful:
                if provider_name == selected_name:
                    continue
                for field_name in _QUOTE_SUPPLEMENT_FIELDS:
                    if _quote_value_present(getattr(selected, field_name)):
                        continue
                    value = getattr(candidate, field_name)
                    if not _quote_value_present(value):
                        continue
                    if not _quote_field_compatible(selected, candidate, field_name):
                        continue
                    setattr(selected, field_name, value)
                    selected.field_sources[field_name] = candidate.field_sources.get(field_name, provider_name)
                    supplemental_fields.append(field_name)
            if supplemental_fields:
                detail = "、".join(
                    f"{_QUOTE_FIELD_LABELS[field_name]}={selected.field_sources[field_name]}"
                    for field_name in dict.fromkeys(supplemental_fields)
                )
                selected.notes.append(f"行情缺失字段由其他真实来源补充: {detail}。")
            spread = _quote_price_spread(successful)
            selected.source_spread_pct = spread
            self._record_coverage(
                "get_quote",
                successful_real_sources=[name for name, _ in successful],
                failed_real_sources=failures,
                selected_source=selected.source or selected_name,
                sample_used=False,
                price_spread_pct=spread,
                field_sources=selected.field_sources,
            )
            _extend_unique(selected.notes, self.report_notes("get_quote"))
            self._cache_set("get_quote", cache_key, selected)
            return selected

        sample_failures: list[str] = []
        if self.allow_sample_fallback:
            for provider in sample_providers:
                try:
                    quote = provider.get_quote(symbol)
                    if quote.price is None:
                        raise ProviderError("无可用价格字段")
                    self._record_coverage(
                        "get_quote",
                        successful_real_sources=[],
                        failed_real_sources=failures,
                        selected_source=quote.source or provider.name,
                        sample_used=True,
                        price_spread_pct=None,
                    )
                    _extend_unique(quote.notes, self.report_notes("get_quote"))
                    return quote
                except Exception as exc:  # noqa: BLE001 - report sample failure with real failures
                    sample_failures.append(f"{provider.name}: {_compact_provider_error(exc)}")

        self._record_coverage(
            "get_quote",
            successful_real_sources=[],
            failed_real_sources=failures,
            selected_source="",
            sample_used=False,
            price_spread_pct=None,
        )
        raise ProviderError(_coverage_error(failures, sample_failures, "get_quote"))

    def get_history(self, symbol: str, period: str = "1y", interval: str = "1d") -> list[Candle]:
        cache_key = (normalize_symbol(symbol), period, interval)
        cached = self._cache_get("get_history", cache_key)
        if cached is not None:
            return cached
        operation_timeout = self._operation_timeout_or_raise("get_history")
        successful: list[tuple[str, list[Candle]]] = []
        failures: list[dict[str, str]] = []
        sample_providers: list[MarketDataProvider] = []
        real_providers: list[MarketDataProvider] = []
        for provider in self.providers:
            if not _provider_supports(provider, "get_history", symbol, period, interval):
                continue
            if _is_sample_provider(provider):
                sample_providers.append(provider)
                continue
            if blocked := self._circuit_error(provider):
                failures.append({"name": provider.name, "error": blocked})
                continue
            real_providers.append(provider)
        for provider, ok, value in _collect_provider_calls(
            real_providers, "get_history", (symbol, period, interval), operation_timeout,
            self._provider_inflight, self._provider_inflight_lock,
        ):
            try:
                if not ok:
                    if isinstance(value, ProviderTimeoutError):
                        self._trip_circuit(provider)
                    raise value
                candles = value
                if not candles:
                    raise ProviderError("empty result")
                successful.append((provider.name, candles))
            except Exception as exc:  # noqa: BLE001
                failures.append({"name": provider.name, "error": _compact_provider_error(exc)})

        if successful:
            selected_name, selected = max(
                successful,
                key=lambda row: (row[1][-1].date if row[1] else "", len(row[1])),
            )
            close_spread = _history_close_spread(successful)
            self._record_coverage(
                "get_history",
                successful_real_sources=[name for name, _ in successful],
                failed_real_sources=failures,
                selected_source=selected_name,
                sample_used=False,
                history_close_spread_pct=close_spread,
            )
            self._cache_set("get_history", cache_key, selected)
            return selected

        sample_failures: list[str] = []
        if self.allow_sample_fallback:
            for provider in sample_providers:
                try:
                    candles = provider.get_history(symbol, period, interval)
                    if not candles:
                        raise ProviderError("empty result")
                    self._record_coverage(
                        "get_history",
                        successful_real_sources=[],
                        failed_real_sources=failures,
                        selected_source=provider.name,
                        sample_used=True,
                    )
                    return candles
                except Exception as exc:  # noqa: BLE001
                    sample_failures.append(f"{provider.name}: {_compact_provider_error(exc)}")
        self._record_coverage(
            "get_history",
            successful_real_sources=[],
            failed_real_sources=failures,
            selected_source="",
            sample_used=False,
        )
        raise ProviderError(_coverage_error(failures, sample_failures, "get_history"))

    def get_financials(self, symbol: str) -> Financials:
        cache_key = (normalize_symbol(symbol),)
        cached = self._cache_get("get_financials", cache_key)
        if cached is not None:
            quote = self._cache_get("get_quote", cache_key)
            if quote is not None and enrich_financial_pe(cached, quote):
                coverage = self._coverage.get("get_financials")
                if coverage is not None:
                    coverage["field_sources"] = dict(cached.field_sources)
            return cached
        operation_timeout = self._operation_timeout_or_raise("get_financials")
        successful: list[tuple[str, Financials]] = []
        failures: list[dict[str, str]] = []
        sample_providers: list[MarketDataProvider] = []
        real_providers: list[MarketDataProvider] = []
        for provider in self.providers:
            if not _provider_supports(provider, "get_financials", symbol):
                continue
            if _is_sample_provider(provider):
                sample_providers.append(provider)
                continue
            if blocked := self._circuit_error(provider):
                failures.append({"name": provider.name, "error": blocked})
                continue
            real_providers.append(provider)
        for provider, ok, value in _collect_provider_calls(
            real_providers, "get_financials", (symbol,), operation_timeout,
            self._provider_inflight, self._provider_inflight_lock,
        ):
            try:
                if not ok:
                    if isinstance(value, ProviderTimeoutError):
                        self._trip_circuit(provider)
                    raise value
                financials = value
                if not _financials_have_data(financials):
                    raise ProviderError("无可用字段")
                successful.append((provider.name, financials))
            except Exception as exc:  # noqa: BLE001 - provider failures become visible coverage
                failures.append({"name": provider.name, "error": _compact_provider_error(exc)})

        if successful:
            differences = _financial_field_differences(successful)
            merged = successful[0][1]
            primary_name = successful[0][0]
            merged.field_sources = {
                field_name: primary_name
                for field_name in _FINANCIAL_FIELDS
                if getattr(merged, field_name) is not None
            }
            merged.notes.append(_financial_basis_note(primary_name, merged))
            for provider_name, financials in successful[1:]:
                merged.notes.append(_financial_basis_note(provider_name, financials))
                for field_name in _FINANCIAL_FIELDS:
                    candidate = getattr(financials, field_name)
                    if getattr(merged, field_name) is not None or candidate is None:
                        continue
                    if not _financial_field_compatible(merged, financials, field_name):
                        merged.notes.append(
                            f"未合并 {provider_name} 的 {_FINANCIAL_FIELD_LABELS.get(field_name, field_name)}："
                            "币种或报告期/口径与优先源不一致。"
                        )
                        continue
                    setattr(merged, field_name, candidate)
                    merged.field_sources[field_name] = provider_name
                _extend_unique(merged.notes, financials.notes)
            source_names = list(dict.fromkeys(name for name, _ in successful))
            merged.source = " / ".join(source_names)
            quote = self._cache_get("get_quote", cache_key)
            if quote is not None:
                enrich_financial_pe(merged, quote)
            self._record_coverage(
                "get_financials",
                successful_real_sources=source_names,
                failed_real_sources=failures,
                selected_source=merged.source,
                sample_used=False,
                field_differences_pct=differences,
                field_sources=merged.field_sources,
            )
            _extend_unique(merged.notes, self.report_notes("get_financials"))
            self._cache_set("get_financials", cache_key, merged)
            return merged

        sample_failures: list[str] = []
        if self.allow_sample_fallback:
            for provider in sample_providers:
                try:
                    financials = provider.get_financials(symbol)
                    if not _financials_have_data(financials):
                        raise ProviderError("无可用字段")
                    self._record_coverage(
                        "get_financials",
                        successful_real_sources=[],
                        failed_real_sources=failures,
                        selected_source=financials.source or provider.name,
                        sample_used=True,
                    )
                    _extend_unique(financials.notes, self.report_notes("get_financials"))
                    return financials
                except Exception as exc:  # noqa: BLE001 - report sample failure with real failures
                    sample_failures.append(f"{provider.name}: {_compact_provider_error(exc)}")

        self._record_coverage(
            "get_financials",
            successful_real_sources=[],
            failed_real_sources=failures,
            selected_source="",
            sample_used=False,
        )
        raise ProviderError(_coverage_error(failures, sample_failures, "get_financials"))

    def get_news(self, symbol: str, limit: int = 5) -> list[NewsItem]:
        if limit <= 0:
            self._record_coverage(
                "get_news",
                successful_real_sources=[],
                failed_real_sources=[],
                selected_source="",
                sample_used=False,
            )
            return []
        cache_key = (normalize_symbol(symbol), limit)
        cached = self._cache_get("get_news", cache_key)
        if cached is not None:
            return cached
        operation_timeout = self._operation_timeout_or_raise("get_news")
        successful_sources: list[str] = []
        failures: list[dict[str, str]] = []
        sample_providers: list[MarketDataProvider] = []
        real_providers: list[MarketDataProvider] = []
        aggregated: list[NewsItem] = []
        keywords = _news_keywords_for_symbol(symbol)
        for provider in self.providers:
            if not _provider_supports(provider, "get_news", symbol, limit):
                continue
            if _is_sample_provider(provider):
                sample_providers.append(provider)
                continue
            if blocked := self._circuit_error(provider):
                failures.append({"name": provider.name, "error": blocked})
                continue
            real_providers.append(provider)
        for provider, ok, value in _collect_provider_calls(
            real_providers, "get_news", (symbol, max(limit, 1)), operation_timeout,
            self._provider_inflight, self._provider_inflight_lock,
        ):
            try:
                if not ok:
                    if isinstance(value, ProviderTimeoutError):
                        self._trip_circuit(provider)
                    raise value
                items = value
                scoped = bool(getattr(provider, "news_is_symbol_scoped", False))
                relevant = [
                    item for item in items
                    if item.title.strip() and (scoped or _news_item_matches(item, keywords))
                ]
                if not relevant:
                    raise ProviderError("未返回强相关新闻")
                for item in relevant:
                    if not item.source:
                        item.source = provider.name
                successful_sources.append(provider.name)
                aggregated.extend(relevant)
            except Exception as exc:  # noqa: BLE001 - provider failures become visible coverage
                failures.append({"name": provider.name, "error": _compact_provider_error(exc)})

        if aggregated:
            source_names = list(dict.fromkeys(successful_sources))
            result = _dedupe_news(aggregated, limit)
            self._record_coverage(
                "get_news",
                successful_real_sources=source_names,
                failed_real_sources=failures,
                selected_source=" / ".join(source_names),
                sample_used=False,
            )
            self._cache_set("get_news", cache_key, result)
            return result

        sample_failures: list[str] = []
        if self.allow_sample_fallback:
            for provider in sample_providers:
                try:
                    items = provider.get_news(symbol, max(limit, 1))
                    result = _dedupe_news([item for item in items if item.title.strip()], limit)
                    if not result:
                        raise ProviderError("empty result")
                    self._record_coverage(
                        "get_news",
                        successful_real_sources=[],
                        failed_real_sources=failures,
                        selected_source=provider.name,
                        sample_used=True,
                    )
                    return result
                except Exception as exc:  # noqa: BLE001 - report sample failure with real failures
                    sample_failures.append(f"{provider.name}: {_compact_provider_error(exc)}")

        self._record_coverage(
            "get_news",
            successful_real_sources=[],
            failed_real_sources=failures,
            selected_source="",
            sample_used=False,
        )
        raise ProviderError(_coverage_error(failures, sample_failures, "get_news"))

    def _first(self, method: str, *args: Any, validator: Any | None = None) -> Any:
        failures: list[dict[str, str]] = []
        sample_providers: list[MarketDataProvider] = []
        for provider in self.providers:
            if not _provider_supports(provider, method, *args):
                continue
            if _is_sample_provider(provider):
                sample_providers.append(provider)
                continue
            try:
                result = getattr(provider, method)(*args)
                if result and (validator is None or validator(result)):
                    self._record_coverage(
                        method,
                        successful_real_sources=[provider.name],
                        failed_real_sources=failures,
                        selected_source=getattr(result, "source", "") or provider.name,
                        sample_used=False,
                    )
                    return result
                detail = "无可用字段" if method == "get_financials" else "empty result"
                failures.append({"name": provider.name, "error": detail})
            except Exception as exc:  # noqa: BLE001 - convert provider failures to notes/fallback
                failures.append({"name": provider.name, "error": _compact_provider_error(exc)})

        sample_failures: list[str] = []
        if self.allow_sample_fallback:
            for provider in sample_providers:
                try:
                    result = getattr(provider, method)(*args)
                    if result and (validator is None or validator(result)):
                        self._record_coverage(
                            method,
                            successful_real_sources=[],
                            failed_real_sources=failures,
                            selected_source=getattr(result, "source", "") or provider.name,
                            sample_used=True,
                        )
                        return result
                    sample_failures.append(f"{provider.name}: empty result")
                except Exception as exc:  # noqa: BLE001 - report sample failure with real failures
                    sample_failures.append(f"{provider.name}: {_compact_provider_error(exc)}")

        self._record_coverage(
            method,
            successful_real_sources=[],
            failed_real_sources=failures,
            selected_source="",
            sample_used=False,
        )
        raise ProviderError(_coverage_error(failures, sample_failures, method))

    def _cache_get(self, method: str, args: tuple[Any, ...]) -> Any | None:
        ttl = self._cache_ttls.get(method, 0.0)
        if ttl <= 0:
            return None
        key = (method, *args)
        now = time.monotonic()
        with self._cache_lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            stored_at, value, coverage = entry
            age = now - stored_at
            if age >= ttl:
                self._cache.pop(key, None)
                return None
            cached_value = deepcopy(value)
            cached_coverage = deepcopy(coverage)
        cached_coverage["cache_hit"] = True
        cached_coverage["cache_age_seconds"] = age
        self._coverage[method] = cached_coverage
        return cached_value

    def _cache_set(self, method: str, args: tuple[Any, ...], value: Any) -> None:
        if self._cache_ttls.get(method, 0.0) <= 0:
            return
        coverage = self.source_coverage(method)
        coverage["cache_hit"] = False
        coverage["cache_age_seconds"] = 0.0
        key = (method, *args)
        with self._cache_lock:
            self._cache[key] = (time.monotonic(), deepcopy(value), coverage)

    @contextmanager
    def request_deadline(self):  # noqa: ANN201
        """Share one wall-clock budget across a multi-operation snapshot."""
        previous = self._request_deadline
        self._request_deadline = time.monotonic() + self.snapshot_timeout
        try:
            yield
        finally:
            self._request_deadline = previous

    def _operation_timeout_or_raise(self, method: str) -> float:
        timeout = self.provider_timeout
        if self._request_deadline is not None:
            timeout = min(timeout, self._request_deadline - time.monotonic())
        if timeout > 0:
            return timeout
        error = "snapshot total deadline exhausted before this operation started"
        self._record_coverage(
            method,
            successful_real_sources=[],
            failed_real_sources=[{"name": "request deadline", "error": error}],
            selected_source="",
            sample_used=False,
        )
        raise ProviderTimeoutError(error)

    def reset_coverage(self) -> None:
        self._coverage.clear()

    def _circuit_error(self, provider: MarketDataProvider) -> str:
        remaining = self._provider_circuit_until.get(id(provider), 0.0) - time.monotonic()
        if remaining <= 0:
            self._provider_circuit_until.pop(id(provider), None)
            return ""
        return f"temporarily skipped after timeout ({remaining:.1f}s cooldown remaining)"

    def _trip_circuit(self, provider: MarketDataProvider) -> None:
        self._provider_circuit_until[id(provider)] = time.monotonic() + self.provider_cooldown

    def source_coverage(self, method: str) -> dict[str, Any]:
        row = self._coverage.get(method) or self._empty_coverage(method)
        return {
            **row,
            "successful_real_sources": list(row["successful_real_sources"]),
            "failed_real_sources": [dict(item) for item in row["failed_real_sources"]],
            "field_differences_pct": dict(row["field_differences_pct"]),
            "field_sources": dict(row["field_sources"]),
        }

    def report_notes(self, method: str | None = None) -> list[str]:
        methods = [method] if method else [name for name in _COVERAGE_LABELS if name in self._coverage]
        notes: list[str] = []
        for method_name in methods:
            coverage = self._coverage.get(method_name)
            if not coverage:
                continue
            label = _COVERAGE_LABELS.get(method_name, method_name)
            sources = coverage["successful_real_sources"]
            if sources:
                notes.append(f"{label}真实来源覆盖: {'、'.join(sources)}。")
            if coverage.get("cache_hit"):
                notes.append(f"{label}使用 TTL 缓存，缓存年龄 {coverage.get('cache_age_seconds', 0):.1f} 秒。")
            if coverage["sample_used"]:
                notes.append(f"{label}使用 SAMPLE_FALLBACK；样例数据不属于真实来源，也不构成交叉验证。")
            failures = coverage["failed_real_sources"]
            if failures:
                detail = "；".join(f"{item['name']}: {item['error']}" for item in failures)
                notes.append(f"{label}来源失败: {detail}。")
            spread = coverage.get("price_spread_pct")
            if method_name == "get_quote" and spread is not None:
                notes.append(f"跨源行情最大差异: {spread:.2f}%。")
            history_spread = coverage.get("history_close_spread_pct")
            if method_name == "get_history" and history_spread is not None:
                notes.append(f"历史行情重叠窗口收盘价最大差异: {history_spread:.2f}%。")
            differences = coverage.get("field_differences_pct") or {}
            material_differences = [(name, value) for name, value in differences.items() if value >= 0.01]
            if method_name == "get_financials" and material_differences:
                detail = "、".join(
                    f"{_FINANCIAL_FIELD_LABELS.get(name, name)} {value:.2f}%"
                    for name, value in material_differences
                )
                notes.append(f"基本面跨源字段差异: {detail}。")
        return notes

    def _record_coverage(
        self,
        method: str,
        *,
        successful_real_sources: list[str],
        failed_real_sources: list[dict[str, str]],
        selected_source: str,
        sample_used: bool,
        price_spread_pct: float | None = None,
        field_differences_pct: dict[str, float] | None = None,
        history_close_spread_pct: float | None = None,
        field_sources: dict[str, str] | None = None,
    ) -> None:
        self._coverage[method] = {
            "method": method,
            "successful_real_sources": list(dict.fromkeys(successful_real_sources)),
            "failed_real_sources": [dict(item) for item in failed_real_sources],
            "selected_source": selected_source,
            "sample_used": sample_used,
            "price_spread_pct": price_spread_pct,
            "field_differences_pct": dict(field_differences_pct or {}),
            "history_close_spread_pct": history_close_spread_pct,
            "field_sources": dict(field_sources or {}),
            "cache_hit": False,
            "cache_age_seconds": None,
        }

    @staticmethod
    def _empty_coverage(method: str) -> dict[str, Any]:
        return {
            "method": method,
            "successful_real_sources": [],
            "failed_real_sources": [],
            "selected_source": "",
            "sample_used": False,
            "price_spread_pct": None,
            "field_differences_pct": {},
            "history_close_spread_pct": None,
            "field_sources": {},
            "cache_hit": False,
            "cache_age_seconds": None,
        }

    def diagnostics(self) -> list[dict[str, str]]:
        """Return provider status for CLI visibility."""
        if self._using_default_providers:
            return self._default_diagnostics
        rows: list[dict[str, str]] = []
        for provider in self.providers:
            status = "enabled"
            detail = ""
            if isinstance(provider, SampleDataProvider):
                status = "enabled" if self.allow_sample_fallback else "disabled"
                detail = "demo-only fallback"
            rows.append({"name": provider.name, "status": status, "detail": detail})
        return rows


def _provider_supports(provider: MarketDataProvider, method: str, *args: Any) -> bool:
    supports = getattr(provider, "supports", None)
    if callable(supports):
        return bool(supports(method, *args))
    capabilities = getattr(provider, "capabilities", None)
    return capabilities is None or method in capabilities


def _collect_provider_calls(
    providers: list[MarketDataProvider],
    method: str,
    args: tuple[Any, ...],
    timeout: float,
    inflight: set[int],
    inflight_lock: threading.Lock,
) -> list[tuple[MarketDataProvider, bool, Any]]:
    """Run independent real providers concurrently under one operation deadline.

    Daemon workers are intentional: unlike a normal ThreadPoolExecutor context,
    a stuck third-party SDK cannot make the request wait again during shutdown.
    Late results are ignored and the timed-out source is reported.
    """
    if not providers:
        return []
    completed: queue.Queue[tuple[int, bool, Any]] = queue.Queue()

    def worker(index: int, provider: MarketDataProvider, provider_key: int) -> None:
        try:
            completed.put((index, True, getattr(provider, method)(*args)))
        except Exception as exc:  # noqa: BLE001 - returned as provider coverage
            completed.put((index, False, exc))
        finally:
            with inflight_lock:
                inflight.discard(provider_key)

    results: dict[int, tuple[bool, Any]] = {}
    for index, provider in enumerate(providers):
        provider_key = id(provider)
        with inflight_lock:
            if provider_key in inflight:
                results[index] = (
                    False,
                    ProviderTimeoutError("previous provider call is still running; no duplicate worker started"),
                )
                continue
            inflight.add(provider_key)
        threading.Thread(
            target=worker,
            args=(index, provider, provider_key),
            name=f"finance-provider-{provider.name}-{method}",
            daemon=True,
        ).start()

    deadline = time.monotonic() + timeout
    while len(results) < len(providers):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            index, ok, value = completed.get(timeout=remaining)
        except queue.Empty:
            break
        results[index] = (ok, value)

    while True:
        try:
            index, ok, value = completed.get_nowait()
        except queue.Empty:
            break
        results[index] = (ok, value)

    rows: list[tuple[MarketDataProvider, bool, Any]] = []
    for index, provider in enumerate(providers):
        ok, value = results.get(
            index,
            (False, ProviderTimeoutError(f"timed out after {timeout:g}s operation deadline")),
        )
        rows.append((provider, ok, value))
    return rows


def _is_sample_provider(provider: MarketDataProvider) -> bool:
    return isinstance(provider, SampleDataProvider) or provider.name == "SAMPLE_FALLBACK"


def _quote_value_present(value: Any) -> bool:
    return value is not None and value != ""


def _quote_field_compatible(primary: Quote, candidate: Quote, field_name: str) -> bool:
    if normalize_symbol(primary.symbol) != normalize_symbol(candidate.symbol):
        return False
    if (
        field_name in {"market_cap", "eps"}
        and primary.currency
        and candidate.currency
        and primary.currency != candidate.currency
    ):
        return False
    return True


def _financials_have_data(financials: Financials) -> bool:
    return any(getattr(financials, field_name) is not None for field_name in _FINANCIAL_FIELDS)


def enrich_financial_pe(financials: Financials, quote: Quote) -> bool:
    """Fill a missing PE from a reported quote value or a strictly guarded derivation."""
    if financials.pe_ratio is not None:
        return False
    if normalize_symbol(financials.symbol) != normalize_symbol(quote.symbol):
        return False
    if quote.pe_ratio is not None and float(quote.pe_ratio) > 0:
        financials.pe_ratio = float(quote.pe_ratio)
        financials.field_sources["pe_ratio"] = (
            quote.field_sources.get("pe_ratio") or quote.source or "UNKNOWN_QUOTE_SOURCE"
        )
        return True

    price = _to_float(quote.price)
    eps = _to_float(financials.eps)
    if price is None or price <= 0 or eps is None or eps <= 0:
        return False
    if _unsafe_derivation_source(quote.source) or _unsafe_derivation_source(financials.source):
        return False
    quote_currency = _canonical_currency(quote.currency)
    financial_currency = _canonical_currency(financials.currency)
    if not quote_currency or quote_currency != financial_currency:
        return False
    period_type = str(financials.period_type or "").strip().upper()
    if period_type not in {"TTM", "ANNUAL"}:
        return False
    age_days = _financial_age_days(financials.as_of)
    if age_days is None or not 0 <= age_days <= 550:
        return False

    estimate = price / eps
    price_source = quote.field_sources.get("price") or quote.source or "UNKNOWN_QUOTE_SOURCE"
    eps_source = financials.field_sources.get("eps") or financials.source or "UNKNOWN_FINANCIAL_SOURCE"
    financials.pe_ratio = round(estimate, 4)
    financials.field_sources["pe_ratio"] = (
        f"DERIVED: {price_source} price / {eps_source} {period_type} EPS"
    )
    financials.notes.append(
        f"估算 PE {estimate:.2f} = 当前价格 {price:g}（{price_source}，{quote.as_of or '未知时点'}）"
        f" ÷ {period_type} EPS {eps:g}（{eps_source}，报告期 {financials.as_of}）；"
        "程序推导值，不是数据商直接报告的 PE。"
    )
    return True


def _unsafe_derivation_source(source: str) -> bool:
    upper = str(source or "").upper()
    return any(token in upper for token in ("SAMPLE_FALLBACK", "UNAVAILABLE", "SKIPPED"))


def _canonical_currency(value: str) -> str:
    compact = str(value or "").strip().upper().replace(" ", "")
    aliases = {
        "USD": "USD",
        "US$": "USD",
        "美元": "USD",
        "CNY": "CNY",
        "RMB": "CNY",
        "人民币": "CNY",
        "人民币元": "CNY",
        "HKD": "HKD",
        "港元": "HKD",
        "港币": "HKD",
    }
    return aliases.get(compact, compact if len(compact) == 3 and compact.isalpha() else "")


def _financial_age_days(value: str) -> int | None:
    try:
        report_date = datetime.fromisoformat(str(value).strip()[:10]).date()
    except (TypeError, ValueError):
        return None
    return (datetime.now(UTC).date() - report_date).days


def _financial_field_differences(financials: list[tuple[str, Financials]]) -> dict[str, float]:
    differences: dict[str, float] = {}
    primary = financials[0][1] if financials else None
    for field_name in _FINANCIAL_FIELDS:
        values: list[float] = []
        for _, row in financials:
            if primary is not None and not _financial_field_compatible(primary, row, field_name):
                continue
            value = _to_float(getattr(row, field_name))
            if value is not None:
                values.append(value)
        if len(values) < 2:
            continue
        denominator = max(abs(value) for value in values)
        differences[field_name] = 0.0 if denominator == 0 else (max(values) - min(values)) / denominator * 100
    return differences


def _financial_field_compatible(primary: Financials, candidate: Financials, field_name: str) -> bool:
    if (
        field_name in _FINANCIAL_MONETARY_FIELDS
        and primary.currency
        and candidate.currency
        and primary.currency != candidate.currency
    ):
        return False
    if field_name in _FINANCIAL_PERIOD_FIELDS:
        if primary.as_of and candidate.as_of and primary.as_of != candidate.as_of:
            return False
        if primary.period_type and candidate.period_type and primary.period_type != candidate.period_type:
            return False
    return True


def _financial_basis_note(provider_name: str, financials: Financials) -> str:
    return (
        f"{provider_name} 财务口径: report_period={financials.as_of or '未知'}, "
        f"period_type={financials.period_type or '未知'}, currency={financials.currency or '未知'}, "
        f"fetched_at={financials.fetched_at or '未知'}。"
    )


def _field_value(row: dict[str, Any], candidates: str | tuple[str, ...]) -> Any:
    names = (candidates,) if isinstance(candidates, str) else candidates
    for name in names:
        if name and row.get(name) not in (None, "", "-"):
            return row.get(name)
    return None


def _debt_to_equity_from_debt_to_assets(value: Any) -> float | None:
    """Convert debt/assets percent to downstream debt/equity percent."""
    debt_to_assets = _to_float(value)
    if debt_to_assets is None or debt_to_assets < 0:
        return None
    if debt_to_assets >= 100:
        return 1_000_000.0  # zero/negative equity; finite sentinel reliably trips risk gates
    return debt_to_assets / (100 - debt_to_assets) * 100


def _lots_to_shares(value: Any) -> int | None:
    lots = _to_int(value)
    return lots * 100 if lots is not None else None


def _history_close_spread(histories: list[tuple[str, list[Candle]]]) -> float | None:
    by_source = [
        {candle.date: float(candle.close) for candle in candles if candle.close is not None}
        for _, candles in histories
    ]
    if len(by_source) < 2:
        return None
    common_dates = set(by_source[0])
    for rows in by_source[1:]:
        common_dates.intersection_update(rows)
    if not common_dates:
        return None
    spreads: list[float] = []
    for date in common_dates:
        values = [rows[date] for rows in by_source]
        low = min(values)
        if low > 0:
            spreads.append((max(values) - low) / low * 100)
    return max(spreads) if spreads else None


def _quote_price_spread(quotes: list[tuple[str, Quote]]) -> float | None:
    prices = [float(quote.price) for _, quote in quotes if quote.price is not None and quote.price > 0]
    if len(prices) < 2:
        return None
    low = min(prices)
    return (max(prices) - low) / low * 100


def _news_keywords_for_symbol(symbol: str) -> list[str]:
    normalized = normalize_symbol(symbol)
    keywords = _news_keywords(normalized, to_yahoo_symbol(normalized))
    for alias, target in CHINESE_SYMBOLS.items():
        if normalize_symbol(target) == normalized and alias.lower() not in keywords:
            keywords.append(alias.lower())
    return keywords


def _news_item_matches(item: NewsItem, keywords: list[str]) -> bool:
    return _news_matches(
        {
            "title": item.title,
            "summary": item.summary,
            "link": item.link,
            "publisher": item.publisher,
        },
        keywords,
    )


def _dedupe_news(items: list[NewsItem], limit: int) -> list[NewsItem]:
    if limit <= 0:
        return []
    candidates: list[NewsItem] = []
    seen_titles: set[str] = set()
    seen_links: set[str] = set()
    for item in sorted(items, key=_news_timestamp, reverse=True):
        title_key = re.sub(r"[\W_]+", "", item.title.lower())
        link_key = item.link.split("?", 1)[0].rstrip("/").lower()
        if not title_key and not link_key:
            continue
        if title_key and title_key in seen_titles:
            continue
        if link_key and link_key in seen_links:
            continue
        if title_key:
            seen_titles.add(title_key)
        if link_key:
            seen_links.add(link_key)
        candidates.append(item)

    by_source: dict[str, list[NewsItem]] = {}
    for item in candidates:
        by_source.setdefault(item.source or item.publisher or "unknown", []).append(item)
    rows: list[NewsItem] = []
    while len(rows) < limit and any(by_source.values()):
        for source_items in by_source.values():
            if source_items and len(rows) < limit:
                rows.append(source_items.pop(0))
    return rows


def _news_timestamp(item: NewsItem) -> float:
    value = str(item.published_at or "").strip()
    if not value:
        return 0.0
    for candidate in (value, value.replace("Z", "+00:00")):
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.timestamp()
        except ValueError:
            pass
    for pattern in ("%Y%m%dT%H%M%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, pattern).replace(tzinfo=UTC).timestamp()
        except ValueError:
            continue
    return 0.0


def _compact_provider_error(exc: Exception, limit: int = 180) -> str:
    text = " ".join(str(exc).split()) or exc.__class__.__name__
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _coverage_error(
    failures: list[dict[str, str]],
    sample_failures: list[str],
    method: str,
) -> str:
    errors = [f"{item['name']}: {item['error']}" for item in failures]
    errors.extend(sample_failures)
    return "; ".join(errors) or f"no provider supports {method}"


def _extend_unique(target: list[str], values: list[str]) -> None:
    for value in values:
        if value not in target:
            target.append(value)


def export_history_csv(candles: list[Candle]) -> str:
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["date", "open", "high", "low", "close", "volume"])
    for candle in candles:
        writer.writerow([candle.date, candle.open, candle.high, candle.low, candle.close, candle.volume])
    return buffer.getvalue()


def _raw(value: Any) -> float | None:
    if isinstance(value, dict):
        value = value.get("raw")
    return _to_float(value)


def _text_value(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("raw") or value.get("fmt")
    return str(value or "")


def _unix_date(value: float | None) -> str:
    if value is None:
        return ""
    try:
        timestamp = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(timestamp, UTC).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return ""


def _latest_report_date(*rows: dict[str, Any]) -> str:
    values: list[str] = []
    for row in rows:
        for key in ("end_date", "report_date", "ann_date"):
            raw = str(row.get(key) or "").strip()
            if raw:
                values.append(_format_trade_date(raw[:10].replace("-", "")))
                break
    return max(values) if values else ""


def _to_float(value: Any) -> float | None:
    if value in (None, "", "None", "N/A", "-"):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    if value in (None, "", "None", "N/A", "-"):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _percent_to_float(value: Any) -> float | None:
    if isinstance(value, str):
        value = value.replace("%", "")
    return _to_float(value)


def _list_float(values: list[Any] | None, index: int) -> float | None:
    if not values or index >= len(values):
        return None
    return _to_float(values[index])


def _list_int(values: list[Any] | None, index: int) -> int | None:
    if not values or index >= len(values):
        return None
    return _to_int(values[index])


def _news_keywords(normalized: str, query_symbol: str, quote_name: str = "") -> list[str]:
    raw = [
        normalized,
        query_symbol,
        normalized.split(".", 1)[0],
        query_symbol.split(".", 1)[0],
        quote_name,
    ]
    if normalized.endswith(".HK"):
        raw.append(normalized[:-3].lstrip("0") or normalized[:-3])
    generic_words = {
        "co", "company", "corp", "corporation", "group", "holding", "holdings",
        "inc", "limited", "ltd", "plc", "tech", "technologies", "technology",
    }
    for part in quote_name.replace(",", " ").replace(".", " ").split():
        if len(part) >= 4 and part.lower() not in generic_words:
            raw.append(part)
    keywords: list[str] = []
    for value in raw:
        cleaned = str(value).strip().lower()
        if cleaned and cleaned not in keywords:
            keywords.append(cleaned)
    return keywords


def _news_matches(row: dict[str, Any], keywords: list[str]) -> bool:
    # Publisher names and URL paths are weak metadata: neither may establish
    # article relevance on its own (e.g. ticker FOX vs publisher Fox Business).
    haystack = " ".join(str(row.get(key, "")) for key in ("title", "summary")).lower()
    for keyword in keywords:
        if re.fullmatch(r"[a-z0-9]+", keyword):
            if re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", haystack):
                return True
        elif keyword in haystack:
            return True
    return False


def _period_to_days(period: str) -> int:
    normalized = period.lower().strip()
    table = {"1mo": 22, "3mo": 66, "6mo": 126, "1y": 252, "2y": 504, "5y": 1260}
    if normalized in table:
        return table[normalized]
    if normalized.endswith("d") and normalized[:-1].isdigit():
        return max(int(normalized[:-1]), 5)
    return 252


def _trim_period(candles: list[Candle], period: str) -> list[Candle]:
    days = _period_to_days(period)
    return candles[-days:]


def _date_window(period: str) -> tuple[str, str]:
    end = datetime.now(UTC).date()
    start = end - timedelta(days=_period_to_days(period) * 7 // 5 + 10)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def _format_trade_date(value: str) -> str:
    text = value.strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text


def _env_truthy(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off", ""}


def _positive_env_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) and value > 0 else default


def _nonnegative_env_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) and value >= 0 else default


def _sample_profile(symbol: str) -> dict[str, Any]:
    normalized = normalize_symbol(symbol)
    if normalized not in _SAMPLE_PROFILES:
        raise ProviderError(f"no sample fallback profile for {normalized}")
    return _SAMPLE_PROFILES[normalized]


def _generic_profile(symbol: str) -> dict[str, Any]:
    return {
        "name": symbol,
        "currency": "USD",
        "price": 100.0,
        "volume": 10_000_000,
        "market_cap": 50_000_000_000,
        "pe_ratio": 22.0,
        "forward_pe": 20.0,
        "eps": 4.5,
        "revenue": 20_000_000_000,
        "gross_profit": 9_000_000_000,
        "net_income": 4_000_000_000,
        "free_cash_flow": 3_500_000_000,
        "debt_to_equity": 80.0,
        "return_on_equity": 0.18,
        "profit_margin": 0.2,
        "trend": 0.08,
        "volatility": 0.025,
    }


_SAMPLE_PROFILES: dict[str, dict[str, Any]] = {
    "AAPL": {
        **_generic_profile("AAPL"),
        "name": "Apple Inc.",
        "price": 210.0,
        "market_cap": 3_200_000_000_000,
        "pe_ratio": 31.0,
        "forward_pe": 28.0,
        "eps": 6.7,
        "revenue": 390_000_000_000,
        "gross_profit": 180_000_000_000,
        "net_income": 100_000_000_000,
        "free_cash_flow": 95_000_000_000,
        "debt_to_equity": 150.0,
        "return_on_equity": 1.2,
        "profit_margin": 0.25,
        "trend": 0.10,
        "volatility": 0.018,
    },
    "NVDA": {
        **_generic_profile("NVDA"),
        "name": "NVIDIA Corporation",
        "price": 145.0,
        "market_cap": 3_500_000_000_000,
        "pe_ratio": 45.0,
        "forward_pe": 35.0,
        "eps": 3.2,
        "revenue": 130_000_000_000,
        "gross_profit": 95_000_000_000,
        "net_income": 70_000_000_000,
        "free_cash_flow": 60_000_000_000,
        "debt_to_equity": 25.0,
        "return_on_equity": 0.85,
        "profit_margin": 0.54,
        "trend": 0.32,
        "volatility": 0.035,
    },
    "AMD": {
        **_generic_profile("AMD"),
        "name": "Advanced Micro Devices, Inc.",
        "price": 165.0,
        "market_cap": 270_000_000_000,
        "pe_ratio": 48.0,
        "forward_pe": 29.0,
        "eps": 3.4,
        "revenue": 28_000_000_000,
        "gross_profit": 14_000_000_000,
        "net_income": 4_200_000_000,
        "free_cash_flow": 3_000_000_000,
        "debt_to_equity": 7.0,
        "return_on_equity": 0.08,
        "profit_margin": 0.15,
        "trend": 0.18,
        "volatility": 0.04,
    },
    "TSLA": {
        **_generic_profile("TSLA"),
        "name": "Tesla, Inc.",
        "price": 260.0,
        "market_cap": 830_000_000_000,
        "pe_ratio": 70.0,
        "forward_pe": 55.0,
        "eps": 3.7,
        "revenue": 100_000_000_000,
        "gross_profit": 18_000_000_000,
        "net_income": 12_000_000_000,
        "free_cash_flow": 4_500_000_000,
        "debt_to_equity": 15.0,
        "return_on_equity": 0.18,
        "profit_margin": 0.12,
        "trend": 0.02,
        "volatility": 0.045,
    },
    "MSFT": {
        **_generic_profile("MSFT"),
        "name": "Microsoft Corporation",
        "price": 480.0,
        "market_cap": 3_600_000_000_000,
        "pe_ratio": 36.0,
        "forward_pe": 31.0,
        "eps": 13.2,
        "revenue": 260_000_000_000,
        "gross_profit": 180_000_000_000,
        "net_income": 95_000_000_000,
        "free_cash_flow": 75_000_000_000,
        "debt_to_equity": 35.0,
        "return_on_equity": 0.35,
        "profit_margin": 0.36,
        "trend": 0.15,
        "volatility": 0.02,
    },
    "600519.SS": {
        **_generic_profile("600519.SS"),
        "name": "Kweichow Moutai Co., Ltd.",
        "currency": "CNY",
        "price": 1500.0,
        "market_cap": 1_900_000_000_000,
        "pe_ratio": 23.0,
        "forward_pe": 21.0,
        "eps": 65.0,
        "revenue": 170_000_000_000,
        "gross_profit": 155_000_000_000,
        "net_income": 85_000_000_000,
        "free_cash_flow": 75_000_000_000,
        "debt_to_equity": 10.0,
        "return_on_equity": 0.32,
        "profit_margin": 0.50,
        "trend": 0.04,
        "volatility": 0.018,
    },
}
