"""High-level finance research facade used by tools and CLI."""
from __future__ import annotations

from functools import wraps
import re

from ticker_dossier.security import redact_sensitive_text

from ticker_dossier.market_data import ProviderChain, enrich_financial_pe, export_history_csv
from ticker_dossier.market_data.models import Candle, Financials, Quote, StockSnapshot, utc_now_iso
from ticker_dossier.market_data.symbols import extract_symbols, normalize_symbol
from ticker_dossier.portfolio.service import (
    construct_portfolio,
    load_account,
    mark_to_market,
    migrate_account,
    rebalance_portfolio,
    render_account,
    render_account_locations,
    render_daily_pnl,
    render_portfolio_review,
    render_portfolio_migration,
    render_recommendation,
    render_transactions,
    sell_holding,
    score_candidates,
    value_account_read_only,
)

from .analysis.backtest import backtest_moving_average_cross, format_backtest, parse_strategy
from .analysis.indicators import calculate_indicators, format_indicators
from .analysis.quality import render_quality_screen
from .debate.orchestrator import ModelDebateOrchestrator, render_debate_outcomes
from .discovery.resolver import resolve_symbol, resolve_symbol_text
from .discovery.web import web_search
from .learning.history import (
    calibrate_momentum_signal,
    learn_from_history,
    render_learning,
    save_learning,
    update_history_learning_skill,
)
from .learning.predictions import PredictionRecord, record_prediction, render_prediction_record
from .protocols import DebateBackend, DebateBackendFactory
from .reporting import render_comparison, render_daily_brief, render_financials, render_stock_report


def _with_provider_request_deadline(method):  # noqa: ANN001, ANN201
    @wraps(method)
    def wrapped(self, *args, **kwargs):  # noqa: ANN001, ANN202
        deadline = getattr(self.provider, "request_deadline", None)
        if not callable(deadline):
            return method(self, *args, **kwargs)
        with deadline():
            return method(self, *args, **kwargs)

    return wrapped


