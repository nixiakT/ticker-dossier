from __future__ import annotations

from contextvars import copy_context
import threading
import time
from typing import Any, Callable

import pytest

from ticker_dossier.integrations.market_data import ProviderError, ProviderTimeoutError
from ticker_dossier.integrations.market_data.providers.alpha_vantage import AlphaVantageProvider
from ticker_dossier.integrations.market_data.providers.tushare import TushareProvider
from ticker_dossier.integrations.market_data.providers.yahoo import YahooFinanceProvider
from ticker_dossier.research.agent import FinanceResearchAgent
from ticker_dossier.research.market_data import chain as chain_module
from ticker_dossier.research.market_data.chain import ProviderChain
from ticker_dossier.research.models import Candle, Financials, NewsItem, Quote


def _run_threads(*functions: Callable[[], Any]) -> list[Any]:
    results: list[Any] = [None] * len(functions)
    errors: list[BaseException] = []
    start = threading.Barrier(len(functions))

    def run(index: int, function: Callable[[], Any]) -> None:
        try:
            start.wait(timeout=1)
            results[index] = function()
        except BaseException as exc:  # noqa: BLE001 - surface worker assertions in the test
            errors.append(exc)

    threads = [
        threading.Thread(target=run, args=(index, function), daemon=True)
        for index, function in enumerate(functions)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert not [thread for thread in threads if thread.is_alive()]
    assert errors == []
    return results


def test_identical_concurrent_calls_share_one_healthy_provider_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINANCE_QUOTE_CACHE_TTL_SECONDS", "0")
    monkeypatch.setenv("FINANCE_PROVIDER_TIMEOUT_SECONDS", "1")

    class Provider:
        name = "SHARED"

        def __init__(self) -> None:
            self.calls = 0
            self.entered = threading.Event()
            self.release = threading.Event()

        def get_quote(self, symbol: str) -> Quote:
            self.calls += 1
            self.entered.set()
            assert self.release.wait(timeout=1)
            return Quote(symbol=symbol, price=100, source=self.name)

    provider = Provider()
    chain = ProviderChain(providers=[provider])  # type: ignore[list-item]

    def fetch() -> tuple[float | None, dict[str, Any]]:
        with chain.request_deadline():
            quote = chain.get_quote("AAPL")
            return quote.price, chain.source_coverage("get_quote")

    results: list[Any] = []
    errors: list[BaseException] = []

    def run() -> None:
        try:
            results.append(fetch())
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    first = threading.Thread(target=run, daemon=True)
    second = threading.Thread(target=run, daemon=True)
    first.start()
    assert provider.entered.wait(timeout=1)
    second.start()
    time.sleep(0.03)
    assert provider.calls == 1
    provider.release.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not first.is_alive() and not second.is_alive()
    assert errors == []
    assert [result[0] for result in results] == [100, 100]
    assert all(result[1]["failed_real_sources"] == [] for result in results)
    assert provider.calls == 1
    assert chain._provider_circuit_until == {}
    assert chain._provider_inflight == {}


def test_different_symbols_do_not_share_or_block_provider_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINANCE_QUOTE_CACHE_TTL_SECONDS", "0")

    class Provider:
        name = "SYMBOL_PARALLEL"

        def __init__(self) -> None:
            self.barrier = threading.Barrier(2)

        def get_quote(self, symbol: str) -> Quote:
            self.barrier.wait(timeout=1)
            return Quote(symbol=symbol, price=100, source=f"SOURCE_{symbol}")

    chain = ProviderChain(providers=[Provider()])  # type: ignore[list-item]

    results = _run_threads(
        lambda: chain.get_quote("AAPL").source,
        lambda: chain.get_quote("MSFT").source,
    )

    assert set(results) == {"SOURCE_AAPL", "SOURCE_MSFT"}


def test_different_methods_do_not_share_or_block_provider_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINANCE_QUOTE_CACHE_TTL_SECONDS", "0")
    monkeypatch.setenv("FINANCE_HISTORY_CACHE_TTL_SECONDS", "0")

    class Provider:
        name = "METHOD_PARALLEL"

        def __init__(self) -> None:
            self.barrier = threading.Barrier(2)

        def get_quote(self, symbol: str) -> Quote:
            self.barrier.wait(timeout=1)
            return Quote(symbol=symbol, price=100, source=self.name)

        def get_history(self, symbol: str, period: str, interval: str) -> list[Candle]:
            self.barrier.wait(timeout=1)
            return [Candle("2026-08-11", 99, 101, 98, 100)]

    chain = ProviderChain(providers=[Provider()])  # type: ignore[list-item]

    results = _run_threads(
        lambda: chain.get_quote("AAPL").price,
        lambda: chain.get_history("AAPL", "1mo", "1d")[-1].close,
    )

    assert results == [100, 100]


def test_interleaved_request_deadlines_remain_thread_local() -> None:
    chain = ProviderChain(providers=[])
    chain.provider_timeout = 10.0
    chain.snapshot_timeout = 0.3
    first_entered = threading.Event()
    second_entered = threading.Event()
    first_exited = threading.Event()
    errors: list[BaseException] = []
    observed: list[float] = []

    def first() -> None:
        try:
            with chain.request_deadline():
                first_entered.set()
                assert second_entered.wait(timeout=1)
            first_exited.set()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def second() -> None:
        try:
            assert first_entered.wait(timeout=1)
            with chain.request_deadline():
                second_entered.set()
                assert first_exited.wait(timeout=1)
                observed.append(chain._operation_timeout_or_raise("get_quote"))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    first_thread = threading.Thread(target=first, daemon=True)
    second_thread = threading.Thread(target=second, daemon=True)
    first_thread.start()
    second_thread.start()
    first_thread.join(timeout=2)
    second_thread.join(timeout=2)

    assert errors == []
    assert len(observed) == 1
    assert 0 < observed[0] <= chain.snapshot_timeout


def test_short_snapshot_timeout_does_not_trip_provider_circuit_for_fresh_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINANCE_QUOTE_CACHE_TTL_SECONDS", "0")
    monkeypatch.setenv("FINANCE_PROVIDER_TIMEOUT_SECONDS", "0.2")
    monkeypatch.setenv("FINANCE_SNAPSHOT_TIMEOUT_SECONDS", "0.02")

    class Provider:
        name = "SNAPSHOT_LIMITED"

        def __init__(self) -> None:
            self.calls = 0
            self.first_finished = threading.Event()

        def get_quote(self, symbol: str) -> Quote:
            self.calls += 1
            if self.calls == 1:
                time.sleep(0.06)
                self.first_finished.set()
            return Quote(symbol=symbol, price=100, source=self.name)

    provider = Provider()
    chain = ProviderChain(providers=[provider])  # type: ignore[list-item]

    with chain.request_deadline(), pytest.raises(ProviderError):
        chain.get_quote("AAPL")

    assert chain._provider_circuit_until == {}
    assert provider.first_finished.wait(timeout=1)
    wait_deadline = time.monotonic() + 1
    while chain._provider_inflight and time.monotonic() < wait_deadline:
        time.sleep(0.001)
    assert chain._provider_inflight == {}
    assert chain.get_quote("AAPL").source == provider.name
    assert provider.calls == 2


def test_provider_reported_timeout_still_trips_inside_short_snapshot_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINANCE_QUOTE_CACHE_TTL_SECONDS", "0")
    monkeypatch.setenv("FINANCE_PROVIDER_TIMEOUT_SECONDS", "0.2")
    monkeypatch.setenv("FINANCE_SNAPSHOT_TIMEOUT_SECONDS", "0.02")

    class Provider:
        name = "PROVIDER_TIMEOUT"

        def get_quote(self, symbol: str) -> Quote:
            raise ProviderTimeoutError("provider transport timed out")

    provider = Provider()
    chain = ProviderChain(providers=[provider])  # type: ignore[list-item]

    with chain.request_deadline(), pytest.raises(ProviderError):
        chain.get_quote("AAPL")

    assert id(provider) in chain._provider_circuit_until


def test_circuit_check_and_trip_share_one_atomic_lock() -> None:
    class SlowAccessDict(dict[int, float]):
        def __init__(self) -> None:
            super().__init__()
            self._access_lock = threading.Lock()
            self._active = 0
            self.raced = False

        def _enter(self) -> None:
            with self._access_lock:
                self._active += 1
                self.raced = self.raced or self._active > 1
            time.sleep(0.001)

        def _exit(self) -> None:
            with self._access_lock:
                self._active -= 1

        def get(self, key: int, default: float | None = None) -> float | None:
            self._enter()
            try:
                return dict.get(self, key, default)
            finally:
                self._exit()

        def pop(self, key: int, default: float | None = None) -> float | None:
            self._enter()
            try:
                return dict.pop(self, key, default)
            finally:
                self._exit()

        def __setitem__(self, key: int, value: float) -> None:
            self._enter()
            try:
                dict.__setitem__(self, key, value)
            finally:
                self._exit()

    class Provider:
        name = "LOCKED_CIRCUIT"

    chain = ProviderChain(providers=[])
    state = SlowAccessDict()
    chain._provider_circuit_until = state
    provider = Provider()

    _run_threads(
        lambda: [chain._trip_circuit(provider) for _ in range(20)],  # type: ignore[arg-type]
        lambda: [chain._circuit_error(provider) for _ in range(20)],  # type: ignore[arg-type]
    )

    assert state.raced is False


def test_concurrent_cache_and_fresh_coverage_keep_request_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINANCE_QUOTE_CACHE_TTL_SECONDS", "60")

    class Provider:
        name = "BY_SYMBOL"

        def get_quote(self, symbol: str) -> Quote:
            return Quote(symbol=symbol, price=100, source=f"SOURCE_{symbol}")

    chain = ProviderChain(providers=[Provider()])  # type: ignore[list-item]
    chain.get_quote("AAPL")
    after_fetch = threading.Barrier(2)

    def fetch(symbol: str) -> dict[str, Any]:
        with chain.request_deadline():
            chain.get_quote(symbol)
            after_fetch.wait(timeout=1)
            return chain.source_coverage("get_quote")

    aapl, msft = _run_threads(lambda: fetch("AAPL"), lambda: fetch("MSFT"))

    assert aapl["selected_source"] == "SOURCE_AAPL"
    assert aapl["cache_hit"] is True
    assert msft["selected_source"] == "SOURCE_MSFT"
    assert msft["cache_hit"] is False


def test_copy_context_coverage_updates_are_copy_on_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINANCE_QUOTE_CACHE_TTL_SECONDS", "0")

    class Provider:
        name = "CONTEXT_SOURCE"

        def get_quote(self, symbol: str) -> Quote:
            return Quote(symbol=symbol, price=100, source=f"SOURCE_{symbol}")

    chain = ProviderChain(providers=[Provider()])  # type: ignore[list-item]
    chain.get_quote("AAPL")
    copied = copy_context()

    def fetch_in_copy() -> dict[str, Any]:
        chain.get_quote("MSFT")
        return chain.source_coverage("get_quote")

    copied_coverage = copied.run(fetch_in_copy)
    original_coverage = chain.source_coverage("get_quote")

    assert copied_coverage["selected_source"] == "SOURCE_MSFT"
    assert original_coverage["selected_source"] == "SOURCE_AAPL"


def test_concurrent_snapshots_keep_all_source_provenance_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "FINANCE_QUOTE_CACHE_TTL_SECONDS",
        "FINANCE_HISTORY_CACHE_TTL_SECONDS",
        "FINANCE_FINANCIALS_CACHE_TTL_SECONDS",
        "FINANCE_NEWS_CACHE_TTL_SECONDS",
    ):
        monkeypatch.setenv(name, "0")
    barriers = {method: threading.Barrier(2) for method in (
        "get_quote",
        "get_history",
        "get_financials",
        "get_news",
    )}

    class Provider:
        news_is_symbol_scoped = True

        def __init__(self, name: str, symbol: str) -> None:
            self.name = name
            self.symbol = symbol

        def supports(self, method: str, symbol: str, *args: object) -> bool:
            return symbol == self.symbol

        def get_quote(self, symbol: str) -> Quote:
            barriers["get_quote"].wait(timeout=1)
            return Quote(symbol=symbol, price=100, source=self.name)

        def get_history(self, symbol: str, period: str, interval: str) -> list[Candle]:
            barriers["get_history"].wait(timeout=1)
            return [Candle("2026-08-11", 99, 101, 98, 100)]

        def get_financials(self, symbol: str) -> Financials:
            barriers["get_financials"].wait(timeout=1)
            return Financials(symbol=symbol, revenue=100, source=self.name)

        def get_news(self, symbol: str, limit: int) -> list[NewsItem]:
            barriers["get_news"].wait(timeout=1)
            return [NewsItem(title=f"{symbol} update", source=self.name)]

    chain = ProviderChain(providers=[  # type: ignore[list-item]
        Provider("SOURCE_A", "AAPL"),
        Provider("SOURCE_B", "MSFT"),
    ])
    agent = FinanceResearchAgent(provider=chain)

    aapl, msft = _run_threads(
        lambda: agent.snapshot("AAPL"),
        lambda: agent.snapshot("MSFT"),
    )

    for method in ("get_quote", "get_history", "get_financials", "get_news"):
        assert aapl.source_coverage[method]["successful_real_sources"] == ["SOURCE_A"]
        assert msft.source_coverage[method]["successful_real_sources"] == ["SOURCE_B"]


