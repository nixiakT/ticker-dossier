from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import sys
import time
from types import SimpleNamespace

import pandas as pd
import pytest

from ticker_dossier.market_data import (
    AKShareProvider,
    ProviderError,
    SampleDataProvider,
    TushareProvider,
    YahooFinanceProvider,
)
from ticker_dossier.market_data.providers.normalization import _news_keywords, _news_matches
from ticker_dossier.research.agent import FinanceResearchAgent
from ticker_dossier.market_data import ProviderChain, enrich_financial_pe
from ticker_dossier.market_data.models import Candle, Financials, NewsItem, Quote, StockSnapshot
from ticker_dossier.research.analysis.quality import evaluate_quality_gate, render_quality_screen
from ticker_dossier.research.discovery.resolver import SymbolCandidate, resolve_symbol, resolve_symbol_text
from ticker_dossier.market_data.symbols import extract_symbols


def test_english_company_name_is_not_treated_as_a_literal_ticker(monkeypatch: pytest.MonkeyPatch) -> None:
    import ticker_dossier.research.discovery.resolver as resolver

    monkeypatch.setattr(
        resolver,
        "_eastmoney_candidates",
        lambda query: [SymbolCandidate("APLE", "Apple Hospitality REIT, Inc.", "美股", "Eastmoney suggest")],
    )
    monkeypatch.setattr(
        resolver,
        "_yahoo_candidates",
        lambda query: [SymbolCandidate("AAPL", "Apple Inc.", "NMS", "Yahoo Finance search")],
    )
    monkeypatch.setattr(resolver, "_akshare_candidates", lambda query: [])
    monkeypatch.setattr(resolver, "_web_candidates", lambda query: [])

    assert extract_symbols("Apple stock looks good") == ["AAPL"]
    assert extract_symbols("This company looks great") == []
    assert resolve_symbol("Apple").symbol == "AAPL"
    assert resolve_symbol_text("Apple").splitlines()[1].startswith("1. AAPL")


def test_symbol_extraction_rejects_prompt_words_and_years() -> None:
    assert extract_symbols("I THINK AAPL IS GREAT") == ["AAPL"]
    assert extract_symbols("THIS STOCK WILL RISE") == []
    assert extract_symbols("AAPL 2026 outlook") == ["AAPL"]
    assert extract_symbols("比较 2025 和 2026 股票") == []
    assert extract_symbols("分析 $JPM 和 $BAC") == ["JPM", "BAC"]


def test_unrelated_search_candidate_is_not_auto_selected(monkeypatch: pytest.MonkeyPatch) -> None:
    import ticker_dossier.research.discovery.resolver as resolver

    monkeypatch.setattr(resolver, "_eastmoney_candidates", lambda query: [])
    monkeypatch.setattr(resolver, "_yahoo_candidates", lambda query: [])
    monkeypatch.setattr(resolver, "_akshare_candidates", lambda query: [])
    monkeypatch.setattr(
        resolver,
        "_web_candidates",
        lambda query: [SymbolCandidate("AAPL", "", "US", "web search")],
    )

    with pytest.raises(LookupError, match="无法可靠解析"):
        resolve_symbol("Blue Ocean Robotics")


def test_failed_news_fetch_is_not_counted_as_a_news_item() -> None:
    agent = FinanceResearchAgent(provider=ProviderChain(providers=[NewsFailingProvider()]))

    snapshot = agent.snapshot("AAPL", period="1mo", news_limit=5)
    screen = render_quality_screen(snapshot)

    assert snapshot.news == []
    assert any("新闻获取失败" in note for note in snapshot.quote.notes)
    assert "| 新闻/公告 | 待补充 | 0 条近期真实来源 |" in screen


def test_news_keywords_drop_generic_company_words() -> None:
    keywords = _news_keywords("02513.HK", "2513.HK", "Zhipu Technology Group Inc.")

    assert "zhipu" in keywords
    assert not {"tech", "technology", "group", "inc"}.intersection(keywords)
    assert not _news_matches(
        {"title": "Rocket Lab launches new space technology", "publisher": "Reuters"},
        keywords,
    )


def test_news_matching_uses_token_boundaries_for_one_letter_tickers() -> None:
    keywords = _news_keywords("T", "T")

    assert _news_matches({"title": "AT&T (T) raises guidance"}, keywords)
    assert not _news_matches({"title": "Apple launches another product"}, keywords)


def test_news_publisher_or_url_cannot_create_relevance_alone() -> None:
    assert not _news_matches(
        {"title": "Oil prices rise", "publisher": "Fox Business", "link": "https://example.com/fox"},
        ["fox"],
    )
    assert not _news_matches(
        {"title": "Unrelated market update", "link": "https://example.com/article/2513"},
        ["2513"],
    )


