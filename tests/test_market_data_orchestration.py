from __future__ import annotations

import threading

from ticker_dossier.market_data.providers.normalization import _compact_provider_error
from ticker_dossier.market_data import SampleDataProvider
from ticker_dossier.market_data import cache, configuration, coverage, execution, selection
from ticker_dossier.market_data.chain import ProviderChain
from ticker_dossier.market_data.serialization import export_history_csv
from ticker_dossier.market_data.models import Financials, Quote
from ticker_dossier.runtime.context import redact_sensitive_text as runtime_redact_sensitive_text
from ticker_dossier.security import redact_sensitive_text


def test_market_data_orchestration_lives_under_one_package_tree() -> None:
    modules = {
        ProviderChain.__module__,
        execution._collect_provider_calls.__module__,
        coverage._coverage_error.__module__,
        selection._dedupe_news.__module__,
        configuration._positive_env_float.__module__,
        export_history_csv.__module__,
    }

    assert all(name.startswith("ticker_dossier.market_data") for name in modules)


def test_provider_error_compaction_uses_central_secret_redaction_before_truncation() -> None:
    secrets = {
        "apikey": "alpha-provider-key-123456",
        "api_key": "underscore-provider-key-123456",
        "token": "provider-token-123456789",
        "secret": "provider-secret-123456789",
        "password": "provider-password-123456789",
        "cookie": "provider-cookie-123456789",
    }
    assert runtime_redact_sensitive_text is redact_sensitive_text

    for label, secret in secrets.items():
        error = RuntimeError("x" * 120 + f" https://provider.test/query?{label}={secret}&symbol=AAPL")
        compact = _compact_provider_error(error)

        assert secret not in compact
        assert "[REDACTED_SECRET]" in compact

    standalone = "sk-providersecret1234567890"
    compact = _compact_provider_error(RuntimeError(f"provider failed with {standalone}"))
    assert standalone not in compact
    assert "[REDACTED_SECRET]" in compact


def test_cache_detaches_values_restores_coverage_and_expires_deterministically() -> None:
    ttls = {"get_quote": 60.0}
    store: cache.CacheStore = {}
    lock = threading.Lock()
    coverage_by_method: dict[str, dict[str, object]] = {}
    coverage.record_coverage(
        coverage_by_method,
        "get_quote",
        successful_real_sources=["REAL"],
        failed_real_sources=[],
        selected_source="REAL",
        sample_used=False,
    )
    original = Quote(symbol="AAPL", price=100, source="REAL", notes=["provider note"])
    cache.set_cached(
        "get_quote",
        ("AAPL",),
        original,
        ttls=ttls,
        cache=store,
        cache_lock=lock,
        coverage_by_method=coverage_by_method,
        now=10.0,
    )

    original.notes.append("caller mutation")
    cached = cache.get_cached(
        "get_quote",
        ("AAPL",),
        ttls=ttls,
        cache=store,
        cache_lock=lock,
        coverage_by_method=coverage_by_method,
        now=12.5,
    )

    assert cached.notes == ["provider note"]
    assert coverage_by_method["get_quote"]["cache_hit"] is True
    assert coverage_by_method["get_quote"]["cache_age_seconds"] == 2.5
    assert cache.get_cached(
        "get_quote",
        ("AAPL",),
        ttls=ttls,
        cache=store,
        cache_lock=lock,
        coverage_by_method=coverage_by_method,
        now=70.0,
    ) is None
    assert store == {}


def test_provider_partition_respects_support_sample_and_open_circuit() -> None:
    class Provider:
        def __init__(self, name: str, supported: bool = True) -> None:
            self.name = name
            self.supported = supported

        def supports(self, method: str, *args: object) -> bool:
            return self.supported

    unsupported = Provider("UNSUPPORTED", supported=False)
    blocked = Provider("BLOCKED")
    runnable = Provider("RUNNABLE")
    sample = SampleDataProvider()

    real, samples, failures = execution.partition_providers(
        [unsupported, blocked, runnable, sample],  # type: ignore[list-item]
        "get_quote",
        ("AAPL",),
        lambda provider: "circuit open" if provider.name == "BLOCKED" else "",
    )

    assert real == [runnable]
    assert samples == [sample]
    assert failures == [{"name": "BLOCKED", "error": "circuit open"}]


def test_circuit_helpers_have_a_deterministic_cooldown_boundary() -> None:
    class Provider:
        name = "SLOW"

    provider = Provider()
    open_until: dict[int, float] = {}

    execution.trip_circuit(open_until, provider, 5.0, now=10.0)  # type: ignore[arg-type]

    assert execution.circuit_error(open_until, provider, now=12.0) == (  # type: ignore[arg-type]
        "temporarily skipped after timeout (3.0s cooldown remaining)"
    )
    assert execution.circuit_error(open_until, provider, now=15.0) == ""  # type: ignore[arg-type]
    assert open_until == {}


def test_selection_only_merges_compatible_quote_and_financial_fields() -> None:
    primary = Quote(
        symbol="AAPL",
        currency="USD",
        price=101,
        source="FRESH",
        as_of="2026-08-11",
        is_realtime=True,
    )
    incompatible = Quote(
        symbol="AAPL",
        currency="CNY",
        price=100,
        market_cap=999,
        source="VALUATION",
        as_of="2026-08-10",
    )

    selected_name, quote, spread = selection.select_quote(
        [("FRESH", primary), ("VALUATION", incompatible)]
    )

    assert selected_name == "FRESH"
    assert quote.market_cap is None
    assert quote.price == 101
    assert spread == 1.0
    assert quote is not primary

    annual_usd = Financials(
        symbol="AAPL",
        source="ANNUAL_USD",
        currency="USD",
        as_of="2025-12-31",
        period_type="annual",
        revenue=100,
    )
    ttm_cny = Financials(
        symbol="AAPL",
        source="TTM_CNY",
        currency="CNY",
        as_of="2026-03-31",
        period_type="TTM",
        free_cash_flow=10,
    )

    merged, sources, _ = selection.merge_financials(
        [("ANNUAL_USD", annual_usd), ("TTM_CNY", ttm_cny)]
    )

    assert sources == ["ANNUAL_USD", "TTM_CNY"]
    assert merged.free_cash_flow is None
    assert any("未合并 TTM_CNY" in note for note in merged.notes)


def test_coverage_access_is_defensive_and_notes_remain_stable() -> None:
    rows: dict[str, dict[str, object]] = {}
    coverage.record_coverage(
        rows,
        "get_quote",
        successful_real_sources=["A", "A", "B"],
        failed_real_sources=[{"name": "C", "error": "unavailable"}],
        selected_source="A",
        sample_used=False,
        price_spread_pct=2.5,
    )

    detached = coverage.source_coverage(rows, "get_quote")
    detached["successful_real_sources"].append("MUTATED")

    assert rows["get_quote"]["successful_real_sources"] == ["A", "B"]
    assert coverage.report_notes(rows, "get_quote") == [
        "行情真实来源覆盖: A、B。",
        "行情来源失败: C: unavailable。",
        "跨源行情最大差异: 2.50%。",
    ]