def test_support_failures_are_recorded_without_aborting_other_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINANCE_QUOTE_CACHE_TTL_SECONDS", "0")

    class BrokenSupports:
        name = "BROKEN_SUPPORTS"

        def supports(self, method: str, *args: object) -> bool:
            raise RuntimeError("supports exploded")

    class BrokenCapabilities:
        name = "BROKEN_CAPABILITIES"

        @property
        def capabilities(self) -> set[str]:
            raise RuntimeError("capabilities exploded")

    class Healthy:
        name = "HEALTHY"

        def get_quote(self, symbol: str) -> Quote:
            return Quote(symbol=symbol, price=100, source=self.name)

    chain = ProviderChain(  # type: ignore[list-item]
        providers=[BrokenSupports(), BrokenCapabilities(), Healthy()]
    )

    quote = chain.get_quote("AAPL")
    failures = chain.source_coverage("get_quote")["failed_real_sources"]

    assert quote.source == "HEALTHY"
    assert failures == [
        {"name": "BROKEN_SUPPORTS", "error": "supports exploded"},
        {"name": "BROKEN_CAPABILITIES", "error": "capabilities exploded"},
    ]


def test_explicit_provider_injection_never_constructs_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called(sample_fallback: bool) -> Any:
        raise AssertionError(f"default construction called: {sample_fallback}")

    monkeypatch.setattr(chain_module, "build_default_providers", fail_if_called)

    chain = ProviderChain(providers=[])

    assert chain.providers == []
    assert chain.diagnostics() == []