def test_quote_coverage_cross_checks_real_sources_and_excludes_sample() -> None:
    chain = ProviderChain(
        providers=[
            QuoteFailingProvider(),
            PricedProvider("REAL_A", 100.0),
            PricedProvider("REAL_B", 102.0),
            SampleDataProvider(),
        ]
    )

    quote = chain.get_quote("AAPL")
    coverage = chain.source_coverage("get_quote")

    assert quote.source == "REAL_A"
    assert coverage["successful_real_sources"] == ["REAL_A", "REAL_B"]
    assert coverage["failed_real_sources"] == [{"name": "QUOTE_FAIL", "error": "quote failed"}]
    assert coverage["sample_used"] is False
    assert coverage["price_spread_pct"] == pytest.approx(2.0)
    assert any("跨源行情最大差异" in note for note in quote.notes)


def test_quote_cross_check_selects_the_fresh_realtime_result() -> None:
    stale = PricedProvider("STALE", 100.0, as_of="2026-01-01", is_realtime=False)
    fresh = PricedProvider("FRESH", 101.0, as_of="2026-01-02", is_realtime=True)
    chain = ProviderChain(providers=[stale, fresh])

    quote = chain.get_quote("AAPL")

    assert quote.source == "FRESH"
    assert chain.source_coverage("get_quote")["selected_source"] == "FRESH"


def test_quote_cross_check_fills_missing_valuation_without_replacing_primary_price() -> None:
    class DetailedQuoteProvider:
        def __init__(
            self,
            name: str,
            price: float,
            *,
            as_of: str,
            is_realtime: bool,
            market_cap: float | None = None,
            pe_ratio: float | None = None,
        ) -> None:
            self.name = name
            self.price = price
            self.as_of = as_of
            self.is_realtime = is_realtime
            self.market_cap = market_cap
            self.pe_ratio = pe_ratio

        def get_quote(self, symbol: str) -> Quote:
            return Quote(
                symbol=symbol,
                currency="CNY",
                price=self.price,
                market_cap=self.market_cap,
                pe_ratio=self.pe_ratio,
                source=self.name,
                as_of=self.as_of,
                is_realtime=self.is_realtime,
            )

    valuation = DetailedQuoteProvider(
        "AKSHARE",
        100,
        as_of="2026-07-13",
        is_realtime=False,
        market_cap=1_500_000_000_000,
        pe_ratio=20,
    )
    primary = DetailedQuoteProvider("TUSHARE", 101, as_of="2026-07-14", is_realtime=True)
    chain = ProviderChain(providers=[valuation, primary])

    quote = chain.get_quote("600519")
    coverage = chain.source_coverage("get_quote")

    assert quote.source == "TUSHARE"
    assert quote.price == 101
    assert quote.market_cap == 1_500_000_000_000
    assert quote.pe_ratio == 20
    assert quote.field_sources["price"] == "TUSHARE"
    assert quote.field_sources["market_cap"] == "AKSHARE"
    assert quote.field_sources["pe_ratio"] == "AKSHARE"
    assert coverage["field_sources"]["market_cap"] == "AKSHARE"


def test_quote_cross_check_does_not_replace_primary_valuation() -> None:
    primary = PricedProvider("PRIMARY", 101, as_of="2026-07-14", is_realtime=True)
    supplemental = PricedProvider("SUPPLEMENTAL", 99, as_of="2026-07-13", is_realtime=False)
    primary.get_quote = lambda symbol: Quote(  # type: ignore[method-assign]
        symbol=symbol,
        price=101,
        market_cap=2_000,
        pe_ratio=30,
        source=primary.name,
        as_of=primary.as_of,
        is_realtime=True,
    )
    supplemental.get_quote = lambda symbol: Quote(  # type: ignore[method-assign]
        symbol=symbol,
        price=99,
        market_cap=1_000,
        pe_ratio=20,
        source=supplemental.name,
        as_of=supplemental.as_of,
    )

    quote = ProviderChain(providers=[supplemental, primary]).get_quote("AAPL")

    assert quote.market_cap == 2_000
    assert quote.pe_ratio == 30
    assert quote.field_sources["market_cap"] == "PRIMARY"
    assert quote.field_sources["pe_ratio"] == "PRIMARY"


def test_yahoo_financials_reports_primary_and_fallback_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailedResponse:
        def raise_for_status(self) -> None:
            raise RuntimeError("401 crumb required")

    provider = YahooFinanceProvider()
    provider.client = SimpleNamespace(get=lambda *args, **kwargs: FailedResponse())

    def fail_fallback(symbol: str) -> dict[str, object]:
        raise ProviderError("fallback returned empty info")

    monkeypatch.setattr(provider, "_yfinance_info", fail_fallback)

    with pytest.raises(ProviderError) as exc_info:
        provider.get_financials("NVDA")

    message = str(exc_info.value)
    assert "quoteSummary failed" in message
    assert "401 crumb required" in message
    assert "yfinance fallback failed" in message
    assert "fallback returned empty info" in message


