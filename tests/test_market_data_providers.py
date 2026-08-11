from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import time

import httpx
import pytest
import ticker_dossier.market_data as market_data_api

from ticker_dossier.market_data import (
    AKShareProvider,
    AlphaVantageProvider,
    MarketDataProvider,
    ProviderError,
    ProviderTimeoutError,
    SampleDataProvider,
    TushareProvider,
    YahooFinanceProvider,
)
from ticker_dossier.market_data import ProviderChain
from ticker_dossier.market_data.models import Quote


def test_market_data_package_is_safe_as_the_first_research_related_import() -> None:
    source_root = Path(__file__).parents[1]
    code = (
        "import sys; "
        f"sys.path.insert(0, {str(source_root)!r}); "
        "from ticker_dossier.market_data import SampleDataProvider; "
        "from ticker_dossier.research import FinanceResearchAgent"
    )

    subprocess.run([sys.executable, "-I", "-c", code], check=True)


def test_market_data_package_reexports_provider_contracts_by_identity() -> None:
    expected = {
        "MarketDataProvider": MarketDataProvider,
        "ProviderError": ProviderError,
        "ProviderTimeoutError": ProviderTimeoutError,
        "AlphaVantageProvider": AlphaVantageProvider,
        "YahooFinanceProvider": YahooFinanceProvider,
        "TushareProvider": TushareProvider,
        "AKShareProvider": AKShareProvider,
        "SampleDataProvider": SampleDataProvider,
    }

    for name, exported in expected.items():
        assert getattr(market_data_api, name) is exported


def test_concrete_adapters_satisfy_the_runtime_provider_contract() -> None:
    providers = [
        AlphaVantageProvider(api_key="test"),
        YahooFinanceProvider(),
        TushareProvider(token="test"),
        AKShareProvider(),
        SampleDataProvider(),
    ]

    assert all(isinstance(provider, MarketDataProvider) for provider in providers)
    assert all(provider.name for provider in providers)


def test_provider_selection_respects_structural_support_checks() -> None:
    class UnsupportedProvider:
        name = "UNSUPPORTED"

        def supports(self, method: str, symbol: str, *args: object) -> bool:
            return False

        def get_quote(self, symbol: str) -> Quote:
            raise AssertionError("an unsupported adapter must not be called")

    class SupportedProvider:
        name = "SUPPORTED"

        def get_quote(self, symbol: str) -> Quote:
            return Quote(symbol=symbol, price=101, source=self.name, is_realtime=True)

    chain = ProviderChain(providers=[UnsupportedProvider(), SupportedProvider()])

    assert chain.get_quote("aapl").source == "SUPPORTED"
    assert chain.source_coverage("get_quote")["successful_real_sources"] == ["SUPPORTED"]


def test_provider_http_error_redacts_api_key_from_coverage_and_final_error() -> None:
    secret = "provider-query-secret-123456789"

    class LeakyProvider:
        name = "LEAKY"

        def get_quote(self, symbol: str) -> Quote:
            request = httpx.Request(
                "GET",
                f"https://provider.test/query?function=quote&apikey={secret}&symbol={symbol}",
            )
            httpx.Response(401, request=request).raise_for_status()
            raise AssertionError("raise_for_status must fail")

    chain = ProviderChain(providers=[LeakyProvider()])

    with pytest.raises(ProviderError) as captured:
        chain.get_quote("AAPL")

    coverage = chain.source_coverage("get_quote")
    coverage_error = coverage["failed_real_sources"][0]["error"]
    final_error = str(captured.value)
    assert secret not in coverage_error
    assert secret not in final_error
    assert "[REDACTED_SECRET]" in coverage_error
    assert "[REDACTED_SECRET]" in final_error


def test_quote_cache_returns_independent_values_and_preserves_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINANCE_QUOTE_CACHE_TTL_SECONDS", "60")

    class CountingProvider:
        name = "COUNTING"

        def __init__(self) -> None:
            self.calls = 0

        def get_quote(self, symbol: str) -> Quote:
            self.calls += 1
            return Quote(symbol=symbol, price=100, source=self.name, notes=["original"])

    provider = CountingProvider()
    chain = ProviderChain(providers=[provider])

    first = chain.get_quote("AAPL")
    first.notes.append("caller mutation")
    second = chain.get_quote("AAPL")

    assert provider.calls == 1
    assert "caller mutation" not in second.notes
    assert chain.source_coverage("get_quote")["cache_hit"] is True


def test_timeout_trips_circuit_and_next_call_skips_the_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINANCE_PROVIDER_TIMEOUT_SECONDS", "0.01")
    monkeypatch.setenv("FINANCE_PROVIDER_COOLDOWN_SECONDS", "60")
    monkeypatch.setenv("FINANCE_QUOTE_CACHE_TTL_SECONDS", "0")

    class SlowProvider:
        name = "SLOW"

        def __init__(self) -> None:
            self.calls = 0

        def get_quote(self, symbol: str) -> Quote:
            self.calls += 1
            time.sleep(0.1)
            return Quote(symbol=symbol, price=99, source=self.name)

    class FastProvider:
        name = "FAST"

        def get_quote(self, symbol: str) -> Quote:
            return Quote(symbol=symbol, price=100, source=self.name, is_realtime=True)

    slow = SlowProvider()
    chain = ProviderChain(providers=[slow, FastProvider()])

    assert chain.get_quote("AAPL").source == "FAST"
    assert chain.get_quote("AAPL").source == "FAST"

    assert slow.calls == 1
    failure = chain.source_coverage("get_quote")["failed_real_sources"][0]
    assert failure["name"] == "SLOW"
    assert "temporarily skipped after timeout" in failure["error"]