class FinanceResearchAgent:
    def __init__(
        self,
        provider: ProviderChain | None = None,
        debate_backend: DebateBackend | None = None,
        *,
        debate_backend_factory: DebateBackendFactory | None = None,
    ):
        self._owns_provider = provider is None
        self.provider = ProviderChain() if provider is None else provider
        self.debate_backend = debate_backend
        self.debate_backend_factory = debate_backend_factory

    def close(self) -> None:
        """Close only the provider lifecycle created by this facade."""
        if not self._owns_provider:
            return
        close = getattr(self.provider, "close", None)
        if callable(close):
            close()

    @_with_provider_request_deadline
    def snapshot(self, symbol: str, period: str = "1y", news_limit: int = 5) -> StockSnapshot:
        reset_coverage = getattr(self.provider, "reset_coverage", None)
        if callable(reset_coverage):
            reset_coverage()
        normalized = _resolve_symbol(symbol)
        errors: list[str] = []
        try:
            quote = self.provider.get_quote(normalized)
        except Exception as exc:
            quote = Quote(
                symbol=normalized,
                source="UNAVAILABLE",
                as_of=utc_now_iso(),
                is_realtime=False,
                notes=[f"行情获取失败: {_compact_error(exc)}"],
            )
            errors.append(f"行情获取失败: {_compact_error(exc)}")

        try:
            history = self.provider.get_history(normalized, period, "1d")
        except Exception as exc:
            history = []
            errors.append(f"历史价格获取失败: {_compact_error(exc)}")

        try:
            financials = self.provider.get_financials(normalized)
        except Exception as exc:
            financials = Financials(
                symbol=normalized,
                source="UNAVAILABLE",
                as_of=utc_now_iso(),
                notes=[f"基本面获取失败: {_compact_error(exc)}"],
            )
            errors.append(f"基本面获取失败: {_compact_error(exc)}")

        try:
            news = self.provider.get_news(normalized, news_limit) if news_limit > 0 else []
        except Exception as exc:
            news = []
            errors.append(f"新闻获取失败: {_compact_error(exc)}")

        if financials.market_cap is None and quote.market_cap is not None:
            financials.market_cap = quote.market_cap
            financials.field_sources["market_cap"] = _quote_field_source(quote, "market_cap")
        if financials.pe_ratio is None and quote.pe_ratio is not None:
            financials.pe_ratio = quote.pe_ratio
            financials.field_sources["pe_ratio"] = _quote_field_source(quote, "pe_ratio")
        if financials.eps is None and quote.eps is not None:
            financials.eps = quote.eps
            financials.field_sources["eps"] = _quote_field_source(quote, "eps")
        enrich_financial_pe(financials, quote)
        if errors:
            _extend_unique(quote.notes, errors)
        report_notes = getattr(self.provider, "report_notes", None)
        if callable(report_notes):
            _extend_unique(quote.notes, report_notes())

        indicators = calculate_indicators(history)
        return StockSnapshot(
            symbol=normalized,
            quote=quote,
            history=history,
            financials=financials,
            news=news,
            indicators=indicators,
            fetched_at=utc_now_iso(),
            source_coverage=_source_coverage(self.provider),
        )

    @_with_provider_request_deadline
    def quick_snapshot(self, symbol: str, period: str = "6mo") -> StockSnapshot:
        reset_coverage = getattr(self.provider, "reset_coverage", None)
        if callable(reset_coverage):
            reset_coverage()
        normalized = _resolve_symbol(symbol)
        errors: list[str] = []
        try:
            quote = self.provider.get_quote(normalized)
        except Exception as exc:
            quote = Quote(
                symbol=normalized,
                source="UNAVAILABLE",
                as_of=utc_now_iso(),
                is_realtime=False,
                notes=[f"行情获取失败: {_compact_error(exc)}"],
            )
            errors.append(f"行情获取失败: {_compact_error(exc)}")
        try:
            history = self.provider.get_history(normalized, period, "1d")
        except Exception as exc:
            history = []
            errors.append(f"历史价格获取失败: {_compact_error(exc)}")
        financials = Financials(
            symbol=normalized,
            source="SKIPPED",
            as_of=quote.as_of,
            currency=quote.currency,
            period_type="quote",
            fetched_at=utc_now_iso(),
            market_cap=quote.market_cap,
            pe_ratio=quote.pe_ratio,
            eps=quote.eps,
            field_sources={
                name: _quote_field_source(quote, name)
                for name, value in (
                    ("market_cap", quote.market_cap),
                    ("pe_ratio", quote.pe_ratio),
                    ("eps", quote.eps),
                )
                if value is not None
            },
            notes=["quick review skips full fundamentals for speed"],
        )
        if errors:
            _extend_unique(quote.notes, errors)
        report_notes = getattr(self.provider, "report_notes", None)
        if callable(report_notes):
            _extend_unique(quote.notes, report_notes())
        return StockSnapshot(
            symbol=normalized,
            quote=quote,
            history=history,
            financials=financials,
            news=[],
            indicators=calculate_indicators(history),
            fetched_at=utc_now_iso(),
            source_coverage=_source_coverage(self.provider),
        )

    def get_quote(self, symbol: str) -> str:
        normalized = _resolve_symbol(symbol)
        quote = self.provider.get_quote(normalized)
        return "\n".join([
            f"标的: {normalized} {quote.name}".strip(),
            f"价格: {quote.price} {quote.currency}",
            f"涨跌: {quote.change} ({quote.change_percent}%)",
            f"成交量: {quote.volume}",
            f"数据源: {quote.source}",
            f"时间: {quote.as_of}",
            f"实时/准实时: {'是' if quote.is_realtime else '否'}",
            *[f"备注: {note}" for note in quote.notes],
        ])

    def get_price_history(self, symbol: str, period: str = "1y", format: str = "summary") -> str:
        normalized = _resolve_symbol(symbol)
        try:
            history = self.provider.get_history(normalized, period, "1d")
        except Exception as exc:
            return f"{normalized}: 历史价格获取失败: {_compact_error(exc)}"
        if format == "csv":
            return export_history_csv(history)
        indicators = calculate_indicators(history)
        return "\n".join([
            f"标的: {normalized}",
            f"数据点: {len(history)}",
            f"起止: {history[0].date if history else '无'} 到 {history[-1].date if history else '无'}",
            format_indicators(indicators),
        ])

    def get_financials(self, symbol: str) -> str:
        normalized = _resolve_symbol(symbol)
        try:
            financials = self.provider.get_financials(normalized)
        except Exception as exc:
            return f"{normalized}: 基本面获取失败: {_compact_error(exc)}"
        return "\n".join([
            f"# 基本面：{normalized}",
            render_financials(financials),
            *[f"备注: {note}" for note in financials.notes],
        ])

    def get_news(self, symbol: str, limit: int = 5) -> str:
        normalized = _resolve_symbol(symbol)
        try:
            news = self.provider.get_news(normalized, limit)
        except Exception as exc:
            error = _compact_error(exc)
            if "empty result" in error:
                return f"{normalized}: 未找到与该标的强相关的新闻；新闻接口可能返回了噪声或被过滤。"
            return f"{normalized}: 新闻获取失败: {error}"
        if not news:
            return f"{normalized}: 暂无新闻数据。"
        lines = [f"{normalized} 新闻:"]
        for item in news:
            lines.append(f"- {item.title} ({item.publisher}, {item.published_at})")
            if item.link:
                lines.append(f"  {item.link}")
        return "\n".join(lines)

    def calculate_indicators(self, symbol: str, period: str = "1y") -> str:
        normalized = _resolve_symbol(symbol)
        try:
            history = self.provider.get_history(normalized, period, "1d")
        except Exception as exc:
            return f"{normalized}: 技术指标计算失败，历史价格获取失败: {_compact_error(exc)}"
        return format_indicators(calculate_indicators(history))

    def generate_report(self, symbol: str, period: str = "1y") -> str:
        return render_stock_report(self.snapshot(symbol, period, 5))

    def quality_screen(self, symbol: str, period: str = "1y") -> str:
        return render_quality_screen(self.snapshot(symbol, period, 5))

    def compare_stocks(self, symbols: list[str] | str, period: str = "1y") -> str:
        symbol_list = _coerce_symbols(symbols)
        snapshots = [self.snapshot(symbol, period, 3) for symbol in symbol_list]
        return render_comparison(snapshots)

    def debate_stocks(self, symbols: list[str] | str, period: str = "1y") -> str:
        symbol_list = _coerce_symbols(symbols)
        snapshots = [self.snapshot(symbol, period, 3) for symbol in symbol_list]
        outcomes = ModelDebateOrchestrator(
            backend=self.debate_backend,
            backend_factory=self.debate_backend_factory,
        ).run(snapshots)
        output = render_debate_outcomes(outcomes)
        recorded: list[str] = []
        for snapshot, outcome in zip(snapshots, outcomes):
            judge = outcome.judge
            thesis = (
                f"{judge.conclusion} 核心分歧: {judge.core_disagreement} "
                f"证据: {', '.join(judge.supporting_evidence) or '规则兜底，无模型证据引用'}"
            )
            prediction = record_prediction(
                symbol=snapshot.symbol,
                direction=judge.direction,
                horizon_days=judge.horizon_days,
                confidence=judge.confidence,
                confidence_kind="model_supplied" if outcome.mode == "model" else "heuristic_signal",
                signal_strength=judge.confidence,
                thesis=thesis,
                baseline_price=snapshot.quote.price,
                baseline_as_of=snapshot.quote.as_of,
                source="debate",
            )
            mode = "model judge" if outcome.mode == "model" else "rule fallback"
            recorded.append(
                f"{prediction.symbol}:{prediction.id} "
                f"({mode}, {prediction.direction}, signal {prediction.confidence * 100:.0f}/100, not probability)"
            )
        return output + "\n\nPredictions recorded: " + ", ".join(recorded)

    def backtest_strategy(
        self,
        symbol: str,
        strategy: str | None = None,
        period: str = "2y",
        fast_window: int | None = None,
        slow_window: int | None = None,
        initial_cash: float = 100_000.0,
    ) -> str:
        config = parse_strategy(
            strategy,
            fast_window=fast_window,
            slow_window=slow_window,
            initial_cash=initial_cash,
        )
        try:
            history = self.provider.get_history(normalize_symbol(symbol), period, "1d")
        except Exception as exc:
            return f"{normalize_symbol(symbol)}: 回测失败，历史价格获取失败: {_compact_error(exc)}"
        result = backtest_moving_average_cross(history, config)
        return format_backtest(result)

    def daily_brief(self, symbols: list[str] | str, period: str = "3mo") -> str:
        symbol_list = _coerce_symbols(symbols)
        snapshots = [self.snapshot(symbol, period, 2) for symbol in symbol_list]
        return render_daily_brief(snapshots)

    def build_paper_portfolio(
        self,
        symbols: list[str] | str,
        initial_cash: float = 1_000_000.0,
        period: str = "1y",
        max_positions: int = 5,
        name: str = "default",
    ) -> str:
        symbol_list = _coerce_symbols(symbols)
        snapshots = [self.snapshot(symbol, period, 3) for symbol in symbol_list]
        account, scores = construct_portfolio(
            snapshots,
            initial_cash=initial_cash,
            max_positions=max_positions,
            name=name,
        )
        return render_recommendation(account, scores)

    def rebalance_paper_portfolio(
        self,
        symbols: list[str] | str,
        period: str = "1y",
        max_positions: int = 5,
        name: str = "default",
    ) -> str:
        symbol_list = _coerce_symbols(symbols)
        snapshots = [self.snapshot(symbol, period, 3) for symbol in symbol_list]
        account, scores = rebalance_portfolio(
            snapshots,
            name=name,
            max_positions=max_positions,
        )
        return render_recommendation(account, scores)

    def mark_paper_portfolio(self, name: str = "default") -> str:
        account = mark_to_market(get_quote=self.provider.get_quote, name=name)
        return render_account(account)

    def show_paper_portfolio(self, name: str = "default") -> str:
        return render_account(load_account(name, create_if_missing=False))

    def locate_paper_portfolio(self, name: str = "default") -> str:
        return render_account_locations(name)

    def migrate_paper_portfolio(self, name: str = "default") -> str:
        return render_portfolio_migration(migrate_account(name))

    def sell_paper_holding(
        self,
        symbol: str,
        shares: float | str = "all",
        name: str = "default",
        reason: str = "manual sell",
    ) -> str:
        normalized = _resolve_symbol(symbol)
        try:
            quote = self.provider.get_quote(normalized)
            price = quote.price
        except Exception:
            price = None
        account = sell_holding(normalized, shares=shares, price=price, reason=reason, name=name)
        return render_account(account) + "\n\n" + render_transactions(account, 10)

    def paper_trades(self, name: str = "default", limit: int = 30) -> str:
        return render_transactions(load_account(name, create_if_missing=False), limit)

    def paper_daily_pnl(self, name: str = "default", limit: int = 30) -> str:
        return render_daily_pnl(load_account(name, create_if_missing=False), limit)

    def review_paper_portfolio(
        self,
        symbols: list[str] | str | None = None,
        period: str = "6mo",
        name: str = "default",
    ) -> str:
        account = load_account(name, create_if_missing=False)
        candidates = _coerce_symbols(symbols or []) if symbols else []
        current = [holding.symbol for holding in account.holdings]
        fallback = [] if candidates else ["AAPL", "MSFT", "NVDA", "AMD", "GOOGL", "AVGO", "TSLA", "JPM", "V"]
        symbol_list = _unique_symbols([*current, *candidates, *fallback])
        snapshots: list[StockSnapshot] = []
        failures: list[str] = []
        for symbol in symbol_list:
            try:
                snapshots.append(self.quick_snapshot(symbol, period))
            except Exception as exc:
                failures.append(f"{symbol}: {_compact_error(exc)}")
        scores = score_candidates(snapshots)
        valuation = value_account_read_only(account, snapshots)
        output = render_portfolio_review(account, scores, valuation=valuation)
        if failures:
            output += "\n\n## 数据失败\n" + "\n".join(f"- {item}" for item in failures[:8])
        return output

    def learn_from_history(
        self,
        symbol: str,
        period: str = "2y",
        horizon_days: int = 20,
        record: bool = True,
        update_skill: bool = True,
    ) -> str:
        normalized = _resolve_symbol(symbol)
        try:
            history = self.provider.get_history(normalized, period, "1d")
        except Exception as exc:
            return f"{normalized}: 历史学习失败，历史价格获取失败: {_compact_error(exc)}"
        rule = learn_from_history(normalized, history, horizon_days=horizon_days)
        save_path = save_learning(rule)
        skill_path = update_history_learning_skill(rule) if update_skill else None
        prediction_line = ""
        if record and history and history[-1].close is not None:
            prediction = record_prediction(
                symbol=normalized,
                direction=rule.predicted_direction,
                horizon_days=horizon_days,
                confidence=rule.confidence,
                confidence_kind="heuristic_signal",
                signal_strength=rule.confidence,
                thesis=f"history-learning expected_return={rule.expected_return_pct:.2f}% features={rule.current_features}",
                baseline_price=float(history[-1].close),
                baseline_as_of=history[-1].date,
                source="history-learning",
            )
            prediction_line = f"\n\nPrediction recorded: {prediction.id} due={prediction.due_at}"
        lines = [
            render_learning(rule),
            "",
            f"Learning saved: {save_path}",
        ]
        if skill_path:
            lines.append(f"Skill updated: {skill_path}")
        if prediction_line:
            lines.append(prediction_line.strip())
        return "\n".join(lines)

    def route_task(self, task: str) -> str:
        symbols = extract_symbols(task)
        if _is_live_trade_task(task):
            return _live_trade_refusal(symbols)
        if not symbols and _looks_like_stock_task(task):
            symbols = [_resolve_symbol(_extract_name_query(task))]
        if not symbols:
            symbols = ["AAPL"]
        lowered = task.lower()
        period = _extract_period(task)
        if _is_portfolio_review_task(task):
            return self.review_paper_portfolio(symbols, period or "6mo")
        if _is_portfolio_task(task):
            return self.build_paper_portfolio(symbols, _extract_cash(task) or 1_000_000.0, period or "1y")
        if _is_market_update_task(task):
            return self.market_update_task(task, symbols[0])
        if any(word in task for word in ("标的", "代码", "上市", "是不是上市", "已经上市")):
            return self.verify_symbol_task(task, symbols[0])
        if any(word in task for word in ("回测", "策略")) or "backtest" in lowered:
            return self.backtest_strategy(symbols[0], task, period or "2y")
        if _is_history_learning_task(task):
            return self.learn_from_history(symbols[0], period or "2y", _extract_horizon(task) or 20)
        if any(word in task for word in ("简报", "自选股")) or "brief" in lowered:
            return self.daily_brief(symbols, period or "3mo")
        if any(word in task for word in ("质量门禁", "去劣", "初筛", "checklist", "quality")):
            return self.quality_screen(symbols[0], period or "1y")
        if _is_prediction_task(task):
            return self.record_task_predictions(task, symbols)
        if any(word in task for word in ("比较", "对比")) or "compare" in lowered:
            return self.compare_stocks(symbols, period or "1y")
        if any(word in task for word in ("辩论", "选股", "debate")):
            return self.debate_stocks(symbols, period or "1y")
        if len(symbols) > 1:
            return self.compare_stocks(symbols, period or "1y")
        return self.generate_report(symbols[0], period or "1y")

    def record_task_predictions(self, task: str, symbols: list[str]) -> str:
        requested_direction = _extract_direction(task)
        requested_confidence = _extract_confidence(task)
        horizon = _extract_horizon(task) or 30
        lines = ["Prediction records (saved to scorecard):"]
        for symbol in _unique_symbols(symbols):
            try:
                prediction = self.create_prediction_record(
                    symbol=symbol,
                    direction=requested_direction,
                    horizon_days=horizon,
                    signal_strength=requested_confidence,
                    signal_source="user_supplied" if requested_confidence is not None else "heuristic_signal",
                    use_calibration=requested_confidence is None,
                    thesis=task,
                )
                lines.extend(["", render_prediction_record(prediction)])
            except Exception as exc:  # noqa: BLE001 - keep other symbols recordable
                lines.append(f"- {symbol}: 未记录（真实基准价格不可用：{_compact_error(exc)}）")
        return "\n".join(lines)

    def create_prediction_record(
        self,
        *,
        symbol: str,
        direction: str | None = None,
        horizon_days: int = 30,
        signal_strength: float | None = None,
        signal_source: str = "heuristic_signal",
        use_calibration: bool = True,
        thesis: str = "",
    ) -> PredictionRecord:
        normalized = _resolve_symbol(symbol)
        baseline_price, baseline_as_of, source, history, indicators = self._prediction_inputs(normalized)
        calibration = calibrate_momentum_signal(
            history,
            horizon_days=horizon_days,
            direction=direction,
        )
        final_direction = direction or calibration.direction
        raw_strength = signal_strength if signal_strength is not None else calibration.signal_strength
        if use_calibration and calibration.calibrated_probability is not None:
            estimate = calibration.calibrated_probability
            confidence_kind = "historical_calibrated"
        else:
            estimate = raw_strength
            confidence_kind = signal_source if signal_strength is not None else "heuristic_signal"
        technical_summary = indicators.get("trend_summary", "暂无技术摘要")
        return record_prediction(
            symbol=normalized,
            direction=final_direction,
            horizon_days=horizon_days,
            confidence=estimate,
            confidence_kind=confidence_kind,
            signal_strength=raw_strength,
            calibrated_probability=calibration.calibrated_probability,
            calibration_samples=calibration.sample_count,
            calibration_hits=calibration.hits,
            calibration_interval_low=calibration.interval_low,
            calibration_interval_high=calibration.interval_high,
            calibration_method=calibration.method,
            thesis=f"{thesis or 'prediction'}; indicators={technical_summary}",
            baseline_price=baseline_price,
            baseline_as_of=baseline_as_of,
            source=source,
        )

    @_with_provider_request_deadline
    def _prediction_inputs(self, symbol: str) -> tuple[float, str, str, list[Candle], dict]:
        reset_coverage = getattr(self.provider, "reset_coverage", None)
        if callable(reset_coverage):
            reset_coverage()
        try:
            history = self.provider.get_history(symbol, "5y", "1d")
        except Exception:
            history = []
        latest = next((candle for candle in reversed(history) if candle.close is not None), None)
        if latest is not None:
            coverage = _source_coverage(self.provider).get("get_history", {})
            source = str(coverage.get("selected_source") or getattr(self.provider, "name", "HISTORY"))
            return float(latest.close), latest.date, source, history, calculate_indicators(history)

        quote = self.provider.get_quote(symbol)
        if quote.price is None:
            raise ValueError("行情和历史价格均无可用基准价")
        return float(quote.price), quote.as_of, quote.source or "QUOTE", history, calculate_indicators(history)

    def verify_symbol_task(self, task: str, symbol: str) -> str:
        normalized = _resolve_symbol(symbol)
        query = _verification_query(task, normalized)
        parts = [
            "# 标的核验",
            "",
            f"- 识别标的: {normalized}",
            "",
            "## 公开网页搜索",
            web_search(query, 5),
            "",
            "## 行情核验",
            self.get_quote(normalized),
            "",
            "说明：网页搜索用于确认上市状态、代码和公开页面；行情数据仍以返回的数据源、时间和备注为准。",
        ]
        return "\n".join(parts)

    def market_update_task(self, task: str, symbol: str) -> str:
        """Return a source-first market update for today's/latest status questions."""
        normalized = _resolve_symbol(symbol)
        query = _verification_query(task, normalized)
        parts = [
            "# 今日市场核验",
            "",
            f"- 识别标的: {normalized}",
            "",
            "## 上市状态与代码核验",
            web_search(query, 5),
            "",
            "## 公开网页核验",
            web_search(f"{task} {normalized} latest news stock", 5),
            "",
            "## 行情快照",
            self.get_quote(normalized),
            "",
            "## 近期技术面",
            self.calculate_indicators(normalized, "3mo"),
            "",
            "## 新闻摘要",
            self.get_news(normalized, 5),
            "",
            "## 边界",
            "- 以上只做研究核验，不构成买卖建议。",
            "- 若网页搜索入口失败，请优先使用返回的公开财经页面链接或 /fetch URL 交叉验证。",
        ]
        return "\n".join(parts)

    def resolve_symbol(self, query: str, limit: int = 8) -> str:
        return resolve_symbol_text(query, limit)


