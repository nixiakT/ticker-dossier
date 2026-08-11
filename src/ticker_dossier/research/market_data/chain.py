"""Provider-chain orchestration for market-data research.

Concrete HTTP/API adapters live in :mod:`ticker_dossier.integrations.market_data`.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import threading
import time
from typing import Any, cast

from ticker_dossier.config import load_local_env
from ticker_dossier.integrations.market_data import (
    MarketDataProvider,
    ProviderError,
    ProviderTimeoutError,
)
from ticker_dossier.integrations.market_data._normalization import (
    _compact_provider_error,
    _financials_have_data,
)

from ..models import Candle, Financials, NewsItem, Quote
from ..symbols import normalize_symbol
from .cache import get_cached, set_cached
from .configuration import (
    _env_truthy,
    _nonnegative_env_float,
    _positive_env_float,
    build_default_providers,
    provider_diagnostics,
)
from .coverage import (
    _coverage_error,
    _empty_coverage,
    record_coverage,
    report_notes,
    source_coverage,
)
from .execution import (
    _collect_provider_calls,
    _is_sample_provider,
    _OperationDeadlineTimeout,
    ProviderFlights,
    circuit_error,
    partition_providers,
    trip_circuit,
)
from .request_state import RequestStateStore
from .selection import (
    _dedupe_news,
    _extend_unique,
    _news_item_matches,
    _news_keywords_for_symbol,
    enrich_financial_pe,
    merge_financials,
    select_history,
    select_quote,
)


class ProviderChain:
    def __init__(self, providers: list[MarketDataProvider] | None = None):
        load_local_env()
        configured_sample_fallback = _env_truthy("FINANCE_ALLOW_SAMPLE_FALLBACK", default=False)
        self.allow_sample_fallback = configured_sample_fallback or bool(
            providers and any(_is_sample_provider(provider) for provider in providers)
        )
        self._using_default_providers = providers is None
        self._owned_providers: list[MarketDataProvider]
        if providers is None:
            default, self._default_diagnostics, self._owned_providers = (
                build_default_providers(configured_sample_fallback)
            )
            self.providers = default
        else:
            self._default_diagnostics = []
            self._owned_providers = []
            self.providers = providers
        self.provider_timeout = _positive_env_float("FINANCE_PROVIDER_TIMEOUT_SECONDS", 25.0)
        self.snapshot_timeout = _positive_env_float("FINANCE_SNAPSHOT_TIMEOUT_SECONDS", 45.0)
        self.provider_cooldown = _positive_env_float("FINANCE_PROVIDER_COOLDOWN_SECONDS", 60.0)
        self._provider_circuit_until: dict[int, float] = {}
        self._provider_circuit_lock = threading.Lock()
        self._provider_inflight: ProviderFlights = {}
        self._provider_inflight_lock = threading.Lock()
        self._request_states = RequestStateStore(f"provider-chain-{id(self)}")
        self._lifecycle_lock = threading.Lock()
        self._closed = False
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
            cached_quote = cast(Quote, cached)
            _extend_unique(cached_quote.notes, self.report_notes("get_quote"))
            return cached_quote
        operation_timeout = self._operation_timeout_or_raise("get_quote")
        successful: list[tuple[str, Quote]] = []
        real_providers, sample_providers, failures = partition_providers(
            self.providers,
            "get_quote",
            (symbol,),
            self._circuit_error,
        )
        for provider, ok, value in _collect_provider_calls(
            real_providers, "get_quote", (symbol,), operation_timeout,
            self._provider_inflight, self._provider_inflight_lock,
        ):
            try:
                if not ok:
                    if self._timeout_should_trip_circuit(value, operation_timeout):
                        self._trip_circuit(provider)
                    raise value
                quote = value
                if quote.price is None:
                    raise ProviderError("无可用价格字段")
                successful.append((provider.name, quote))
            except Exception as exc:  # noqa: BLE001 - provider errors are reported as data coverage
                failures.append({"name": provider.name, "error": _compact_provider_error(exc)})

        if successful:
            selected_name, selected, spread = select_quote(successful)
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
            return cast(list[Candle], cached)
        operation_timeout = self._operation_timeout_or_raise("get_history")
        successful: list[tuple[str, list[Candle]]] = []
        real_providers, sample_providers, failures = partition_providers(
            self.providers,
            "get_history",
            (symbol, period, interval),
            self._circuit_error,
        )
        for provider, ok, value in _collect_provider_calls(
            real_providers, "get_history", (symbol, period, interval), operation_timeout,
            self._provider_inflight, self._provider_inflight_lock,
        ):
            try:
                if not ok:
                    if self._timeout_should_trip_circuit(value, operation_timeout):
                        self._trip_circuit(provider)
                    raise value
                candles = value
                if not candles:
                    raise ProviderError("empty result")
                successful.append((provider.name, candles))
            except Exception as exc:  # noqa: BLE001
                failures.append({"name": provider.name, "error": _compact_provider_error(exc)})

        if successful:
            selected_name, selected, close_spread = select_history(successful)
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
                    sample_candles: list[Candle] = provider.get_history(symbol, period, interval)
                    if not sample_candles:
                        raise ProviderError("empty result")
                    self._record_coverage(
                        "get_history",
                        successful_real_sources=[],
                        failed_real_sources=failures,
                        selected_source=provider.name,
                        sample_used=True,
                    )
                    return sample_candles
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
            cached_financials = cast(Financials, cached)
            quote = self._cache_get("get_quote", cache_key)
            if quote is not None and enrich_financial_pe(cached_financials, cast(Quote, quote)):
                coverage_by_method = self._request_states.mutable_coverage_copy()
                coverage = coverage_by_method.get("get_financials")
                if coverage is not None:
                    coverage["field_sources"] = dict(cached_financials.field_sources)
                    self._request_states.replace_coverage(coverage_by_method)
            _extend_unique(cached_financials.notes, self.report_notes("get_financials"))
            return cached_financials
        operation_timeout = self._operation_timeout_or_raise("get_financials")
        successful: list[tuple[str, Financials]] = []
        real_providers, sample_providers, failures = partition_providers(
            self.providers,
            "get_financials",
            (symbol,),
            self._circuit_error,
        )
        for provider, ok, value in _collect_provider_calls(
            real_providers, "get_financials", (symbol,), operation_timeout,
            self._provider_inflight, self._provider_inflight_lock,
        ):
            try:
                if not ok:
                    if self._timeout_should_trip_circuit(value, operation_timeout):
                        self._trip_circuit(provider)
                    raise value
                financials = value
                if not _financials_have_data(financials):
                    raise ProviderError("无可用字段")
                successful.append((provider.name, financials))
            except Exception as exc:  # noqa: BLE001 - provider failures become visible coverage
                failures.append({"name": provider.name, "error": _compact_provider_error(exc)})

        if successful:
            merged, source_names, differences = merge_financials(successful)
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
            return cast(list[NewsItem], cached)
        operation_timeout = self._operation_timeout_or_raise("get_news")
        successful_sources: list[str] = []
        aggregated: list[NewsItem] = []
        keywords = _news_keywords_for_symbol(symbol)
        real_providers, sample_providers, failures = partition_providers(
            self.providers,
            "get_news",
            (symbol, limit),
            self._circuit_error,
        )
        for provider, ok, value in _collect_provider_calls(
            real_providers, "get_news", (symbol, max(limit, 1)), operation_timeout,
            self._provider_inflight, self._provider_inflight_lock,
        ):
            try:
                if not ok:
                    if self._timeout_should_trip_circuit(value, operation_timeout):
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

    def _cache_get(self, method: str, args: tuple[Any, ...]) -> Any | None:
        coverage = self._request_states.mutable_coverage_copy()
        cached = get_cached(
            method,
            args,
            ttls=self._cache_ttls,
            cache=self._cache,
            cache_lock=self._cache_lock,
            coverage_by_method=coverage,
        )
        if cached is not None:
            self._request_states.replace_coverage(coverage)
        return cached

    def _cache_set(self, method: str, args: tuple[Any, ...], value: Any) -> None:
        coverage = self._request_states.current().coverage
        set_cached(
            method,
            args,
            value,
            ttls=self._cache_ttls,
            cache=self._cache,
            cache_lock=self._cache_lock,
            coverage_by_method=coverage,
        )

    @contextmanager
    def request_deadline(self) -> Iterator[None]:
        """Share one wall-clock budget across a multi-operation snapshot."""
        deadline = time.monotonic() + self.snapshot_timeout
        with self._request_states.isolated(deadline):
            yield

    def _operation_timeout_or_raise(self, method: str) -> float:
        timeout = self.provider_timeout
        request_deadline = self._request_states.current().deadline
        if request_deadline is not None:
            timeout = min(timeout, request_deadline - time.monotonic())
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
        self._request_states.replace_coverage({})

    def _circuit_error(self, provider: MarketDataProvider) -> str:
        with self._provider_circuit_lock:
            return circuit_error(self._provider_circuit_until, provider)

    def _trip_circuit(self, provider: MarketDataProvider) -> None:
        with self._provider_circuit_lock:
            trip_circuit(self._provider_circuit_until, provider, self.provider_cooldown)

    def _timeout_should_trip_circuit(self, value: Any, operation_timeout: float) -> bool:
        if not isinstance(value, ProviderTimeoutError):
            return False
        return (
            not isinstance(value, _OperationDeadlineTimeout)
            or operation_timeout >= self.provider_timeout
        )

    def source_coverage(self, method: str) -> dict[str, Any]:
        return source_coverage(self._request_states.current().coverage, method)

    def report_notes(self, method: str | None = None) -> list[str]:
        return report_notes(self._request_states.current().coverage, method)

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
        coverage = self._request_states.mutable_coverage_copy()
        record_coverage(
            coverage,
            method,
            successful_real_sources=successful_real_sources,
            failed_real_sources=failed_real_sources,
            selected_source=selected_source,
            sample_used=sample_used,
            price_spread_pct=price_spread_pct,
            field_differences_pct=field_differences_pct,
            history_close_spread_pct=history_close_spread_pct,
            field_sources=field_sources,
        )
        self._request_states.replace_coverage(coverage)

    _empty_coverage = staticmethod(_empty_coverage)

    def diagnostics(self) -> list[dict[str, str]]:
        """Return provider status for CLI visibility."""
        if self._using_default_providers:
            return self._default_diagnostics
        return provider_diagnostics(self.providers, self.allow_sample_fallback)

    def close(self) -> None:
        """Close only adapters constructed by this chain."""
        with self._lifecycle_lock:
            if self._closed:
                return
            failures: list[str] = []
            remaining: list[MarketDataProvider] = []
            for provider in reversed(self._owned_providers):
                try:
                    close = getattr(provider, "close", None)
                    if callable(close):
                        close()
                except Exception as exc:  # noqa: BLE001 - close every owned adapter
                    failures.append(f"{provider.name}: {_compact_provider_error(exc)}")
                    remaining.append(provider)
            self._owned_providers = list(reversed(remaining))
            self._closed = not self._owned_providers
            if failures:
                raise ProviderError("provider close failed: " + "; ".join(failures))