def test_yfinance_fallback_uses_finance_proxy_and_returns_supported_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured: dict[str, str] = {}

    def set_config(**kwargs: str) -> None:
        configured.update(kwargs)

    fake_yfinance = SimpleNamespace(
        set_config=set_config,
        Ticker=lambda symbol: SimpleNamespace(info={
            "trailingPE": 42.5,
            "trailingEps": 4.98,
            "financialCurrency": "USD",
        }),
    )
    monkeypatch.setenv("FINANCE_HTTP_PROXY", "http://127.0.0.1:7897")
    monkeypatch.setitem(sys.modules, "yfinance", fake_yfinance)

    result = YahooFinanceProvider()._yfinance_info("NVDA")

    assert configured == {"proxy": "http://127.0.0.1:7897"}
    assert result["trailingPE"] == 42.5
    assert result["trailingEps"] == 4.98


def test_missing_pe_is_derived_from_compatible_fresh_price_and_annual_eps() -> None:
    report_date = (datetime.now(UTC) - timedelta(days=120)).date().isoformat()
    quote = Quote(
        symbol="NVDA",
        currency="USD",
        price=211.8,
        source="Yahoo Finance public endpoints",
        as_of="2026-07-14T20:00:00Z",
        field_sources={"price": "Yahoo Finance public endpoints"},
    )
    financials = Financials(
        symbol="NVDA",
        source="AKShare",
        as_of=report_date,
        currency="美元",
        period_type="ANNUAL",
        eps=4.93,
        field_sources={"eps": "AKShare"},
    )

    changed = enrich_financial_pe(financials, quote)

    assert changed is True
    assert financials.pe_ratio == pytest.approx(211.8 / 4.93, rel=1e-4)
    assert financials.forward_pe is None
    assert financials.field_sources["pe_ratio"].startswith("DERIVED:")
    assert any("程序推导值，不是数据商直接报告的 PE" in note for note in financials.notes)


def test_provider_chain_uses_an_already_cached_quote_for_derived_pe() -> None:
    report_date = (datetime.now(UTC) - timedelta(days=120)).date().isoformat()

    class ValuationFallbackProvider:
        name = "REAL_PROVIDER"

        def get_quote(self, symbol: str) -> Quote:
            return Quote(
                symbol=symbol,
                currency="USD",
                price=211.8,
                source="REAL_QUOTE",
                as_of="2026-07-14T20:00:00Z",
            )

        def get_financials(self, symbol: str) -> Financials:
            return Financials(
                symbol=symbol,
                source="REAL_FINANCIALS",
                as_of=report_date,
                currency="USD",
                period_type="ANNUAL",
                eps=4.93,
            )

    chain = ProviderChain(providers=[ValuationFallbackProvider()])
    chain.get_quote("NVDA")

    financials = chain.get_financials("NVDA")

    assert financials.pe_ratio == pytest.approx(211.8 / 4.93, rel=1e-4)
    assert financials.field_sources["pe_ratio"].startswith("DERIVED:")
    assert chain.source_coverage("get_financials")["field_sources"]["pe_ratio"].startswith("DERIVED:")


def test_cached_financials_gain_derived_pe_after_a_quote_becomes_available() -> None:
    report_date = (datetime.now(UTC) - timedelta(days=120)).date().isoformat()

    class ValuationFallbackProvider:
        name = "REAL_PROVIDER"

        def get_quote(self, symbol: str) -> Quote:
            return Quote(symbol=symbol, currency="USD", price=211.8, source="REAL_QUOTE")

        def get_financials(self, symbol: str) -> Financials:
            return Financials(
                symbol=symbol,
                source="REAL_FINANCIALS",
                as_of=report_date,
                currency="USD",
                period_type="ANNUAL",
                eps=4.93,
            )

    chain = ProviderChain(providers=[ValuationFallbackProvider()])
    assert chain.get_financials("NVDA").pe_ratio is None
    chain.get_quote("NVDA")

    financials = chain.get_financials("NVDA")

    assert financials.pe_ratio == pytest.approx(211.8 / 4.93, rel=1e-4)
    assert chain.source_coverage("get_financials")["field_sources"]["pe_ratio"].startswith("DERIVED:")


@pytest.mark.parametrize(
    ("eps", "currency", "period_type", "as_of", "financial_source"),
    [
        (-1.0, "USD", "ANNUAL", "2026-01-25", "AKShare"),
        (4.93, "CNY", "ANNUAL", "2026-01-25", "AKShare"),
        (4.93, "USD", "REPORTED", "2026-01-25", "AKShare"),
        (4.93, "USD", "ANNUAL", "2020-01-01", "AKShare"),
        (4.93, "USD", "ANNUAL", "2026-01-25", "SAMPLE_FALLBACK"),
    ],
)
def test_missing_pe_is_not_derived_from_incompatible_or_weak_evidence(
    eps: float,
    currency: str,
    period_type: str,
    as_of: str,
    financial_source: str,
) -> None:
    quote = Quote(symbol="NVDA", currency="USD", price=211.8, source="Yahoo Finance public endpoints")
    financials = Financials(
        symbol="NVDA",
        source=financial_source,
        as_of=as_of,
        currency=currency,
        period_type=period_type,
        eps=eps,
    )

    assert enrich_financial_pe(financials, quote) is False
    assert financials.pe_ratio is None
    assert "pe_ratio" not in financials.field_sources