def _coerce_symbols(symbols: list[str] | str) -> list[str]:
    if isinstance(symbols, str):
        extracted = extract_symbols(symbols)
        if extracted:
            return extracted
        raw = [part.strip() for part in symbols.replace("，", ",").split(",")]
        return [_resolve_symbol(part) for part in raw if part]
    return [_resolve_symbol(symbol) for symbol in symbols if symbol]


def _unique_symbols(symbols: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for symbol in symbols:
        normalized = _resolve_symbol(symbol)
        key = normalized.upper()
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def _extract_period(task: str) -> str:
    lowered = task.lower()
    if any(token in task for token in ("三个月", "3个月", "近三月", "最近三月")) or "3mo" in lowered:
        return "3mo"
    if any(token in task for token in ("一个月", "1个月", "近一月", "最近一月")) or "1mo" in lowered:
        return "1mo"
    if any(token in task for token in ("半年", "六个月", "6个月")) or "6mo" in lowered:
        return "6mo"
    if any(token in task for token in ("两年", "2年", "过去两年")) or "2y" in lowered:
        return "2y"
    if any(token in task for token in ("五年", "5年", "过去五年")) or "5y" in lowered:
        return "5y"
    return ""


def _extract_cash(task: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*(万|w|W)", task)
    if match:
        return float(match.group(1)) * 10_000
    match = re.search(r"(\d+(?:\.\d+)?)\s*(百万|million)", task, flags=re.IGNORECASE)
    if match:
        return float(match.group(1)) * 1_000_000
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:元|cash|资金)", task, flags=re.IGNORECASE)
    if match:
        return float(match.group(1))
    if "一百万" in task:
        return 1_000_000.0
    return None


def _extract_horizon(task: str) -> int | None:
    match = re.search(r"未来\s*(\d{1,3})\s*天", task)
    if match:
        return int(match.group(1))
    match = re.search(r"(\d{1,3})\s*(?:day|days|d)\b", task, flags=re.IGNORECASE)
    if match:
        return int(match.group(1))
    if "一个月" in task or "1个月" in task:
        return 20
    if "三个月" in task or "3个月" in task:
        return 60
    return None


def _is_prediction_task(task: str) -> bool:
    lowered = task.lower()
    has_direction = any(token in task for token in ("看涨", "看跌", "上涨", "下跌", "涨跌", "方向")) or any(
        token in lowered for token in ("bullish", "bearish", "direction", "prediction")
    )
    wants_record = any(token in task for token in ("置信", "把握", "预测", "记录", "记到", "评分表"))
    return has_direction and wants_record


def _extract_direction(task: str) -> str | None:
    lowered = task.lower()
    if any(token in task for token in ("看跌", "下跌")) or any(token in lowered for token in ("bearish", " down")):
        return "down"
    if any(token in task for token in ("看涨", "上涨")) or any(token in lowered for token in ("bullish", " up")):
        return "up"
    return None


def _extract_confidence(task: str) -> float | None:
    match = re.search(r"(?:置信度?|confidence)\s*[=:：]?\s*(\d+(?:\.\d+)?)\s*%?", task, flags=re.IGNORECASE)
    if not match:
        return None
    value = float(match.group(1))
    return value / 100 if value > 1 else value


def _is_live_trade_task(task: str) -> bool:
    lowered = task.lower()
    live = any(token in task for token in ("实盘", "真实下单", "直接下单", "帮我买", "替我买", "替我卖")) or "live trade" in lowered
    return live and any(token in task for token in ("买", "卖", "下单", "交易"))


def _live_trade_refusal(symbols: list[str]) -> str:
    targets = ", ".join(_unique_symbols(symbols)) if symbols else "未指定标的"
    return "\n".join([
        "已拒绝实盘交易请求。",
        f"- 标的: {targets}",
        "- 不会连接券商、提交订单或执行真实交易。",
        "- 微信通知保持 dry-run；不会发送任何真实消息。",
        "- 如需研究，可改用纸面组合进行本地模拟。",
    ])


def _is_market_update_task(task: str) -> bool:
    return any(token in task for token in ("今天", "今日", "当天", "现在", "最新", "情况", "怎么了", "股价", "行情"))


def _is_history_learning_task(task: str) -> bool:
    lowered = task.lower()
    return any(token in task for token in ("历史数据中学习", "从历史中学习", "历史学习", "学习预测", "沉淀为 skill", "沉淀成 skill")) or any(
        token in lowered for token in ("learn from history", "history learning", "historical learning")
    )


def _is_portfolio_task(task: str) -> bool:
    lowered = task.lower()
    return any(token in task for token in ("100万", "一百万", "模拟组合", "纸面组合", "自己投资", "买多少", "仓位", "建仓")) or any(
        token in lowered for token in ("paper portfolio", "portfolio", "allocate", "allocation")
    )


def _is_portfolio_review_task(task: str) -> bool:
    lowered = task.lower()
    return any(token in task for token in ("为什么买", "为什么要买", "更好选择", "替换", "调仓建议", "谁拖累", "持仓诊断", "组合诊断", "组合表现")) or any(
        token in lowered for token in ("why buy", "better choice", "replacement", "replace", "review portfolio", "portfolio review")
    )


def _resolve_symbol(symbol: str) -> str:
    stripped = symbol.strip()
    if re.fullmatch(r"[A-Z]{1,6}(?:\.[A-Z]{1,4})?|\d{1,6}(?:\.[A-Z]{1,4})?", stripped):
        return normalize_symbol(stripped)
    try:
        return resolve_symbol(symbol).symbol
    except Exception as exc:
        raise ValueError(f"无法可靠解析标的 {symbol!r}，请先用 /resolve 确认 ticker。") from exc


def _looks_like_stock_task(task: str) -> bool:
    lowered = task.lower()
    return any(token in task for token in ("股价", "股票", "行情", "走势", "今天", "今日", "最新", "上市")) or any(
        token in lowered for token in ("stock", "quote", "price", "ticker")
    )


def _extract_name_query(task: str) -> str:
    cleaned = task
    for token in ("股价", "股票", "行情", "走势", "今天", "今日", "最新", "怎么样", "如何", "看看", "看一下", "查一下", "上市"):
        cleaned = cleaned.replace(token, " ")
    return " ".join(cleaned.split()) or task


def _source_coverage(provider) -> dict[str, dict]:  # noqa: ANN001
    getter = getattr(provider, "source_coverage", None)
    if not callable(getter):
        return {}
    return {
        method: getter(method)
        for method in ("get_quote", "get_history", "get_financials", "get_news")
    }


def _compact_error(exc: Exception, limit: int = 220) -> str:
    text = " ".join(redact_sensitive_text(str(exc)).split())
    text = text.replace("For more information check:", "详情:")
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _quote_field_source(quote: Quote, field_name: str) -> str:
    source = quote.field_sources.get(field_name) or quote.source or "UNKNOWN"
    as_of = quote.as_of if source == quote.source else ""
    return f"{source} (quote, {as_of or '未知时点'})"


def _verification_query(task: str, symbol: str) -> str:
    terms: list[str] = []
    if "智谱" in task:
        terms.append("智谱")
    if "spacex" in task.lower():
        terms.append("SpaceX")
    terms.append(symbol)
    if symbol.endswith(".HK"):
        code = symbol[:-3]
        terms.append(code)
        stripped = code.lstrip("0")
        if stripped and stripped != code:
            terms.append(stripped)
    terms.append("股票")
    if re.search(r"[A-Za-z]", task + symbol):
        terms.extend(["stock", "ticker", "IPO", "Nasdaq"])
    unique: list[str] = []
    for term in terms:
        if term and term not in unique:
            unique.append(term)
    return " ".join(unique)


def _extend_unique(target: list[str], values: list[str]) -> None:
    for value in values:
        if value not in target:
            target.append(value)
