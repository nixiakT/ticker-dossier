"""Stock research and report command handlers."""
from __future__ import annotations

from pathlib import Path

from ticker_dossier.cli.command_types import HandlerResult

from ._shared import period_arg, require_arg, require_many


RESEARCH_HANDLER_METHODS = {
    "research.quote": "handle_quote",
    "research.resolve": "handle_resolve",
    "research.history": "handle_history",
    "research.financials": "handle_financials",
    "research.news": "handle_news",
    "research.indicators": "handle_indicators",
    "research.report": "handle_report",
    "research.export_report": "handle_export_report",
    "research.quality": "handle_quality",
    "research.compare": "handle_compare",
    "research.debate": "handle_debate",
    "research.backtest": "handle_backtest",
    "research.brief": "handle_brief",
}


class ResearchCommandHandlers:
    def handle_quote(self, args: list[str], _think_enabled: str | bool) -> HandlerResult:
        symbol = require_arg(args, "/quote AAPL")
        self._trace_tool("finance_get_quote", {"symbol": symbol})
        return self._with_result_trace("finance_get_quote", self.finance.get_quote(symbol))

    def handle_resolve(self, args: list[str], _think_enabled: str | bool) -> HandlerResult:
        query = " ".join(args).strip()
        if not query:
            raise ValueError("用法：/resolve minimax")
        self._trace_tool("finance_resolve_symbol", {"query": query})
        return self._with_result_trace("finance_resolve_symbol", self.finance.resolve_symbol(query))

    def handle_history(self, args: list[str], _think_enabled: str | bool) -> HandlerResult:
        symbol = require_arg(args, "/history AAPL [period]")
        period = args[1] if len(args) > 1 else "1y"
        self._trace_tool("finance_get_price_history", {"symbol": symbol, "period": period})
        return self._with_result_trace(
            "finance_get_price_history",
            self.finance.get_price_history(symbol, period),
        )

    def handle_financials(self, args: list[str], _think_enabled: str | bool) -> HandlerResult:
        symbol = require_arg(args, "/financials AAPL")
        self._trace_tool("finance_get_financials", {"symbol": symbol})
        return self._with_result_trace("finance_get_financials", self.finance.get_financials(symbol))

    def handle_news(self, args: list[str], _think_enabled: str | bool) -> HandlerResult:
        symbol = require_arg(args, "/news AAPL [limit]")
        if len(args) > 1:
            try:
                limit = int(args[1])
            except ValueError as exc:
                raise ValueError("用法：/news AAPL [非负整数 limit]") from exc
            if limit < 0:
                raise ValueError("用法：/news AAPL [非负整数 limit]")
        else:
            limit = 5
        self._trace_tool("finance_get_news", {"symbol": symbol, "limit": limit})
        return self._with_result_trace("finance_get_news", self.finance.get_news(symbol, limit))

    def handle_indicators(self, args: list[str], _think_enabled: str | bool) -> HandlerResult:
        symbol = require_arg(args, "/indicators AAPL [period]")
        period = args[1] if len(args) > 1 else "1y"
        self._trace_tool("finance_calculate_indicators", {"symbol": symbol, "period": period})
        return self._with_result_trace(
            "finance_calculate_indicators",
            self.finance.calculate_indicators(symbol, period),
        )

    def handle_report(self, args: list[str], _think_enabled: str | bool) -> HandlerResult:
        symbol = require_arg(args, "/report AAPL [period]")
        period = args[1] if len(args) > 1 else "1y"
        self._trace_tool("finance_generate_report", {"symbol": symbol, "period": period})
        return self._with_result_trace(
            "finance_generate_report",
            self.finance.generate_report(symbol, period),
        )

    def handle_export_report(self, args: list[str], _think_enabled: str | bool) -> HandlerResult:
        symbol = require_arg(args, "/export-report AAPL [period] [reports/aapl.md]")
        period = args[1] if len(args) > 1 else "1y"
        output_path = args[2] if len(args) > 2 else f"reports/{symbol.lower()}-{period}.md"
        self._trace_tool("finance_generate_report", {"symbol": symbol, "period": period})
        report = self.finance.generate_report(symbol, period)
        resolved = self.write_guard(output_path, report)
        resolved.write_text(report, encoding="utf-8")
        try:
            display_path = str(resolved.relative_to(Path.cwd()))
        except ValueError:
            display_path = str(resolved)
        return self._with_result_trace(
            "finance_generate_report",
            f"研究报告已保存到: {display_path}",
        )

    def handle_quality(self, args: list[str], _think_enabled: str | bool) -> HandlerResult:
        symbol = require_arg(args, "/quality AAPL [period]")
        period = args[1] if len(args) > 1 else "1y"
        self._trace_tool("finance_quality_screen", {"symbol": symbol, "period": period})
        return self._with_result_trace(
            "finance_quality_screen",
            self.finance.quality_screen(symbol, period),
        )

    def handle_compare(self, args: list[str], _think_enabled: str | bool) -> HandlerResult:
        symbols = require_many(args, "/compare NVDA AMD [period]")
        period = period_arg(args[-1])
        if period:
            symbols = args[:-1]
        else:
            period = "1y"
        self._trace_tool("finance_compare_stocks", {"symbols": symbols, "period": period})
        return self._with_result_trace(
            "finance_compare_stocks",
            self.finance.compare_stocks(symbols, period),
        )

    def handle_debate(self, args: list[str], _think_enabled: str | bool) -> HandlerResult:
        symbols = require_many(args, "/debate NVDA AMD [period]")
        period = period_arg(args[-1])
        if period:
            symbols = args[:-1]
        else:
            period = "1y"
        self._trace_tool("finance_debate_stocks", {"symbols": symbols, "period": period})
        return self._with_result_trace(
            "finance_debate_stocks",
            self.finance.debate_stocks(symbols, period),
        )

    def handle_backtest(self, args: list[str], _think_enabled: str | bool) -> HandlerResult:
        symbol = require_arg(args, "/backtest TSLA 20 60 [period]")
        fast = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
        slow = int(args[2]) if len(args) > 2 and args[2].isdigit() else None
        period = args[3] if len(args) > 3 else "2y"
        strategy = f"{fast or 20} 日均线上穿 {slow or 60} 日均线策略"
        self._trace_tool("finance_backtest_strategy", {
            "symbol": symbol,
            "strategy": strategy,
            "period": period,
        })
        return self._with_result_trace(
            "finance_backtest_strategy",
            self.finance.backtest_strategy(symbol, strategy, period, fast, slow),
        )

    def handle_brief(self, args: list[str], _think_enabled: str | bool) -> HandlerResult:
        symbols = require_many(args, "/brief AAPL MSFT NVDA")
        self._trace_tool("finance_daily_brief", {"symbols": symbols})
        return self._with_result_trace("finance_daily_brief", self.finance.daily_brief(symbols))
