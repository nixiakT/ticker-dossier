from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from ticker_dossier.research.agent import FinanceResearchAgent
from ticker_dossier.research.backtest import StrategyConfig, backtest_moving_average_cross, format_backtest, parse_strategy
from ticker_dossier.research.data import ProviderChain, ProviderError, SampleDataProvider, _news_matches
from ticker_dossier.research.models import Candle, Financials, NewsItem, Quote, StockSnapshot, utc_now_iso
from ticker_dossier.research.history_learning import learn_from_history, render_learning, update_history_learning_skill
from ticker_dossier.research.paper_portfolio import (
    PortfolioConflictError,
    construct_portfolio,
    inspect_account_locations,
    load_account,
    mark_to_market,
    migrate_account,
    rebalance_portfolio,
    render_account,
    render_account_locations,
    render_daily_pnl,
    render_portfolio_review,
    render_transactions,
    score_candidates,
    sell_holding,
    value_account_read_only,
)
from ticker_dossier.research.predictions import evaluate_due_predictions, evaluate_prediction, load_predictions, record_prediction, render_learning_report
from ticker_dossier.research.quality import render_quality_screen
from ticker_dossier.research.rendering import render_stock_report
from ticker_dossier.research.resolver import resolve_symbol
from ticker_dossier.research.symbols import extract_symbols, normalize_symbol, to_yahoo_symbol
from ticker_dossier.research.web import web_fetch, web_search
from ticker_dossier.security import SecurityError


def test_symbol_normalization_handles_common_markets() -> None:
    assert normalize_symbol("智谱") == "02513.HK"
    assert normalize_symbol("2513.HK") == "2513.HK"
    assert normalize_symbol("02513") == "02513.HK"
    assert normalize_symbol("600519") == "600519.SS"
    assert to_yahoo_symbol("02513.HK") == "2513.HK"
    assert extract_symbols("比较 智谱 和 AAPL 最近走势")[:2] == ["02513.HK", "AAPL"]
    assert normalize_symbol("SpaceX") == "SPCX"
    assert normalize_symbol("腾讯") == "00700.HK"
    assert to_yahoo_symbol("腾讯") == "0700.HK"
    assert extract_symbols("SpaceX 最近情况如何")[0] == "SPCX"


def test_finance_http_proxy_is_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    import ticker_dossier.integrations.http as finance_http

    monkeypatch.setenv("FINANCE_HTTP_PROXY", "http://127.0.0.1:7897")

    assert finance_http.proxy_label() == "http://127.0.0.1:7897"


def test_resolve_symbol_uses_yahoo_search_for_company_names(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    def fake_get(url: str, **kwargs: object) -> httpx.Response:
        query = str((kwargs.get("params") or {}).get("q", ""))  # type: ignore[union-attr]
        payload = {
            "quotes": [
                {"symbol": "0100.HK", "shortname": "MINIMAX-W", "quoteType": "EQUITY", "exchange": "HKG"}
            ]
        }
        if query.lower() == "nvidia":
            payload = {
                "quotes": [
                    {"symbol": "NVDA", "shortname": "NVIDIA Corporation", "quoteType": "EQUITY", "exchange": "NMS"}
                ]
            }
        return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)

    assert resolve_symbol("minimax").symbol == "0100.HK"
    assert resolve_symbol("nvidia").symbol == "NVDA"
    assert resolve_symbol("SpaceX").symbol == "SPCX"


