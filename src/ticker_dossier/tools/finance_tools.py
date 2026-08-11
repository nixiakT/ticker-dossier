"""Finance tools for stock research."""
from __future__ import annotations

from dataclasses import replace

from ticker_dossier.runtime.tools import Tool
from ticker_dossier.research.agent import FinanceResearchAgent


_fallback_agent: FinanceResearchAgent | None = None


def _default_agent() -> FinanceResearchAgent:
    """Lazily support directly imported legacy tool objects.

    Application registries bind tools to an explicitly owned research service;
    this fallback exists only for callers that execute the module-level
    compatibility objects themselves.
    """
    global _fallback_agent
    if _fallback_agent is None:
        _fallback_agent = FinanceResearchAgent()
    return _fallback_agent


def _route_task(task: str) -> str:
    return _default_agent().route_task(task)


def _get_quote(symbol: str) -> str:
    return _default_agent().get_quote(symbol)


def _resolve_symbol(query: str, limit: int = 8) -> str:
    return _default_agent().resolve_symbol(query, limit)


def _get_price_history(symbol: str, period: str = "1y", format: str = "summary") -> str:
    return _default_agent().get_price_history(symbol, period, format)


def _get_financials(symbol: str) -> str:
    return _default_agent().get_financials(symbol)


def _get_news(symbol: str, limit: int = 5) -> str:
    return _default_agent().get_news(symbol, limit)


def _calculate_indicators(symbol: str, period: str = "1y") -> str:
    return _default_agent().calculate_indicators(symbol, period)


def _generate_report(symbol: str, period: str = "1y") -> str:
    return _default_agent().generate_report(symbol, period)


def _quality_screen(symbol: str, period: str = "1y") -> str:
    return _default_agent().quality_screen(symbol, period)


def _compare_stocks(symbols: list[str] | str, period: str = "1y") -> str:
    return _default_agent().compare_stocks(symbols, period)


def _debate_stocks(symbols: list[str] | str, period: str = "1y") -> str:
    return _default_agent().debate_stocks(symbols, period)


def _backtest_strategy(
    symbol: str,
    strategy: str = "",
    period: str = "2y",
    fast_window: int | None = None,
    slow_window: int | None = None,
    initial_cash: float = 100_000,
) -> str:
    return _default_agent().backtest_strategy(
        symbol, strategy, period, fast_window, slow_window, initial_cash
    )


def _daily_brief(symbols: list[str] | str, period: str = "3mo") -> str:
    return _default_agent().daily_brief(symbols, period)


def _build_paper_portfolio(
    symbols: list[str] | str,
    initial_cash: float = 1_000_000,
    period: str = "1y",
    max_positions: int = 5,
    name: str = "default",
) -> str:
    return _default_agent().build_paper_portfolio(
        symbols, initial_cash, period, max_positions, name
    )


def _rebalance_paper_portfolio(
    symbols: list[str] | str,
    period: str = "1y",
    max_positions: int = 5,
    name: str = "default",
) -> str:
    return _default_agent().rebalance_paper_portfolio(symbols, period, max_positions, name)


def _mark_paper_portfolio(name: str = "default") -> str:
    return _default_agent().mark_paper_portfolio(name)


def _show_paper_portfolio(name: str = "default") -> str:
    return _default_agent().show_paper_portfolio(name)


def _sell_paper_holding(
    symbol: str,
    shares: float | str = "all",
    name: str = "default",
    reason: str = "manual sell",
) -> str:
    return _default_agent().sell_paper_holding(symbol, shares, name, reason)


def _paper_trades(name: str = "default", limit: int = 30) -> str:
    return _default_agent().paper_trades(name, limit)


def _paper_daily_pnl(name: str = "default", limit: int = 30) -> str:
    return _default_agent().paper_daily_pnl(name, limit)


def _review_paper_portfolio(
    symbols: list[str] | str = "",
    period: str = "6mo",
    name: str = "default",
) -> str:
    return _default_agent().review_paper_portfolio(symbols, period, name)


def _learn_from_history(
    symbol: str,
    period: str = "2y",
    horizon_days: int = 20,
    record: bool = True,
    update_skill: bool = True,
) -> str:
    return _default_agent().learn_from_history(
        symbol, period, horizon_days, record, update_skill
    )