def test_chain_close_only_closes_default_owned_providers_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Closable:
        name = "CLOSABLE"

        def __init__(self) -> None:
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    active = Closable()
    disabled = Closable()
    injected = Closable()
    monkeypatch.setattr(
        chain_module,
        "build_default_providers",
        lambda sample_fallback: ([active], [], [disabled, active]),
    )

    owned_chain = ProviderChain()
    injected_chain = ProviderChain(providers=[injected])  # type: ignore[list-item]
    owned_chain.close()
    owned_chain.close()
    injected_chain.close()

    assert active.close_calls == 1
    assert disabled.close_calls == 1
    assert injected.close_calls == 0


def test_chain_close_retries_only_failed_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Healthy:
        name = "HEALTHY_CLOSE"

        def __init__(self) -> None:
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    class Flaky(Healthy):
        name = "FLAKY_CLOSE"

        def close(self) -> None:
            self.close_calls += 1
            if self.close_calls == 1:
                raise RuntimeError("try again")

    healthy = Healthy()
    flaky = Flaky()
    monkeypatch.setattr(
        chain_module,
        "build_default_providers",
        lambda sample_fallback: ([healthy], [], [flaky, healthy]),
    )
    chain = ProviderChain()

    with pytest.raises(ProviderError, match="FLAKY_CLOSE: try again"):
        chain.close()

    assert healthy.close_calls == 1
    assert flaky.close_calls == 1
    assert chain._closed is False
    chain.close()
    chain.close()
    assert healthy.close_calls == 1
    assert flaky.close_calls == 2
    assert chain._closed is True