def test_resolve_symbol_uses_eastmoney_for_cn_hk_us_names(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    def fake_get(url: str, **kwargs: object) -> httpx.Response:
        params = kwargs.get("params") or {}
        query = str(params.get("input") or params.get("q") or "")  # type: ignore[union-attr]
        data = None
        if "eastmoney" in url:
            rows = {
                "minimax": [{"Code": "00100", "Name": "MINIMAX-W", "JYS": "HK", "Classify": "HK", "SecurityTypeName": "港股"}],
                "小米": [{"Code": "01810", "Name": "小米集团-W", "JYS": "HK", "Classify": "HK", "SecurityTypeName": "港股"}],
                "贵州茅台": [{"Code": "600519", "Name": "贵州茅台", "JYS": "2", "Classify": "AStock", "SecurityTypeName": "沪A"}],
            }.get(query, [])
            data = {"QuotationCodeTable": {"Data": rows}}
        else:
            data = {"quotes": []}
        return httpx.Response(200, json=data, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)

    assert resolve_symbol("minimax").symbol == "00100.HK"
    assert resolve_symbol("小米").symbol == "01810.HK"
    assert resolve_symbol("贵州茅台").symbol == "600519.SS"


def test_resolve_symbol_uses_web_fallback_for_legal_names(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    def fake_get(url: str, **kwargs: object) -> httpx.Response:
        if "eastmoney" in url:
            payload = {"QuotationCodeTable": {"Data": []}}
            return httpx.Response(200, json=payload, request=httpx.Request("GET", url))
        if "finance/search" in url:
            return httpx.Response(200, json={"quotes": []}, request=httpx.Request("GET", url))
        html = "<html><body>稀宇科技 MiniMax 股票代码 HK 00100</body></html>"
        return httpx.Response(200, text=html, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)

    assert resolve_symbol("稀宇科技").symbol == "00100.HK"


def test_provider_chain_falls_through_to_next_provider() -> None:
    chain = ProviderChain(providers=[FailingProvider(), StaticProvider()])

    quote = chain.get_quote("AAPL")

    assert quote.symbol == "AAPL"
    assert quote.source == "STATIC"
    assert quote.price == 123.45


def test_provider_chain_can_report_custom_provider_diagnostics() -> None:
    chain = ProviderChain(providers=[StaticProvider()])

    assert chain.diagnostics() == [{"name": "STATIC", "status": "enabled", "detail": ""}]


def test_sample_fallback_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINANCE_ALLOW_SAMPLE_FALLBACK", "0")

    chain = ProviderChain()

    assert all(not isinstance(provider, SampleDataProvider) for provider in chain.providers)
    fallback = [row for row in chain.diagnostics() if row["name"] == "SAMPLE_FALLBACK"][0]
    assert fallback["status"] == "disabled"


def test_route_task_selects_compare_and_backtest() -> None:
    agent = FinanceResearchAgent(provider=ProviderChain(providers=[StaticProvider()]))

    compare = agent.route_task("比较 AAPL 和 MSFT 的基本面")
    backtest = agent.route_task("帮我回测 AAPL 的 20 日均线上穿 60 日均线策略")

    assert "# 股票对比" in compare
    assert "# 策略回测结果" in backtest


def test_get_financials_does_not_repeat_quote_or_history_requests() -> None:
    class FundamentalsOnlyProvider:
        def __init__(self) -> None:
            self.financial_calls: list[str] = []

        def get_financials(self, symbol: str) -> Financials:
            self.financial_calls.append(symbol)
            return Financials(
                symbol=symbol,
                source="STATIC_FUNDAMENTALS",
                as_of="2026-06-30",
                revenue=100,
                net_income=20,
            )

        def get_quote(self, symbol: str) -> Quote:
            raise AssertionError("finance_get_financials must not fetch a quote")

        def get_history(self, symbol: str, period: str, interval: str) -> list[Candle]:
            raise AssertionError("finance_get_financials must not fetch price history")

    provider = FundamentalsOnlyProvider()
    output = FinanceResearchAgent(provider=provider).get_financials("AAPL")  # type: ignore[arg-type]

    assert provider.financial_calls == ["AAPL"]
    assert "STATIC_FUNDAMENTALS" in output
    assert "2026-06-30" in output


def test_prediction_records_use_history_without_quote_financials_or_news(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ticker_dossier.research.agent as finance_agent

    class PredictionProvider:
        name = "HISTORY_ONLY"

        def __init__(self) -> None:
            self.history_calls: list[str] = []
            self.quote_calls = 0
            self.financial_calls = 0
            self.news_calls = 0

        def get_history(self, symbol: str, period: str, interval: str) -> list[Candle]:
            self.history_calls.append(symbol)
            return [
                Candle(
                    (date(2026, 1, 1) + timedelta(days=index)).isoformat(),
                    100 + index,
                    100 + index,
                    100 + index,
                    100 + index,
                )
                for index in range(90)
            ]

        def get_quote(self, symbol: str) -> Quote:
            self.quote_calls += 1
            raise AssertionError("prediction should not fetch a quote when history is available")

        def get_financials(self, symbol: str) -> Financials:
            self.financial_calls += 1
            raise AssertionError("prediction should not fetch fundamentals")

        def get_news(self, symbol: str, limit: int = 5) -> list[NewsItem]:
            self.news_calls += 1
            raise AssertionError("prediction should not fetch news")

    def fake_record_prediction(**kwargs):  # noqa: ANN003, ANN201
        return type("Prediction", (), {**kwargs, "id": f"pred-{kwargs['symbol']}", "due_at": "2026-08-13"})()

    monkeypatch.setattr(finance_agent, "record_prediction", fake_record_prediction)
    provider = PredictionProvider()
    agent = FinanceResearchAgent(provider=provider)  # type: ignore[arg-type]

    output = agent.record_task_predictions("记录未来30天涨跌方向", ["AAPL", "MSFT"])

    assert provider.history_calls == ["AAPL", "MSFT"]
    assert provider.quote_calls == 0
    assert provider.financial_calls == 0
    assert provider.news_calls == 0
    assert "pred-AAPL" in output
    assert "pred-MSFT" in output


def test_route_task_portfolio_review_handles_direct_tickers_without_resolver_network(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    import ticker_dossier.research.paper_portfolio as portfolio
    import ticker_dossier.research.agent as finance_agent

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(portfolio, "PORTFOLIO_DIR", tmp_path / ".finance_agent")
    monkeypatch.setattr(finance_agent, "resolve_symbol", lambda symbol: (_ for _ in ()).throw(AssertionError("network resolver should not be called")))
    agent = FinanceResearchAgent(provider=ProviderChain(providers=[StaticProvider()]))

    agent.build_paper_portfolio(["AAPL", "MSFT"], 100_000)
    output = agent.route_task("MSFT 为什么买 有没有更好选择 GOOGL AVGO")

    assert "纸面组合诊断" in output
    assert "持仓复盘" in output


def test_route_task_builds_paper_portfolio_for_cash_allocation(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    import ticker_dossier.research.paper_portfolio as portfolio

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(portfolio, "PORTFOLIO_DIR", tmp_path / ".finance_agent")
    agent = FinanceResearchAgent(provider=ProviderChain(providers=[StaticProvider()]))

    output = agent.route_task("给 agent 100万 自己投资 AAPL MSFT NVDA 买多少")

    assert "# 模拟投资账户" in output
    assert "候选评分" in output
    assert "不会执行真实交易" in output


def test_paper_portfolio_constructs_and_marks_account(tmp_path) -> None:  # noqa: ANN001
    snapshot = StockSnapshot(
        symbol="AAPL",
        quote=Quote(symbol="AAPL", price=100, source="STATIC", as_of=utc_now_iso(), is_realtime=True),
        history=rising_candles(120),
        financials=Financials(
            symbol="AAPL",
            source="STATIC",
            as_of=utc_now_iso(),
            pe_ratio=20,
            free_cash_flow=1_000_000,
            return_on_equity=0.18,
            profit_margin=0.22,
        ),
        news=[NewsItem(title="Static headline")],
        indicators={"return_3m_pct": 12, "return_1y_pct": 24, "annualized_volatility_pct": 20},
        fetched_at=utc_now_iso(),
    )

    account, scores = construct_portfolio([snapshot], initial_cash=1_000_000, base_dir=tmp_path)
    assert account.transactions
    assert account.transactions[0]["action"] == "BUY"
    marked = mark_to_market(
        get_quote=lambda symbol: Quote(symbol=symbol, price=110, source="STATIC", as_of=utc_now_iso()),
        base_dir=tmp_path,
    )
    sold = sell_holding("AAPL", shares="all", price=120, base_dir=tmp_path, reason="unit sell")
    output = render_account(marked)
    trades = render_transactions(sold)
    daily = render_daily_pnl(sold)

    assert scores[0].score > 35
    assert account.holdings
    assert marked.history[-1]["event"] == "mark"
    assert "累计收益" in output
    assert "SELL" in trades
    assert sold.transactions[-1]["realized_pnl"] > 0
    assert "每日买卖盈亏" in daily
    assert "已实现盈亏" in daily


def test_portfolio_requires_explicit_legacy_migration(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    import ticker_dossier.research.paper_portfolio as portfolio

    legacy_dir = tmp_path / "project" / ".finance_agent"
    persistent_dir = tmp_path / "home" / ".finance-agent" / "portfolios"
    portfolio.create_account(initial_cash=123_456, base_dir=legacy_dir)
    monkeypatch.setattr(portfolio, "LEGACY_PORTFOLIO_DIR", legacy_dir)
    monkeypatch.setattr(portfolio, "DEFAULT_PORTFOLIO_DIR", persistent_dir)
    monkeypatch.setattr(portfolio, "PORTFOLIO_DIR", persistent_dir)

    account = portfolio.load_account()

    assert account.initial_cash == 123_456
    assert not (persistent_dir / "portfolio_default.json").exists()
    assert "未自动迁移" in render_account(account)

    result = migrate_account()
    migrated = portfolio.load_account(create_if_missing=False)
    payload = json.loads(result.destination.read_text(encoding="utf-8"))

    assert migrated.initial_cash == 123_456
    assert result.destination == persistent_dir / "portfolio_default.json"
    assert result.destination.exists()
    assert result.recovery_backup.exists()
    assert not (legacy_dir / "portfolio_default.json").exists()
    assert payload["schema_version"] == 2
    assert payload["account_id"]
    assert payload["origin"] == "workspace"


def test_portfolio_migration_never_overwrites_a_racing_destination(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # noqa: ANN001
    import ticker_dossier.research.paper_portfolio as portfolio

    workspace_dir = tmp_path / "project" / ".finance_agent"
    user_dir = tmp_path / "home" / ".finance-agent" / "portfolios"
    portfolio.create_account(initial_cash=123_456, base_dir=workspace_dir)
    source = workspace_dir / "portfolio_default.json"
    destination = user_dir / "portfolio_default.json"
    monkeypatch.setattr(portfolio, "LEGACY_PORTFOLIO_DIR", workspace_dir)
    monkeypatch.setattr(portfolio, "PORTFOLIO_DIR", user_dir)

    def race_target_into_place(_source: str | Path, target: str | Path) -> None:
        Path(target).write_text("created by another process", encoding="utf-8")
        raise FileExistsError(str(target))

    monkeypatch.setattr(portfolio.os, "link", race_target_into_place)

    with pytest.raises(PortfolioConflictError, match="迁移期间出现"):
        migrate_account()

    assert destination.read_text(encoding="utf-8") == "created by another process"
    assert source.exists()
    assert not list(user_dir.glob(".*.tmp"))


def test_portfolio_conflict_is_visible_for_reads_and_blocks_every_write_path(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # noqa: ANN001
    import ticker_dossier.research.paper_portfolio as portfolio

    workspace_dir = tmp_path / "project" / ".finance_agent"
    user_dir = tmp_path / "home" / ".finance-agent" / "portfolios"
    portfolio.create_account(initial_cash=111_111, base_dir=workspace_dir)
    portfolio.create_account(initial_cash=222_222, base_dir=user_dir)
    workspace_path = workspace_dir / "portfolio_default.json"
    user_path = user_dir / "portfolio_default.json"
    before = (workspace_path.read_bytes(), user_path.read_bytes())
    monkeypatch.setattr(portfolio, "LEGACY_PORTFOLIO_DIR", workspace_dir)
    monkeypatch.setattr(portfolio, "PORTFOLIO_DIR", user_dir)

    locations = inspect_account_locations()
    account = load_account(create_if_missing=False)
    status = render_account(account)
    located = render_account_locations()

    assert locations.conflict is True
    assert account.initial_cash == 222_222
    assert "位置警告" in status and "写操作已锁定" in status
    assert "**冲突**" in located
    with pytest.raises(PortfolioConflictError, match="写入已拒绝"):
        mark_to_market(get_quote=lambda symbol: Quote(symbol=symbol, price=1))
    with pytest.raises(PortfolioConflictError, match="目标.*已存在"):
        migrate_account()
    assert (workspace_path.read_bytes(), user_path.read_bytes()) == before


def test_portfolio_overwrite_creates_recovery_backup(tmp_path) -> None:  # noqa: ANN001
    import ticker_dossier.research.paper_portfolio as portfolio

    portfolio.create_account(initial_cash=123_456, base_dir=tmp_path)
    portfolio.create_account(initial_cash=999_999, overwrite=True, base_dir=tmp_path)

    backups = list((tmp_path / "backups").glob("portfolio_default_*.json"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text(encoding="utf-8"))["initial_cash"] == 123_456


def test_daily_pnl_uses_reported_totals_for_recovered_ledger() -> None:
    from ticker_dossier.research.paper_portfolio import PortfolioAccount

    account = PortfolioAccount(
        name="recovered",
        initial_cash=1_000_000,
        cash=100_000,
        transactions=[{
            "as_of": "2026-07-09T03:00:00Z", "action": "SELL", "symbol": "MSFT",
            "amount": None, "realized_pnl": -2_728,
        }],
        history=[{
            "as_of": "2026-07-09T03:05:36Z", "event": "recovered_rebalance",
            "total_value": 1_009_905.90, "return_pct": 0.99059,
            "reported_buy_amount": 635_811.28, "reported_sell_amount": 631_415.52,
            "reported_realized_pnl": 7_533.53, "reported_trade_count": 8,
        }],
    )

    output = render_daily_pnl(account)

    assert "635,811.28" in output
    assert "631,415.52" in output
    assert "7,533.53" in output
    assert "| 8 |" in output


def test_paper_portfolio_rebalance_preserves_cost_basis(tmp_path) -> None:  # noqa: ANN001
    def snapshot(symbol: str, price: float) -> StockSnapshot:
        return StockSnapshot(
            symbol=symbol,
            quote=Quote(symbol=symbol, price=price, source="STATIC", as_of=utc_now_iso(), is_realtime=True),
            history=[],
            financials=Financials(
                symbol=symbol,
                source="STATIC",
                as_of=utc_now_iso(),
                pe_ratio=20,
                free_cash_flow=1_000_000,
                return_on_equity=0.18,
                profit_margin=0.22,
            ),
            news=[],
            indicators={"return_3m_pct": 12, "return_1y_pct": 24, "annualized_volatility_pct": 20},
            fetched_at=utc_now_iso(),
        )

    construct_portfolio([snapshot("AAPL", 100)], initial_cash=1_000_000, base_dir=tmp_path)
    rebalance_portfolio([snapshot("AAPL", 120), snapshot("MSFT", 100)], base_dir=tmp_path)
    account = load_account(base_dir=tmp_path)
    aapl = next(holding for holding in account.holdings if holding.symbol == "AAPL")

    assert aapl.avg_cost == 100
    assert {item["action"] for item in account.transactions} >= {"BUY", "SELL"}


def test_paper_portfolio_scores_penalize_weak_relative_strength() -> None:
    strong = StockSnapshot(
        symbol="STRONG",
        quote=Quote(symbol="STRONG", price=100, source="STATIC", as_of=utc_now_iso(), is_realtime=True),
        history=[],
        financials=Financials(symbol="STRONG", source="STATIC", as_of=utc_now_iso(), free_cash_flow=1, return_on_equity=0.2),
        news=[],
        indicators={"return_1m_pct": 6, "return_3m_pct": 18, "return_1y_pct": 35, "annualized_volatility_pct": 22},
        fetched_at=utc_now_iso(),
    )
    weak = StockSnapshot(
        symbol="WEAK",
        quote=Quote(symbol="WEAK", price=100, source="STATIC", as_of=utc_now_iso(), is_realtime=True),
        history=[],
        financials=Financials(symbol="WEAK", source="STATIC", as_of=utc_now_iso(), free_cash_flow=1, return_on_equity=0.35, profit_margin=0.36),
        news=[],
        indicators={"return_1m_pct": 1, "return_3m_pct": 3, "return_1y_pct": -24, "annualized_volatility_pct": 22},
        fetched_at=utc_now_iso(),
    )

    scores = {score.symbol: score for score in score_candidates([strong, weak])}

    assert scores["STRONG"].score > scores["WEAK"].score
    assert "弱" in scores["WEAK"].verdict or scores["WEAK"].score < 50
    assert any("相对弱势" in warning or "相对强度" in warning for warning in scores["WEAK"].warnings)


def test_portfolio_review_flags_weak_holding_and_replacements(tmp_path) -> None:  # noqa: ANN001
    weak_snapshot = StockSnapshot(
        symbol="WEAK",
        quote=Quote(symbol="WEAK", price=100, source="STATIC", as_of=utc_now_iso(), is_realtime=True),
        history=[],
        financials=Financials(symbol="WEAK", source="STATIC", as_of=utc_now_iso(), free_cash_flow=1, return_on_equity=0.35, profit_margin=0.36),
        news=[],
        indicators={"return_1m_pct": 1, "return_3m_pct": 2, "return_1y_pct": -25, "annualized_volatility_pct": 20},
        fetched_at=utc_now_iso(),
    )
    strong_snapshot = StockSnapshot(
        symbol="STRONG",
        quote=Quote(symbol="STRONG", price=100, source="STATIC", as_of=utc_now_iso(), is_realtime=True),
        history=[],
        financials=Financials(symbol="STRONG", source="STATIC", as_of=utc_now_iso(), free_cash_flow=1, return_on_equity=0.25, profit_margin=0.25),
        news=[],
        indicators={"return_1m_pct": 8, "return_3m_pct": 20, "return_1y_pct": 40, "annualized_volatility_pct": 20},
        fetched_at=utc_now_iso(),
    )
    account, _ = construct_portfolio([weak_snapshot], initial_cash=100_000, base_dir=tmp_path, min_score=30)
    output = render_portfolio_review(account, score_candidates([weak_snapshot, strong_snapshot]))

    assert "纸面组合诊断" in output
    assert "低置信持仓" in output
    assert "STRONG" in output


def test_portfolio_review_revalues_a_copy_and_reports_stale_price_fallback(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # noqa: ANN001
    import ticker_dossier.research.paper_portfolio as portfolio
    from ticker_dossier.research.paper_portfolio import Holding, PortfolioAccount, save_account

    user_dir = tmp_path / "home" / "portfolios"
    workspace_dir = tmp_path / "project" / ".finance_agent"
    account = PortfolioAccount(
        name="growth",
        initial_cash=1_000,
        cash=100,
        holdings=[
            Holding("AAPL", 5, 100, 90, 450, 0.50, "fresh quote test"),
            Holding("MSFT", 2, 200, 180, 360, 0.40, "fallback test"),
        ],
        created_at="2026-08-01T00:00:00Z",
        updated_at="2026-08-09T00:00:00Z",
    )
    path = save_account(account, base_dir=user_dir)
    before = path.read_bytes()
    monkeypatch.setattr(portfolio, "PORTFOLIO_DIR", user_dir)
    monkeypatch.setattr(portfolio, "LEGACY_PORTFOLIO_DIR", workspace_dir)

    def snapshot(symbol: str) -> StockSnapshot:
        price = None if symbol == "MSFT" else (120 if symbol == "AAPL" else 100)
        return StockSnapshot(
            symbol=symbol,
            quote=Quote(
                symbol=symbol,
                price=price,
                source="STATIC" if price is not None else "UNAVAILABLE",
                as_of="2026-08-11T08:00:00Z",
            ),
            history=[],
            financials=Financials(symbol=symbol, source="STATIC", as_of="2026-08-11T08:00:00Z"),
            news=[],
            indicators={},
            fetched_at="2026-08-11T08:00:01Z",
        )

    loaded = load_account("growth", create_if_missing=False)
    direct_valuation = value_account_read_only(loaded, [snapshot("AAPL"), snapshot("MSFT")])
    assert loaded.holdings[0].last_price == 90
    assert direct_valuation.account.holdings[0].last_price == 120

    agent = FinanceResearchAgent(provider=ProviderChain(providers=[]))
    monkeypatch.setattr(agent, "quick_snapshot", lambda symbol, period="6mo": snapshot(symbol))
    output = agent.review_paper_portfolio(name="growth")
    payload_after = json.loads(path.read_text(encoding="utf-8"))

    assert path.read_bytes() == before
    assert payload_after["holdings"][0]["last_price"] == 90
    assert "当前净值: 1,060.00" in output
    assert "累计收益: 60.00 (6.00%)" in output
    assert "估值行情时间: 2026-08-11T08:00:00Z" in output
    assert "AAPL | 120.00 | 600.00 | 56.60% | 20.00%" in output
    assert "MSFT | 180.00 | 360.00 | 33.96% | -10.00% | **缓存回退**" in output
    assert "陈旧价格回退" in output


def test_history_learning_generates_forecast_and_skill(tmp_path) -> None:  # noqa: ANN001
    rule = learn_from_history("AAPL", rising_candles(180), horizon_days=20)
    skill_path = update_history_learning_skill(rule, path=tmp_path / "skills" / "finance-history-learning" / "SKILL.md")
    output = render_learning(rule)

    assert rule.predicted_direction in {"up", "down", "neutral"}
    assert 0 <= rule.confidence <= 0.95
    assert "历史学习预测" in output
    assert skill_path.exists()
    assert "finance-history-learning" in skill_path.read_text(encoding="utf-8")


def test_route_task_learns_from_history(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    import ticker_dossier.research.history_learning as history_learning
    import ticker_dossier.research.agent as finance_agent

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(history_learning, "LEARNING_PATH", tmp_path / ".finance_agent" / "history_learning.jsonl")
    monkeypatch.setattr(history_learning, "SKILL_PATH", tmp_path / "skills" / "finance-history-learning" / "SKILL.md")
    monkeypatch.setattr(finance_agent, "save_learning", lambda rule: history_learning.save_learning(rule, tmp_path / ".finance_agent" / "history_learning.jsonl"))
    monkeypatch.setattr(finance_agent, "update_history_learning_skill", lambda rule: history_learning.update_history_learning_skill(rule, tmp_path / "skills" / "finance-history-learning" / "SKILL.md"))
    agent = FinanceResearchAgent(provider=ProviderChain(providers=[StaticProvider()]))

    output = agent.route_task("从历史数据中学习 AAPL 未来20天怎么走 并沉淀为 skill")

    assert "历史学习预测" in output
    assert "Skill updated" in output


def test_debate_includes_berkshire_style_roles() -> None:
    class OfflineBackend:
        def chat(self, messages, tools=None, temperature=0.0):  # noqa: ANN001, ANN201
            raise RuntimeError("offline test uses deterministic debate fallback")

    agent = FinanceResearchAgent(
        provider=ProviderChain(providers=[StaticProvider()]),
        debate_backend=OfflineBackend(),
    )

    output = agent.debate_stocks(["AAPL"])

    assert "Buffett Agent" in output
    assert "Munger Agent" in output
    assert "Duan Agent" in output
    assert "Li Lu Agent" in output
    assert "Anti-Bias Agent" in output
    assert "纪律结论" in output
    assert "镜子测试" in output
    assert "可检验预测" in output


def test_route_task_selects_quality_screen() -> None:
    agent = FinanceResearchAgent(provider=ProviderChain(providers=[StaticProvider()]))

    output = agent.route_task("帮我给 AAPL 做质量门禁和去劣初筛")

    assert "# 研究质量初筛" in output
    assert "信息丰富度" in output
    assert "快速否决/重审信号" in output


def test_route_task_selects_market_update_for_today_question(monkeypatch: pytest.MonkeyPatch) -> None:
    import ticker_dossier.research.agent as finance_agent

    monkeypatch.setattr(finance_agent, "web_search", lambda query, limit=5: f"搜索: {query}\n来源: fallback")
    agent = FinanceResearchAgent(provider=ProviderChain(providers=[StaticProvider()]))

    output = agent.route_task("看看智谱今天的情况")

    assert "# 今日市场核验" in output
    assert "上市状态与代码核验" in output
    assert "公开网页核验" in output
    assert "02513.HK" in output


def test_route_task_resolves_company_name_when_no_ticker(monkeypatch: pytest.MonkeyPatch) -> None:
    import ticker_dossier.research.agent as finance_agent

    monkeypatch.setattr(finance_agent, "web_search", lambda query, limit=5: f"搜索: {query}\n来源: fallback")
    monkeypatch.setattr(finance_agent, "_resolve_symbol", lambda value: "0100.HK" if "minimax" in value.lower() else normalize_symbol(value))
    agent = FinanceResearchAgent(provider=ProviderChain(providers=[StaticProvider()]))

    output = agent.route_task("minimax 股价怎么样")

    assert "# 今日市场核验" in output
    assert "0100.HK" in output


def test_web_search_returns_finance_fallback_on_request_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    def fail_get(self: httpx.Client, url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("GET", url)
        raise httpx.ConnectError("No route to host", request=request)

    monkeypatch.setattr(httpx.Client, "get", fail_get)

    output = web_search("智谱 02513 股票", 5)

    assert "搜索入口连接失败" in output
    assert "本地财经链接 fallback" in output
    assert "https://xueqiu.com/S/02513" in output


def test_web_fetch_revalidates_every_redirect_target(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx
    import ticker_dossier.research.web as web_module

    class RedirectClient:
        def __enter__(self):  # noqa: ANN204
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def get(self, url: str, **kwargs: object) -> httpx.Response:
            request = httpx.Request("GET", url)
            return httpx.Response(
                302,
                headers={"location": "http://127.0.0.1/private"},
                request=request,
            )

    monkeypatch.setattr(web_module, "http_client", lambda **kwargs: RedirectClient())

    with pytest.raises(SecurityError, match="白名单"):
        web_fetch("https://example.com/redirect")


def test_parse_strategy_and_backtest_return_metrics() -> None:
    config = parse_strategy("20 日均线上穿 60 日均线", initial_cash=50_000)
    candles = rising_candles(90)

    result = backtest_moving_average_cross(candles, config)

    assert config.fast_window == 20
    assert config.slow_window == 60
    assert result["strategy"] == "moving_average_cross"
    assert result["initial_cash"] == 50_000
    assert "total_return_pct" in result


def test_prediction_record_and_evaluation(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "predictions.jsonl"

    record = record_prediction(
        symbol="AAPL",
        direction="up",
        horizon_days=30,
        confidence=0.7,
        thesis="unit test",
        baseline_price=100,
        baseline_as_of="2026-01-01T00:00:00Z",
        path=path,
    )
    evaluated = evaluate_prediction(record, evaluation_price=110)

    assert evaluated.hit is True
    assert evaluated.return_pct == 10
    assert evaluated.score and evaluated.score > 0


def test_prediction_learning_report_groups_evaluated_records(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "predictions.jsonl"
    first = record_prediction(
        symbol="AAPL",
        direction="up",
        horizon_days=30,
        confidence=0.8,
        thesis="unit win",
        baseline_price=100,
        path=path,
    )
    second = record_prediction(
        symbol="MSFT",
        direction="down",
        horizon_days=30,
        confidence=0.9,
        thesis="unit miss",
        baseline_price=100,
        path=path,
    )
    evaluate_prediction(first, evaluation_price=110)
    evaluate_prediction(second, evaluation_price=105)

    output = render_learning_report([first, second])

    assert "Prediction scorecard" in output
    assert "Direction buckets" in output
    assert "high-estimate misses" in output


def test_parse_strategy_notes_reordered_windows() -> None:
    config = parse_strategy("60 日均线上穿 20 日均线")
    result = backtest_moving_average_cross(rising_candles(90), config)
    output = format_backtest(result)

    assert config.fast_window == 20
    assert config.slow_window == 60
    assert "已规范化为快线 MA20、慢线 MA60" in output
    assert output.count("已规范化为快线 MA20、慢线 MA60") == 1


def test_news_filter_rejects_unrelated_titles() -> None:
    keywords = ["aapl", "apple"]

    assert _news_matches({"title": "Apple unveils new services"}, keywords)
    assert not _news_matches({"title": "Why Intel stock is up today"}, keywords)


def test_report_skips_framework_scoring_for_sample_financials() -> None:
    snapshot = StockSnapshot(
        symbol="AAPL",
        quote=Quote(symbol="AAPL", source="Yahoo Finance public endpoints", as_of=utc_now_iso(), price=100),
        history=[],
        financials=Financials(symbol="AAPL", source="SAMPLE_FALLBACK", as_of=utc_now_iso(), pe_ratio=20),
        news=[],
        indicators={},
        fetched_at=utc_now_iso(),
    )

    output = render_stock_report(snapshot)

    assert "暂不对护城河、现金流、安全边际等框架打分" in output
    assert "评分" not in output


def test_report_includes_research_quality_gate() -> None:
    snapshot = StockSnapshot(
        symbol="AAPL",
        quote=Quote(symbol="AAPL", source="STATIC", as_of=utc_now_iso(), price=100, is_realtime=True),
        history=rising_candles(120),
        financials=Financials(
            symbol="AAPL",
            source="STATIC",
            as_of=utc_now_iso(),
            pe_ratio=20,
            free_cash_flow=1_000_000,
            return_on_equity=0.18,
            profit_margin=0.12,
        ),
        news=[NewsItem(title="Static headline")],
        indicators={},
        fetched_at=utc_now_iso(),
    )

    output = render_stock_report(snapshot)

    assert "## 研究质量门禁" in output
    assert "信息丰富度" in output
    assert "下一步核验" in output


def test_quality_screen_labels_low_confidence_data() -> None:
    snapshot = StockSnapshot(
        symbol="AAPL",
        quote=Quote(symbol="AAPL", source="UNAVAILABLE", as_of=utc_now_iso()),
        history=[],
        financials=Financials(symbol="AAPL", source="UNAVAILABLE", as_of=utc_now_iso()),
        news=[],
        indicators={},
        fetched_at=utc_now_iso(),
    )

    output = render_quality_screen(snapshot)

    assert "信息丰富度: C" in output
    assert "核心数据置信度不足" in output
    assert "数据缺口" in output


def test_utc_now_iso_uses_z_suffix() -> None:
    value = utc_now_iso()

    assert value.endswith("Z")
    assert "+00:00" not in value


class FailingProvider:
    name = "FAILING"

    def get_quote(self, symbol: str) -> Quote:
        raise ProviderError("quote failed")

    def get_history(self, symbol: str, period: str = "1y", interval: str = "1d") -> list[Candle]:
        raise ProviderError("history failed")

    def get_financials(self, symbol: str) -> Financials:
        raise ProviderError("financials failed")

    def get_news(self, symbol: str, limit: int = 5) -> list[NewsItem]:
        raise ProviderError("news failed")


class StaticProvider:
    name = "STATIC"

    def get_quote(self, symbol: str) -> Quote:
        return Quote(symbol=normalize_symbol(symbol), price=123.45, source=self.name, as_of="2026-01-01T00:00:00Z")

    def get_history(self, symbol: str, period: str = "1y", interval: str = "1d") -> list[Candle]:
        return rising_candles(120)

    def get_financials(self, symbol: str) -> Financials:
        return Financials(
            symbol=normalize_symbol(symbol),
            source=self.name,
            as_of="2026-01-01T00:00:00Z",
            market_cap=1_000_000_000,
            pe_ratio=20,
            eps=5,
            revenue=100_000_000,
            net_income=20_000_000,
            return_on_equity=0.18,
        )

    def get_news(self, symbol: str, limit: int = 5) -> list[NewsItem]:
        return [NewsItem(title="Static headline", publisher="STATIC", published_at="2026-01-01", source=self.name)]


def rising_candles(count: int) -> list[Candle]:
    start = date(2025, 1, 1)
    candles: list[Candle] = []
    for index in range(count):
        close = 100 + index
        candles.append(Candle(
            date=(start + timedelta(days=index)).isoformat(),
            open=close - 1,
            high=close + 1,
            low=close - 2,
            close=close,
            volume=1_000_000,
        ))
    return candles


def test_route_task_multi_symbol_request_outputs_both() -> None:
    agent = FinanceResearchAgent(provider=ProviderChain(providers=[StaticProvider()]))

    output = agent.route_task("分析 AAPL MSFT")

    assert "AAPL" in output
    assert "MSFT" in output


def test_route_task_records_five_real_baseline_predictions(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    import ticker_dossier.research.predictions as predictions

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(predictions, "PREDICTION_PATH", tmp_path / "predictions.jsonl")
    agent = FinanceResearchAgent(provider=ProviderChain(providers=[StaticProvider()]))

    output = agent.route_task("预测 AAPL MSFT NVDA AMD TSLA 方向看涨，置信度 75%")
    records = load_predictions(tmp_path / "predictions.jsonl")

    assert len(records) == 5
    assert all(record.direction == "up" and record.confidence == 0.75 for record in records)
    assert all(record.baseline_price == 219 and record.source == "STATIC" for record in records)
    assert all(record.symbol in output for record in records)


def test_live_trade_refusal_is_deterministic_and_does_not_send(monkeypatch: pytest.MonkeyPatch) -> None:
    import ticker_dossier.integrations.wechat as connector

    monkeypatch.setattr(connector, "send_text", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not send")))
    agent = FinanceResearchAgent(provider=ProviderChain(providers=[StaticProvider()]))

    first = agent.route_task("请实盘帮我买 AAPL 并微信通知")
    second = agent.route_task("请实盘帮我买 AAPL 并微信通知")

    assert first == second
    assert "已拒绝实盘交易请求" in first
    assert "dry-run" in first


def test_provider_chain_merges_quality_fundamentals_without_sample() -> None:
    class ThinProvider(StaticProvider):
        name = "THIN"
        def get_financials(self, symbol: str) -> Financials:
            return Financials(symbol=symbol, source=self.name, as_of=utc_now_iso(), market_cap=10)

    class RichProvider(StaticProvider):
        name = "RICH"
        def get_financials(self, symbol: str) -> Financials:
            return Financials(symbol=symbol, source=self.name, as_of=utc_now_iso(), revenue=20, net_income=4, return_on_equity=0.2)

    financials = ProviderChain(providers=[ThinProvider(), RichProvider(), SampleDataProvider()]).get_financials("AAPL")

    assert financials.market_cap == 10
    assert financials.revenue == 20
    assert "SAMPLE_FALLBACK" not in financials.source


def test_debate_persists_prediction(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    import ticker_dossier.research.predictions as predictions

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(predictions, "PREDICTION_PATH", tmp_path / "predictions.jsonl")
    agent = FinanceResearchAgent(provider=ProviderChain(providers=[StaticProvider()]))

    output = agent.debate_stocks(["AAPL"])
    records = load_predictions(tmp_path / "predictions.jsonl")

    assert len(records) == 1
    assert records[0].source == "debate"
    assert records[0].baseline_price == 123.45
    assert "Predictions recorded" in output


def test_due_prediction_evaluation_continues_after_bad_record(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "predictions.jsonl"
    bad = record_prediction(symbol="BAD", direction="up", horizon_days=1, confidence=.5, thesis="bad", baseline_price=10, path=path)
    good = record_prediction(symbol="GOOD", direction="up", horizon_days=1, confidence=.5, thesis="good", baseline_price=10, path=path)
    bad.due_at = "not-a-date"
    good.due_at = "2020-01-01T00:00:00Z"
    from ticker_dossier.research.predictions import save_predictions
    save_predictions([bad, good], path)

    def get_price(symbol: str) -> tuple[float, str]:
        if symbol == "BAD":
            raise ProviderError("unavailable")
        return 12, "2026-01-01T00:00:00Z"

    evaluated, card = evaluate_due_predictions(get_price=get_price, path=path)

    assert len(evaluated) == 2
    assert evaluated[0].notes.startswith("evaluation failed")
    assert evaluated[1].hit is True
    assert card["evaluated"] == 1
