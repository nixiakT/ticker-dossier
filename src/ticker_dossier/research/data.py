"""Market-data orchestration, source selection, and stable compatibility API.

Concrete HTTP/API adapters live in :mod:`ticker_dossier.integrations.market_data`.
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
from datetime import UTC, datetime
from io import StringIO
from typing import Any

from ticker_dossier.config import load_local_env
from ticker_dossier.integrations.market_data import (
    AKShareProvider,
    AlphaVantageProvider,
    MarketDataProvider,
    ProviderError,
    ProviderTimeoutError,
    SampleDataProvider,
    TushareProvider,
    YahooFinanceProvider,
)
from ticker_dossier.integrations.market_data._normalization import (
    _compact_provider_error,
    _date_window,
    _debt_to_equity_from_debt_to_assets,
    _field_value,
    _financials_have_data,
    _format_trade_date,
    _latest_report_date,
    _list_float,
    _list_int,
    _lots_to_shares,
    _news_keywords,
    _news_matches,
    _percent_to_float,
    _period_to_days,
    _raw,
    _text_value,
    _to_float,
    _to_int,
    _trim_period,
    _unix_date,
)
from ticker_dossier.integrations.market_data.providers.sample import (
    _SAMPLE_PROFILES,
    _generic_profile,
    _sample_profile,
)

from .models import Candle, Financials, NewsItem, Quote
from .symbols import CHINESE_SYMBOLS, normalize_symbol, to_yahoo_symbol


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