finance_route_task_tool = Tool(
    name="finance_route_task",
    description="离线兼容/故障兜底：根据自然语言任务生成固定金融报告。真实模型做多来源分析时应优先组合具体 finance_* 和 web_* 工具。",
    parameters={
        "type": "object",
        "properties": {"task": {"type": "string", "description": "用户的完整金融研究任务"}},
        "required": ["task"],
    },
    run=_route_task,
)

finance_get_quote_tool = Tool(
    name="finance_get_quote",
    description="获取股票实时/准实时行情；返回价格、涨跌、成交量、数据源和时间。",
    parameters={
        "type": "object",
        "properties": {"symbol": {"type": "string", "description": "股票代码，如 AAPL、NVDA、600519 或 贵州茅台"}},
        "required": ["symbol"],
    },
    run=_get_quote,
)

finance_resolve_symbol_tool = Tool(
    name="finance_resolve_symbol",
    description="把公司名、简称、中文名、英文名或 ticker 解析为可交易股票代码，覆盖 A 股、港股和美股候选。",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "公司名、简称、中文名、英文名或 ticker"},
            "limit": {"type": "integer", "description": "候选数量，默认 8"},
        },
        "required": ["query"],
    },
    run=_resolve_symbol,
)

finance_get_price_history_tool = Tool(
    name="finance_get_price_history",
    description="获取股票历史价格并返回摘要或 CSV。",
    parameters={
        "type": "object",
        "properties": {
            "symbol": {"type": "string"},
            "period": {"type": "string", "description": "时间范围，如 3mo、1y、2y"},
            "format": {"type": "string", "description": "summary 或 csv"},
        },
        "required": ["symbol"],
    },
    run=_get_price_history,
)