def test_quote_cache_reuses_results_and_returns_independent_copies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINANCE_QUOTE_CACHE_TTL_SECONDS", "60")
    provider = PricedProvider("COUNTED", 100)
    calls = 0
    original = provider.get_quote

    def counted(symbol: str) -> Quote:
        nonlocal calls
        calls += 1
        return original(symbol)

    provider.get_quote = counted  # type: ignore[method-assign]
    chain = ProviderChain(providers=[provider])

    first = chain.get_quote("AAPL")
    first.price = 999
    second = chain.get_quote("AAPL")

    assert calls == 1
    assert second.price == 100
    assert chain.source_coverage("get_quote")["cache_hit"] is True


def test_quote_cache_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINANCE_QUOTE_CACHE_TTL_SECONDS", "0")
    provider = PricedProvider("COUNTED", 100)
    calls = 0
    original = provider.get_quote

    def counted(symbol: str) -> Quote:
        nonlocal calls
        calls += 1
        return original(symbol)

    provider.get_quote = counted  # type: ignore[method-assign]
    chain = ProviderChain(providers=[provider])

    chain.get_quote("AAPL")
    chain.get_quote("AAPL")

    assert calls == 2


def test_provider_cache_covers_history_financials_and_news(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINANCE_HISTORY_CACHE_TTL_SECONDS", "60")
    monkeypatch.setenv("FINANCE_FINANCIALS_CACHE_TTL_SECONDS", "60")
    monkeypatch.setenv("FINANCE_NEWS_CACHE_TTL_SECONDS", "60")

    class CacheableProvider:
        name = "CACHEABLE"
        news_is_symbol_scoped = True

        def __init__(self) -> None:
            self.calls = {"history": 0, "financials": 0, "news": 0}

        def get_history(self, symbol: str, period: str, interval: str) -> list[Candle]:
            self.calls["history"] += 1
            return [Candle("2026-07-14", 100, 101, 99, 100)]

        def get_financials(self, symbol: str) -> Financials:
            self.calls["financials"] += 1
            return Financials(symbol=symbol, source=self.name, revenue=100)

        def get_news(self, symbol: str, limit: int) -> list[NewsItem]:
            self.calls["news"] += 1
            return [NewsItem(f"{symbol} update", source=self.name)]

    provider = CacheableProvider()
    chain = ProviderChain(providers=[provider])

    chain.get_history("AAPL", "1y", "1d")
    chain.get_history("AAPL", "1y", "1d")
    chain.get_financials("AAPL")
    chain.get_financials("AAPL")
    chain.get_news("AAPL", 5)
    chain.get_news("AAPL", 5)

    assert provider.calls == {"history": 1, "financials": 1, "news": 1}


def test_provider_operation_deadline_returns_fast_source_and_records_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINANCE_PROVIDER_TIMEOUT_SECONDS", "0.05")
    chain = ProviderChain(providers=[SlowQuoteProvider("SLOW", 0.25, 99), PricedProvider("FAST", 100)])

    started = time.monotonic()
    quote = chain.get_quote("AAPL")
    elapsed = time.monotonic() - started

    assert quote.source == "FAST"
    assert elapsed < 0.18
    assert chain.source_coverage("get_quote")["failed_real_sources"] == [
        {"name": "SLOW", "error": "timed out after 0.05s operation deadline"}
    ]


def test_timed_out_provider_does_not_accumulate_duplicate_daemon_workers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINANCE_PROVIDER_TIMEOUT_SECONDS", "0.01")
    monkeypatch.setenv("FINANCE_PROVIDER_COOLDOWN_SECONDS", "0.01")
    slow = SlowQuoteProvider("HUNG", 0.3, 99)
    chain = ProviderChain(providers=[slow, PricedProvider("FAST", 100)])

    for _ in range(5):
        assert chain.get_quote("AAPL").source == "FAST"
        time.sleep(0.015)

    assert slow.calls == 1


def test_snapshot_uses_one_total_deadline_across_all_data_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINANCE_PROVIDER_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("FINANCE_SNAPSHOT_TIMEOUT_SECONDS", "0.05")
    providers = [
        SlowMethodProvider("SLOW_QUOTE", "get_quote", 0.3),
        SlowMethodProvider("SLOW_HISTORY", "get_history", 0.3),
        SlowMethodProvider("SLOW_FINANCIALS", "get_financials", 0.3),
        SlowMethodProvider("SLOW_NEWS", "get_news", 0.3),
    ]
    agent = FinanceResearchAgent(provider=ProviderChain(providers=providers))

    started = time.monotonic()
    snapshot = agent.snapshot("AAPL")
    elapsed = time.monotonic() - started

    assert elapsed < 0.18
    assert snapshot.quote.source == "UNAVAILABLE"
    assert sum(provider.calls for provider in providers) == 1


def test_large_quote_spread_blocks_high_confidence_quality_grade() -> None:
    chain = ProviderChain(providers=[PricedProvider("REAL_A", 100), PricedProvider("REAL_B", 110)])
    quote = chain.get_quote("AAPL")
    snapshot = StockSnapshot(
        symbol="AAPL",
        quote=quote,
        history=[Candle(str(index), 1, 1, 1, 1) for index in range(120)],
        financials=Financials(symbol="AAPL", source="REAL", revenue=1, net_income=1, debt_to_equity=10),
        news=[NewsItem("Apple update")],
        indicators={},
        fetched_at="2026-01-01",
        source_coverage={"get_history": {"successful_real_sources": ["A", "B"], "history_close_spread_pct": 0}},
    )

    screen = render_quality_screen(snapshot)

    assert "信息丰富度: C" in screen
    assert "跨源行情冲突" in screen


def test_stale_core_data_and_historical_news_cannot_receive_grade_a() -> None:
    snapshot = StockSnapshot(
        symbol="AAPL",
        quote=Quote(symbol="AAPL", price=100, source="Q", as_of="2020-01-01", is_realtime=False),
        history=[Candle(f"2020-01-{(index % 28) + 1:02d}", 1, 1, 1, 1) for index in range(120)],
        financials=Financials(
            symbol="AAPL", source="F", as_of="2010-12-31", currency="USD", period_type="annual",
            revenue=1, net_income=1, eps=1, free_cash_flow=1, return_on_equity=0.2,
        ),
        news=[NewsItem("Apple historical event", published_at="2020-01-01", source="N")],
        indicators={},
        fetched_at="2026-07-11",
        source_coverage={
            "get_quote": {"successful_real_sources": ["Q1", "Q2"]},
            "get_history": {"successful_real_sources": ["H1", "H2"], "history_close_spread_pct": 0},
            "get_financials": {"successful_real_sources": ["F1", "F2"], "field_differences_pct": {}},
            "get_news": {"successful_real_sources": ["N1", "N2"], "sample_used": False},
        },
    )

    gate = evaluate_quality_gate(snapshot)

    assert gate.information_grade == "C"
    assert any("不应当作当前价格" in warning for warning in gate.warnings)
    assert any("不算近期事件覆盖" in warning for warning in gate.warnings)


def test_history_cross_checks_all_real_sources_and_records_close_spread() -> None:
    first = HistoryProvider("HISTORY_A", [100, 101])
    second = HistoryProvider("HISTORY_B", [100, 103])
    chain = ProviderChain(providers=[first, second, SampleDataProvider()])

    history = chain.get_history("AAPL", "1mo", "1d")
    coverage = chain.source_coverage("get_history")

    assert history[-1].close == 101
    assert first.calls == 1 and second.calls == 1
    assert coverage["successful_real_sources"] == ["HISTORY_A", "HISTORY_B"]
    assert coverage["history_close_spread_pct"] == pytest.approx((103 - 101) / 101 * 100)
    assert coverage["sample_used"] is False


def test_empty_financials_fall_through_to_a_real_populated_source() -> None:
    chain = ProviderChain(providers=[EmptyFinancialsProvider(), RichFinancialsProvider()])

    financials = chain.get_financials("AAPL")
    coverage = chain.source_coverage("get_financials")

    assert financials.source == "RICH_FINANCIALS"
    assert financials.revenue == 123_000_000
    assert coverage["successful_real_sources"] == ["RICH_FINANCIALS"]
    assert coverage["failed_real_sources"][0]["name"] == "EMPTY_FINANCIALS"
    assert "无可用字段" in coverage["failed_real_sources"][0]["error"]


def test_financials_merge_real_sources_and_record_field_differences() -> None:
    chain = ProviderChain(
        providers=[
            PartialFinancialsProvider("FUND_A", revenue=100, eps=5),
            PartialFinancialsProvider("FUND_B", revenue=102, eps=5.5, free_cash_flow=10),
            SampleDataProvider(),
        ]
    )

    financials = chain.get_financials("AAPL")
    coverage = chain.source_coverage("get_financials")

    assert financials.source == "FUND_A / FUND_B"
    assert financials.revenue == 100
    assert financials.free_cash_flow == 10
    assert coverage["successful_real_sources"] == ["FUND_A", "FUND_B"]
    assert coverage["sample_used"] is False
    assert coverage["field_differences_pct"]["revenue"] == pytest.approx(1.960784)
    assert coverage["field_differences_pct"]["eps"] == pytest.approx(9.090909)
    assert any("基本面跨源字段差异" in note for note in financials.notes)


def test_financials_use_sample_only_when_all_real_sources_are_empty() -> None:
    chain = ProviderChain(providers=[EmptyFinancialsProvider(), SampleDataProvider()])

    financials = chain.get_financials("AAPL")
    coverage = chain.source_coverage("get_financials")

    assert financials.source == "SAMPLE_FALLBACK"
    assert coverage["successful_real_sources"] == []
    assert coverage["sample_used"] is True


def test_news_aggregates_real_sources_filters_noise_and_deduplicates() -> None:
    chain = ProviderChain(
        providers=[
            StaticNewsProvider(
                "NEWS_A",
                [NewsItem(title="Apple unveils new chip", link="https://a.example/apple", source="NEWS_A")],
            ),
            StaticNewsProvider(
                "NEWS_B",
                [
                    NewsItem(title="Apple unveils new chip", link="https://b.example/duplicate", source="NEWS_B"),
                    NewsItem(title="Quarterly update", summary="Apple revenue rises", source="NEWS_B"),
                    NewsItem(title="Intel launches server chip", source="NEWS_B"),
                ],
            ),
            StaticNewsProvider(
                "NEWS_NOISE",
                [NewsItem(title="Rocket Lab launches satellite", source="NEWS_NOISE")],
            ),
            SampleDataProvider(),
        ]
    )

    news = chain.get_news("AAPL", limit=3)
    coverage = chain.source_coverage("get_news")

    assert [item.title for item in news] == ["Apple unveils new chip", "Quarterly update"]
    assert coverage["successful_real_sources"] == ["NEWS_A", "NEWS_B"]
    assert coverage["failed_real_sources"][-1]["name"] == "NEWS_NOISE"
    assert coverage["sample_used"] is False
    assert all(item.source != "SAMPLE_FALLBACK" for item in news)


def test_news_uses_sample_only_when_no_real_relevant_result() -> None:
    chain = ProviderChain(
        providers=[
            StaticNewsProvider("NEWS_NOISE", [NewsItem(title="Intel launches server chip")]),
            SampleDataProvider(),
        ]
    )

    news = chain.get_news("AAPL", limit=2)
    coverage = chain.source_coverage("get_news")

    assert len(news) == 2
    assert all(item.source == "SAMPLE_FALLBACK" for item in news)
    assert coverage["successful_real_sources"] == []
    assert coverage["sample_used"] is True


def test_zero_news_limit_returns_without_calling_providers() -> None:
    provider = CountingNewsProvider()
    chain = ProviderChain(providers=[provider])

    assert chain.get_news("AAPL", limit=0) == []
    assert provider.calls == 0
    assert chain.source_coverage("get_news")["successful_real_sources"] == []


def test_quote_backfilled_financial_fields_keep_quote_provenance() -> None:
    agent = FinanceResearchAgent(provider=ProviderChain(providers=[QuoteBackfillProvider()]))

    snapshot = agent.snapshot("AAPL", period="1mo", news_limit=0)

    assert snapshot.financials.market_cap == 3_000_000
    assert snapshot.financials.field_sources["market_cap"] == "BACKFILL (quote, 2026-07-11T00:00:00Z)"


@pytest.mark.parametrize(
    ("symbol", "expected_revenue", "expected_eps", "expected_roe"),
    [
        ("AAPL", 416_161_000_000, 7.49, 1.7142245),
        ("600519.SS", 172_054_200_000, 65.66, 0.3253),
        ("02513.HK", 724_334_000, -12.03, -6.51380026),
    ],
)
def test_akshare_financial_fallback_maps_real_fields_across_markets(
    symbol: str,
    expected_revenue: float,
    expected_eps: float,
    expected_roe: float,
) -> None:
    provider = AKShareProvider()
    provider._ak = FakeAKShareFinancialClient()

    financials = provider.get_financials(symbol)

    assert financials.revenue == expected_revenue
    assert financials.eps == expected_eps
    assert financials.return_on_equity == pytest.approx(expected_roe)
    assert financials.source == "AKShare"
    assert "真实财务指标" in financials.notes[0]


def test_tushare_roe_percentage_is_normalized_to_a_ratio() -> None:
    provider = TushareProvider(token="test-token")
    provider._pro = FakeTushareClient()

    financials = provider.get_financials("600519.SS")

    assert financials.return_on_equity == pytest.approx(0.2)
    assert financials.debt_to_equity == pytest.approx(66.666667)


def test_tushare_does_not_mislabel_operating_cash_flow_as_free_cash_flow() -> None:
    provider = TushareProvider(token="test-token")
    provider._pro = FakeTushareClientWithoutCapex()

    financials = provider.get_financials("600519.SS")

    assert financials.free_cash_flow is None


def test_tushare_quote_keeps_price_and_reports_daily_basic_failure() -> None:
    class QuoteClient:
        def daily(self, **kwargs: object) -> pd.DataFrame:
            return pd.DataFrame([
                {"trade_date": "20260713", "close": 100, "pre_close": 99, "vol": 10},
                {"trade_date": "20260714", "close": 101, "pre_close": 100, "vol": 20},
            ])

        def daily_basic(self, **kwargs: object) -> pd.DataFrame:
            raise RuntimeError("daily quota exceeded")

        def stock_basic(self, **kwargs: object) -> pd.DataFrame:
            return pd.DataFrame([{"ts_code": "600519.SH", "name": "贵州茅台"}])

    provider = TushareProvider(token="test-token")
    provider._pro = QuoteClient()

    quote = provider.get_quote("600519")

    assert quote.price == 101
    assert quote.market_cap is None
    assert quote.pe_ratio is None
    assert any("daily quota exceeded" in note for note in quote.notes)


def test_yfinance_fallback_preserves_currency_and_report_period(monkeypatch: pytest.MonkeyPatch) -> None:
    info = {
        "marketCap": 3_000_000,
        "totalRevenue": 1_000_000,
        "financialCurrency": "USD",
        "currency": "USD",
        "mostRecentQuarter": 1_767_139_200,
    }
    monkeypatch.setitem(
        sys.modules,
        "yfinance",
        SimpleNamespace(Ticker=lambda symbol: SimpleNamespace(info=info)),
    )
    provider = YahooFinanceProvider()
    provider.client = FailingHTTPClient()

    financials = provider.get_financials("AAPL")

    assert financials.currency == "USD"
    assert financials.as_of
    assert financials.market_cap == 3_000_000
    assert financials.revenue == 1_000_000


def test_us_financial_institution_columns_are_mapped() -> None:
    provider = AKShareProvider()
    provider._ak = FakeAKShareFinancialInstitutionClient()

    financials = provider.get_financials("JPM")

    assert financials.revenue == 180_000_000_000
    assert financials.eps == 19.2
    assert financials.return_on_equity == pytest.approx(0.17)
    assert financials.debt_to_equity == pytest.approx(400.0)


class NewsFailingProvider:
    name = "NEWS_FAILING"

    def get_quote(self, symbol: str) -> Quote:
        return Quote(symbol=symbol, price=100, source=self.name, as_of="2026-01-01", is_realtime=True)

    def get_history(self, symbol: str, period: str = "1y", interval: str = "1d") -> list[Candle]:
        return [Candle(date="2026-01-01", open=100, high=101, low=99, close=100)]

    def get_financials(self, symbol: str) -> Financials:
        return Financials(symbol=symbol, source=self.name, as_of="2026-01-01", revenue=1)

    def get_news(self, symbol: str, limit: int = 5) -> list[NewsItem]:
        raise ProviderError("upstream unavailable")


class QuoteFailingProvider:
    name = "QUOTE_FAIL"

    def get_quote(self, symbol: str) -> Quote:
        raise ProviderError("quote failed")


class QuoteBackfillProvider:
    name = "BACKFILL"

    def get_quote(self, symbol: str) -> Quote:
        return Quote(
            symbol=symbol,
            price=100,
            market_cap=3_000_000,
            source=self.name,
            as_of="2026-07-11T00:00:00Z",
            is_realtime=True,
        )

    def get_history(self, symbol: str, period: str = "1y", interval: str = "1d") -> list[Candle]:
        return [Candle("2026-07-11", 100, 100, 100, 100)]

    def get_financials(self, symbol: str) -> Financials:
        return Financials(
            symbol=symbol,
            source=self.name,
            as_of="2026-06-30",
            currency="USD",
            period_type="quarterly",
            revenue=1,
        )


class CountingNewsProvider:
    name = "COUNTING_NEWS"

    def __init__(self) -> None:
        self.calls = 0

    def get_news(self, symbol: str, limit: int = 5) -> list[NewsItem]:
        self.calls += 1
        return [NewsItem("Apple update", source=self.name)]


class FailingHTTPClient:
    def get(self, *args: object, **kwargs: object) -> object:
        raise RuntimeError("quote summary unavailable")


@dataclass
class PricedProvider:
    name: str
    price: float
    as_of: str = "2026-01-01"
    is_realtime: bool = False

    def get_quote(self, symbol: str) -> Quote:
        return Quote(
            symbol=symbol,
            price=self.price,
            source=self.name,
            as_of=self.as_of,
            is_realtime=self.is_realtime,
        )


@dataclass
class SlowQuoteProvider:
    name: str
    delay: float
    price: float
    calls: int = 0

    def get_quote(self, symbol: str) -> Quote:
        self.calls += 1
        time.sleep(self.delay)
        return Quote(symbol=symbol, price=self.price, source=self.name, as_of="2026-07-11")


class SlowMethodProvider:
    def __init__(self, name: str, method: str, delay: float) -> None:
        self.name = name
        self.capabilities = {method}
        self.delay = delay
        self.calls = 0

    def _wait(self) -> None:
        self.calls += 1
        time.sleep(self.delay)

    def get_quote(self, symbol: str) -> Quote:
        self._wait()
        return Quote(symbol=symbol, price=1, source=self.name, as_of="2026-07-11")

    def get_history(self, symbol: str, period: str = "1y", interval: str = "1d") -> list[Candle]:
        self._wait()
        return [Candle("2026-07-11", 1, 1, 1, 1)]

    def get_financials(self, symbol: str) -> Financials:
        self._wait()
        return Financials(symbol=symbol, source=self.name, revenue=1)

    def get_news(self, symbol: str, limit: int = 5) -> list[NewsItem]:
        self._wait()
        return [NewsItem("Apple update", source=self.name)]


@dataclass
class HistoryProvider:
    name: str
    closes: list[float]
    calls: int = 0

    def get_history(self, symbol: str, period: str = "1y", interval: str = "1d") -> list[Candle]:
        self.calls += 1
        return [
            Candle(f"2026-01-{index + 1:02d}", close, close, close, close)
            for index, close in enumerate(self.closes)
        ]


class EmptyFinancialsProvider:
    name = "EMPTY_FINANCIALS"

    def get_financials(self, symbol: str) -> Financials:
        return Financials(symbol=symbol, source=self.name, as_of="2026-01-01")


class RichFinancialsProvider:
    name = "RICH_FINANCIALS"

    def get_financials(self, symbol: str) -> Financials:
        return Financials(symbol=symbol, source=self.name, as_of="2026-01-01", revenue=123_000_000)


@dataclass
class PartialFinancialsProvider:
    name: str
    revenue: float | None = None
    eps: float | None = None
    free_cash_flow: float | None = None
    currency: str = "USD"
    as_of: str = "2026-01-01"
    period_type: str = "annual"

    def get_financials(self, symbol: str) -> Financials:
        return Financials(
            symbol=symbol,
            source=self.name,
            as_of=self.as_of,
            currency=self.currency,
            period_type=self.period_type,
            revenue=self.revenue,
            eps=self.eps,
            free_cash_flow=self.free_cash_flow,
        )


def test_financials_do_not_merge_monetary_fields_across_currency_or_period() -> None:
    chain = ProviderChain(providers=[
        PartialFinancialsProvider("ANNUAL_USD", revenue=100, currency="USD", as_of="2025-12-31", period_type="annual"),
        PartialFinancialsProvider("TTM_CNY", free_cash_flow=10, currency="CNY", as_of="2026-03-31", period_type="TTM"),
    ])

    financials = chain.get_financials("AAPL")

    assert financials.revenue == 100
    assert financials.free_cash_flow is None
    assert any("未合并 TTM_CNY" in note for note in financials.notes)


@dataclass
class StaticNewsProvider:
    name: str
    items: list[NewsItem]

    def get_news(self, symbol: str, limit: int = 5) -> list[NewsItem]:
        return self.items[:limit]


class FakeAKShareFinancialClient:
    def stock_financial_us_analysis_indicator_em(self, symbol: str, indicator: str) -> pd.DataFrame:
        return pd.DataFrame([
            {
                "REPORT_DATE": "2025-09-27",
                "OPERATE_INCOME": 416_161_000_000,
                "GROSS_PROFIT": 195_201_000_000,
                "PARENT_HOLDER_NETPROFIT": 112_010_000_000,
                "BASIC_EPS": 7.49,
                "ROE_AVG": 171.42245,
                "NET_PROFIT_RATIO": 26.915064,
                "DEBT_ASSET_RATIO": 79.475338,
            }
        ])

    def stock_financial_analysis_indicator_em(self, symbol: str, indicator: str) -> pd.DataFrame:
        return pd.DataFrame([
            {
                "REPORT_DATE": "2025-12-31",
                "TOTALOPERATEREVE": 172_054_200_000,
                "MLR": 153_945_800_000,
                "PARENTNETPROFIT": 82_320_070_000,
                "EPSJB": 65.66,
                "ROEJQ": 32.53,
                "XSJLL": 50.527887,
                "ZCFZL": 16.415362,
                "CQBL": 19.640231,
                "FCFF_BACK": 76_139_290_000,
            }
        ])

    def stock_financial_hk_analysis_indicator_em(self, symbol: str, indicator: str) -> pd.DataFrame:
        return pd.DataFrame([
            {
                "REPORT_DATE": "2025-12-31",
                "OPERATE_INCOME": 724_334_000,
                "GROSS_PROFIT": 296_656_000,
                "HOLDER_PROFIT": -4_698_203_000,
                "BASIC_EPS": -12.03,
                "ROE_AVG": -651.380026,
                "NET_PROFIT_RATIO": -651.380026,
                "DEBT_ASSET_RATIO": 267.103714,
            }
        ])


class FakeTushareClient:
    def daily_basic(self, **kwargs: object) -> pd.DataFrame:
        return pd.DataFrame([{"trade_date": "20251231", "total_mv": 100, "pe_ttm": 20}])

    def income(self, **kwargs: object) -> pd.DataFrame:
        return pd.DataFrame([
            {
                "end_date": "20251231",
                "total_revenue": 100,
                "grossprofit": 60,
                "n_income_attr_p": 20,
            }
        ])

    def cashflow(self, **kwargs: object) -> pd.DataFrame:
        return pd.DataFrame([{"end_date": "20251231", "free_cashflow": 10}])

    def fina_indicator(self, **kwargs: object) -> pd.DataFrame:
        return pd.DataFrame([{"end_date": "20251231", "eps": 2, "debt_to_assets": 40, "roe": 20}])


class FakeTushareClientWithoutCapex(FakeTushareClient):
    def cashflow(self, **kwargs: object) -> pd.DataFrame:
        return pd.DataFrame([{"end_date": "20251231", "n_cashflow_act": 100}])


class FakeAKShareFinancialInstitutionClient:
    def stock_financial_us_analysis_indicator_em(self, symbol: str, indicator: str) -> pd.DataFrame:
        return pd.DataFrame([{
            "REPORT_DATE": "2025-12-31",
            "TOTAL_INCOME": 180_000_000_000,
            "PARENT_HOLDER_NETPROFIT": 58_000_000_000,
            "BASIC_EPS_CS": 19.2,
            "ROE": 17.0,
            "DEBT_RATIO": 80.0,
        }])