def test_concurrent_chain_close_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BlockingResource:
        name = "BLOCKING_CLOSE"

        def __init__(self) -> None:
            self.close_calls = 0
            self.entered = threading.Event()
            self.release = threading.Event()

        def close(self) -> None:
            self.close_calls += 1
            self.entered.set()
            assert self.release.wait(timeout=1)

    resource = BlockingResource()
    monkeypatch.setattr(
        chain_module,
        "build_default_providers",
        lambda sample_fallback: ([resource], [], [resource]),
    )
    chain = ProviderChain()
    errors: list[BaseException] = []

    def close() -> None:
        try:
            chain.close()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    first = threading.Thread(target=close, daemon=True)
    second = threading.Thread(target=close, daemon=True)
    first.start()
    assert resource.entered.wait(timeout=1)
    second.start()
    time.sleep(0.03)
    assert resource.close_calls == 1
    resource.release.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert errors == []
    assert not first.is_alive() and not second.is_alive()
    assert resource.close_calls == 1


def test_builtin_resource_providers_expose_close() -> None:
    class Resource:
        def __init__(self) -> None:
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    alpha_resource = Resource()
    yahoo_resource = Resource()
    tushare_resource = Resource()
    alpha = object.__new__(AlphaVantageProvider)
    yahoo = object.__new__(YahooFinanceProvider)
    tushare = object.__new__(TushareProvider)
    alpha.client = alpha_resource  # type: ignore[assignment]
    yahoo.client = yahoo_resource  # type: ignore[assignment]
    tushare._pro = tushare_resource

    alpha.close()
    yahoo.close()
    tushare.close()

    assert alpha_resource.close_calls == 1
    assert yahoo_resource.close_calls == 1
    assert tushare_resource.close_calls == 1
    assert tushare._pro is None


def test_direct_quote_cache_hit_adds_current_ttl_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINANCE_QUOTE_CACHE_TTL_SECONDS", "60")

    class Provider:
        name = "CACHE_NOTE"

        def get_quote(self, symbol: str) -> Quote:
            return Quote(symbol=symbol, price=100, source=self.name)

    chain = ProviderChain(providers=[Provider()])  # type: ignore[list-item]
    chain.get_quote("AAPL")

    cached = chain.get_quote("AAPL")

    assert any("使用 TTL 缓存" in note and "缓存年龄" in note for note in cached.notes)


def test_direct_financials_cache_hit_adds_current_ttl_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINANCE_FINANCIALS_CACHE_TTL_SECONDS", "60")

    class Provider:
        name = "CACHE_NOTE"

        def get_financials(self, symbol: str) -> Financials:
            return Financials(symbol=symbol, revenue=100, source=self.name)

    chain = ProviderChain(providers=[Provider()])  # type: ignore[list-item]
    chain.get_financials("AAPL")

    cached = chain.get_financials("AAPL")

    assert any("使用 TTL 缓存" in note and "缓存年龄" in note for note in cached.notes)