finance_get_financials_tool = Tool(
    name="finance_get_financials",
    description="获取股票基本面和财务来源摘要；估值字段缺失时需结合 finance_get_quote。",
    parameters={"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]},
    run=_get_financials,
)

finance_get_news_tool = Tool(
    name="finance_get_news",
    description="获取股票相关新闻摘要和链接。",
    parameters={
        "type": "object",
        "properties": {"symbol": {"type": "string"}, "limit": {"type": "integer", "minimum": 0}},
        "required": ["symbol"],
    },
    run=_get_news,
)

finance_calculate_indicators_tool = Tool(
    name="finance_calculate_indicators",
    description="计算 MA5/MA20/MA60、RSI、MACD、波动率和收益率等技术指标。",
    parameters={
        "type": "object",
        "properties": {"symbol": {"type": "string"}, "period": {"type": "string"}},
        "required": ["symbol"],
    },
    run=_calculate_indicators,
)

finance_generate_report_tool = Tool(
    name="finance_generate_report",
    description="生成结构化股票研究报告，包含价格、基本面、技术面、新闻、风险和结论。",
    parameters={
        "type": "object",
        "properties": {"symbol": {"type": "string"}, "period": {"type": "string"}},
        "required": ["symbol"],
    },
    run=_generate_report,
)

finance_quality_screen_tool = Tool(
    name="finance_quality_screen",
    description="对股票做研究质量门禁和去劣初筛，输出信息丰富度、数据缺口、快速否决/重审信号和下一步核验。",
    parameters={
        "type": "object",
        "properties": {"symbol": {"type": "string"}, "period": {"type": "string"}},
        "required": ["symbol"],
    },
    run=_quality_screen,
)

finance_compare_stocks_tool = Tool(
    name="finance_compare_stocks",
    description="比较多只股票的估值、基本面、技术指标和风险。",
    parameters={
        "type": "object",
        "properties": {
            "symbols": {
                "oneOf": [
                    {"type": "array", "items": {"type": "string"}},
                    {"type": "string"},
                ]
            },
            "period": {"type": "string"},
        },
        "required": ["symbols"],
    },
    run=_compare_stocks,
)

finance_debate_stocks_tool = Tool(
    name="finance_debate_stocks",
    description="让多头、空头、价值、宏观、风险和裁判 Agent 对股票池进行辩论选股。",
    parameters={
        "type": "object",
        "properties": {
            "symbols": {
                "oneOf": [
                    {"type": "array", "items": {"type": "string"}},
                    {"type": "string"},
                ]
            },
            "period": {"type": "string"},
        },
        "required": ["symbols"],
    },
    run=_debate_stocks,
)

finance_backtest_strategy_tool = Tool(
    name="finance_backtest_strategy",
    description="回测移动均线类策略，支持从自然语言策略描述中提取窗口参数。",
    parameters={
        "type": "object",
        "properties": {
            "symbol": {"type": "string"},
            "strategy": {"type": "string"},
            "period": {"type": "string"},
            "fast_window": {"type": "integer"},
            "slow_window": {"type": "integer"},
            "initial_cash": {"type": "number"},
        },
        "required": ["symbol"],
    },
    run=_backtest_strategy,
)

finance_daily_brief_tool = Tool(
    name="finance_daily_brief",
    description="为自选股生成每日简报。",
    parameters={
        "type": "object",
        "properties": {
            "symbols": {
                "oneOf": [
                    {"type": "array", "items": {"type": "string"}},
                    {"type": "string"},
                ]
            },
            "period": {"type": "string"},
        },
        "required": ["symbols"],
    },
    run=_daily_brief,
)

finance_build_paper_portfolio_tool = Tool(
    name="finance_build_paper_portfolio",
    description="用候选股票池和给定资金构建纸面模拟投资组合，输出买入数量、仓位、评分和风险提示；不会真实下单。",
    parameters={
        "type": "object",
        "properties": {
            "symbols": {
                "oneOf": [
                    {"type": "array", "items": {"type": "string"}},
                    {"type": "string"},
                ]
            },
            "initial_cash": {"type": "number", "description": "初始资金，默认 1000000"},
            "period": {"type": "string"},
            "max_positions": {"type": "integer"},
            "name": {"type": "string", "description": "本地模拟账户名称"},
        },
        "required": ["symbols"],
    },
    run=_build_paper_portfolio,
)

finance_rebalance_paper_portfolio_tool = Tool(
    name="finance_rebalance_paper_portfolio",
    description="根据当前候选股票池重新计算纸面组合仓位，并覆盖本地模拟账户持仓；不会真实下单。",
    parameters={
        "type": "object",
        "properties": {
            "symbols": {
                "oneOf": [
                    {"type": "array", "items": {"type": "string"}},
                    {"type": "string"},
                ]
            },
            "period": {"type": "string"},
            "max_positions": {"type": "integer"},
            "name": {"type": "string"},
        },
        "required": ["symbols"],
    },
    run=_rebalance_paper_portfolio,
)

finance_mark_paper_portfolio_tool = Tool(
    name="finance_mark_paper_portfolio",
    description="按最新行情给本地纸面组合估值，并追加一条每日净值记录。",
    parameters={"type": "object", "properties": {"name": {"type": "string"}}},
    run=_mark_paper_portfolio,
)

finance_show_paper_portfolio_tool = Tool(
    name="finance_show_paper_portfolio",
    description="查看本地纸面组合账户、持仓、收益和最近记录。",
    parameters={"type": "object", "properties": {"name": {"type": "string"}}},
    run=_show_paper_portfolio,
)

finance_sell_paper_holding_tool = Tool(
    name="finance_sell_paper_holding",
    description="在纸面组合中模拟卖出某只股票，记录 SELL 流水、卖出价格、数量、实现盈亏和理由；不会真实下单。",
    parameters={
        "type": "object",
        "properties": {
            "symbol": {"type": "string"},
            "shares": {
                "oneOf": [{"type": "number"}, {"type": "string"}],
                "description": "卖出股数，或 all",
            },
            "name": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": ["symbol"],
    },
    run=_sell_paper_holding,
)

finance_paper_trades_tool = Tool(
    name="finance_paper_trades",
    description="查看纸面组合交易流水，包含 BUY/SELL、数量、价格、金额和实现盈亏。",
    parameters={
        "type": "object",
        "properties": {"name": {"type": "string"}, "limit": {"type": "integer"}},
    },
    run=_paper_trades,
)

finance_paper_daily_pnl_tool = Tool(
    name="finance_paper_daily_pnl",
    description="查看纸面组合按日汇总的买入额、卖出额、已实现盈亏、期末净值和净值变化。",
    parameters={
        "type": "object",
        "properties": {"name": {"type": "string"}, "limit": {"type": "integer"}},
    },
    run=_paper_daily_pnl,
)

finance_review_paper_portfolio_tool = Tool(
    name="finance_review_paper_portfolio",
    description="只读诊断纸面组合：解释当前持仓质量、相对强弱、数据置信度，并给出替换候选；不会改仓。",
    parameters={
        "type": "object",
        "properties": {
            "symbols": {
                "oneOf": [
                    {"type": "array", "items": {"type": "string"}},
                    {"type": "string"},
                ],
                "description": "可选候选池，如 AAPL MSFT NVDA GOOGL AVGO",
            },
            "period": {"type": "string"},
            "name": {"type": "string"},
        },
    },
    run=_review_paper_portfolio,
)

finance_learn_from_history_tool = Tool(
    name="finance_learn_from_history",
    description="从股票历史价格中做 walk-forward 可解释学习，生成方向、置信度、保存预测账本，并更新 finance-history-learning Skill。",
    parameters={
        "type": "object",
        "properties": {
            "symbol": {"type": "string"},
            "period": {"type": "string", "description": "学习历史区间，如 2y、5y"},
            "horizon_days": {"type": "integer", "description": "预测未来多少天，默认 20"},
            "record": {"type": "boolean", "description": "是否写入预测账本"},
            "update_skill": {"type": "boolean", "description": "是否更新 skills/finance-history-learning/SKILL.md"},
        },
        "required": ["symbol"],
    },
    run=_learn_from_history,
)


finance_tools = [
    finance_route_task_tool,
    finance_resolve_symbol_tool,
    finance_get_quote_tool,
    finance_get_price_history_tool,
    finance_get_financials_tool,
    finance_get_news_tool,
    finance_calculate_indicators_tool,
    finance_generate_report_tool,
    finance_quality_screen_tool,
    finance_compare_stocks_tool,
    finance_debate_stocks_tool,
    finance_backtest_strategy_tool,
    finance_daily_brief_tool,
    finance_build_paper_portfolio_tool,
    finance_rebalance_paper_portfolio_tool,
    finance_mark_paper_portfolio_tool,
    finance_show_paper_portfolio_tool,
    finance_sell_paper_holding_tool,
    finance_paper_trades_tool,
    finance_paper_daily_pnl_tool,
    finance_review_paper_portfolio_tool,
    finance_learn_from_history_tool,
]


_FINANCE_METHOD_BY_TOOL = {
    "finance_route_task": "route_task",
    "finance_resolve_symbol": "resolve_symbol",
    "finance_get_quote": "get_quote",
    "finance_get_price_history": "get_price_history",
    "finance_get_financials": "get_financials",
    "finance_get_news": "get_news",
    "finance_calculate_indicators": "calculate_indicators",
    "finance_generate_report": "generate_report",
    "finance_quality_screen": "quality_screen",
    "finance_compare_stocks": "compare_stocks",
    "finance_debate_stocks": "debate_stocks",
    "finance_backtest_strategy": "backtest_strategy",
    "finance_daily_brief": "daily_brief",
    "finance_build_paper_portfolio": "build_paper_portfolio",
    "finance_rebalance_paper_portfolio": "rebalance_paper_portfolio",
    "finance_mark_paper_portfolio": "mark_paper_portfolio",
    "finance_show_paper_portfolio": "show_paper_portfolio",
    "finance_sell_paper_holding": "sell_paper_holding",
    "finance_paper_trades": "paper_trades",
    "finance_paper_daily_pnl": "paper_daily_pnl",
    "finance_review_paper_portfolio": "review_paper_portfolio",
    "finance_learn_from_history": "learn_from_history",
}


def build_finance_tools(agent: FinanceResearchAgent) -> list[Tool]:
    """Bind finance tool templates to one application-owned research service."""
    return [
        replace(template, run=getattr(agent, _FINANCE_METHOD_BY_TOOL[template.name]))
        for template in finance_tools
    ]
