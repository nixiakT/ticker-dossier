"""Report rendering utilities."""
from __future__ import annotations

from typing import Any

from .frameworks import evaluate_frameworks, format_framework_results
from .indicators import format_indicators
from .models import Financials, NewsItem, Quote, StockSnapshot
from .quality import render_quality_gate


def render_stock_report(snapshot: StockSnapshot) -> str:
    quote = snapshot.quote
    financials = snapshot.financials
    frameworks = [] if _low_confidence_financials(financials) else evaluate_frameworks(quote, financials, snapshot.indicators)
    conclusion = _conclusion(quote, financials, snapshot.indicators)

    sections = [
        f"# 股票研究报告：{snapshot.symbol}",
        "",
        "## 数据说明",
        f"- 抓取时间: {snapshot.fetched_at}",
        f"- 行情来源: {quote.source or '未知'}",
        f"- 行情时间: {quote.as_of or '未知'}",
        f"- 是否实时/准实时: {'是' if quote.is_realtime else '否'}",
    ]
    for note in _unique_notes(quote.notes + financials.notes):
        sections.append(f"- 数据备注: {note}")

    sections.extend([
        "",
        "## 当前价格",
        _format_quote(quote),
        "",
        "## 近期走势与技术面",
        format_indicators(snapshot.indicators),
        "",
        "## 基本面",
        render_financials(financials),
        "",
        "## 新闻事件",
        _format_news(snapshot.news),
        "",
        "## 研究质量门禁",
        render_quality_gate(snapshot),
        "",
        "## 投资大师框架蒸馏",
        _format_framework_section(frameworks, financials),
        "",
        "## 主要风险",
        _risk_summary(quote, financials, snapshot.indicators, snapshot.news),
        "",
        "## 结论",
        conclusion,
        "",
        "免责声明：以上内容仅用于研究和学习，不构成投资建议，也不承诺任何收益。",
    ])
    return "\n".join(sections)


def render_comparison(snapshots: list[StockSnapshot]) -> str:
    lines = ["# 股票对比研究", ""]
    lines.append("| 标的 | 价格 | 涨跌幅 | PE | EPS | 市值 | ROE | 1年收益 | 年化波动 | 数据源 |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for snapshot in snapshots:
        q = snapshot.quote
        f = snapshot.financials
        i = snapshot.indicators
        lines.append(
        "| {symbol} | {price} | {change} | {pe} | {eps} | {cap} | {roe} | {ret} | {vol} | {source} |".format(
                symbol=snapshot.symbol,
                price=_fmt_number(q.price),
                change=_fmt_percent(q.change_percent),
                pe=_fmt_number(f.pe_ratio or q.pe_ratio),
                eps=_fmt_number(f.eps or q.eps),
                cap=_fmt_big(f.market_cap or q.market_cap),
                roe=_fmt_percent(_ratio_to_pct(f.return_on_equity)),
                ret=_fmt_percent(i.get("return_1y_pct")),
                vol=_fmt_percent(i.get("annualized_volatility_pct")),
                source=f"{q.source}/{f.source}",
            )
        )
    lines.append("")
    lines.append("## 快速判断")
    for snapshot in snapshots:
        lines.append(f"- {snapshot.symbol}: {_conclusion(snapshot.quote, snapshot.financials, snapshot.indicators)}")
    lines.append("")
    lines.append("免责声明：对比结果仅用于研究，不构成投资建议。")
    return "\n".join(lines)


def render_daily_brief(snapshots: list[StockSnapshot]) -> str:
    lines = ["# 自选股每日简报", ""]
    for snapshot in snapshots:
        q = snapshot.quote
        i = snapshot.indicators
        lines.append(f"## {snapshot.symbol}")
        lines.append(f"- 价格: {_fmt_number(q.price)} {q.currency}，涨跌幅: {_fmt_percent(q.change_percent)}")
        lines.append(f"- 趋势: {i.get('trend_summary', '数据不足')}")
        if snapshot.news:
            lines.append(f"- 新闻: {snapshot.news[0].title}")
        lines.append(f"- 数据源: {q.source}，时间: {q.as_of}")
        lines.append("")
    lines.append("免责声明：简报只用于跟踪信息，不构成投资建议。")
    return "\n".join(lines)


def _format_quote(quote: Quote) -> str:
    return "\n".join([
        f"- 标的: {quote.symbol} {quote.name}".strip(),
        f"- 当前价格: {_fmt_number(quote.price)} {quote.currency}",
        f"- 前收盘: {_fmt_number(quote.previous_close)}",
        f"- 涨跌: {_fmt_number(quote.change)} ({_fmt_percent(quote.change_percent)})",
        f"- 成交量: {_fmt_big(quote.volume)}",
        f"- 市值: {_fmt_big(quote.market_cap)}",
        f"- PE: {_fmt_number(quote.pe_ratio)}",
        f"- EPS: {_fmt_number(quote.eps)}",
    ])


def render_financials(financials: Financials) -> str:
    fields = [
        ("市值", financials.market_cap, "big"),
        ("PE", financials.pe_ratio, "number"),
        ("Forward PE", financials.forward_pe, "number"),
        ("EPS", financials.eps, "number"),
        ("营收", financials.revenue, "big"),
        ("毛利", financials.gross_profit, "big"),
        ("净利润", financials.net_income, "big"),
        ("自由现金流", financials.free_cash_flow, "big"),
        ("债务/权益比（%）", financials.debt_to_equity, "leverage"),
        ("ROE", _ratio_to_pct(financials.return_on_equity), "percent"),
        ("利润率", _ratio_to_pct(financials.profit_margin), "percent"),
    ]
    lines = [
        f"- 数据来源: {financials.source or '未知'}",
        f"- 报告期: {financials.as_of or '未知'}",
        f"- 口径: {financials.period_type or '未知'}",
        f"- 财务币种: {financials.currency or '未知'}",
        f"- 抓取时间: {financials.fetched_at or '未知'}",
    ]
    for label, value, kind in fields:
        if value is None:
            lines.append(f"- {label}: 缺失")
        elif kind == "big":
            lines.append(f"- {label}: {_fmt_big(value)}")
        elif kind == "percent":
            lines.append(f"- {label}: {_fmt_percent(value)}")
        elif kind == "leverage" and float(value) >= 1_000_000:
            lines.append(f"- {label}: 极高（负权益或负债不低于资产）")
        else:
            lines.append(f"- {label}: {_fmt_number(value)}")
    if financials.field_sources:
        provenance = "、".join(
            f"{_financial_field_label(name)}={source}"
            for name, source in sorted(financials.field_sources.items())
        )
        lines.append(f"- 字段来源: {provenance}")
    return "\n".join(lines)


def _financial_field_label(name: str) -> str:
    return {
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
    }.get(name, name)


def _format_news(news: list[NewsItem]) -> str:
    if not news:
        return "- 暂无可用新闻；需要补充新闻源或公告源。"
    lines = []
    for item in news:
        suffix = f" ({item.publisher}, {item.published_at})" if item.publisher or item.published_at else ""
        lines.append(f"- {item.title}{suffix}")
        if item.summary:
            lines.append(f"  摘要: {item.summary[:220]}")
        if item.link:
            lines.append(f"  链接: {item.link}")
    return "\n".join(lines)


def _format_framework_section(results: list[Any], financials: Financials) -> str:
    if _low_confidence_financials(financials):
        return "\n".join([
            "- 基本面数据来自样例 fallback 或不可用，暂不对护城河、现金流、安全边际等框架打分。",
            "- 需要先补齐真实财报、公告、审计口径和管理层信息，再做巴菲特/段永平/达利欧框架评估。",
        ])
    return format_framework_results(results)


def _low_confidence_financials(financials: Financials) -> bool:
    return financials.source in {"SAMPLE_FALLBACK", "UNAVAILABLE", ""}


def _risk_summary(quote: Quote, financials: Financials, indicators: dict[str, Any], news: list[NewsItem]) -> str:
    risks = []
    pe = financials.pe_ratio or quote.pe_ratio
    if pe is not None and pe > 40:
        risks.append("估值倍数较高，对增长预期和利率变化敏感。")
    volatility = indicators.get("annualized_volatility_pct")
    if volatility is not None and volatility > 45:
        risks.append("年化波动率偏高，仓位管理和回撤承受能力很关键。")
    if financials.free_cash_flow is not None and financials.free_cash_flow < 0:
        risks.append("自由现金流为负，需要确认增长质量和融资依赖。")
    if financials.debt_to_equity is not None and financials.debt_to_equity > 150:
        risks.append("杠杆水平较高，利率和信用环境变化可能放大风险。")
    if not news:
        risks.append("新闻和公告数据不足，事件驱动风险可能被遗漏。")
    if quote.source == "SAMPLE_FALLBACK" or financials.source == "SAMPLE_FALLBACK":
        risks.append("当前存在样例 fallback 数据，不能用于真实投资判断。")
    if quote.source == "UNAVAILABLE" or financials.source == "UNAVAILABLE":
        risks.append("部分核心数据源不可用，当前结论只能作为信息整理，不能用于投资判断。")
    if not risks:
        risks.append("当前未触发明显量化风险，但仍需补充行业、竞争、管理层和公告验证。")
    return "\n".join(f"- {risk}" for risk in risks)


def _conclusion(quote: Quote, financials: Financials, indicators: dict[str, Any]) -> str:
    if quote.source == "SAMPLE_FALLBACK" or financials.source == "SAMPLE_FALLBACK":
        return "研究结论：数据置信度不足，仅适合演示流程；请接入真实行情、财报和公告后再做判断。"
    if quote.source == "UNAVAILABLE" or financials.source == "UNAVAILABLE":
        return "研究结论：数据不完整；请先补齐真实行情、财报、公告或网页核验来源后再判断。"

    score = 0
    if financials.free_cash_flow is not None and financials.free_cash_flow > 0:
        score += 1
    if financials.profit_margin is not None and financials.profit_margin > 0.15:
        score += 1
    if financials.pe_ratio is not None and financials.pe_ratio < 40:
        score += 1
    if indicators.get("return_3m_pct") is not None and indicators["return_3m_pct"] > 0:
        score += 1
    if indicators.get("annualized_volatility_pct") is not None and indicators["annualized_volatility_pct"] < 45:
        score += 1

    if score >= 4:
        return "研究结论：值得继续跟踪；质量、估值或趋势中有多项正面信号，但仍需验证核心假设。"
    if score >= 2:
        return "研究结论：中性观察；有可取之处，也存在需要补充验证的数据或风险。"
    return "研究结论：暂不具备高置信度吸引力；建议先补充数据或等待更明确的安全边际。"


def _fmt_number(value: Any) -> str:
    if value is None:
        return "缺失"
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def _fmt_percent(value: Any) -> str:
    if value is None:
        return "缺失"
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return str(value)


def _fmt_big(value: Any) -> str:
    if value is None:
        return "缺失"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    abs_number = abs(number)
    if abs_number >= 1_000_000_000_000:
        return f"{number / 1_000_000_000_000:.2f}T"
    if abs_number >= 1_000_000_000:
        return f"{number / 1_000_000_000:.2f}B"
    if abs_number >= 1_000_000:
        return f"{number / 1_000_000:.2f}M"
    return f"{number:,.0f}"


def _ratio_to_pct(value: Any) -> float | None:
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if abs(value) <= 2:
        return value * 100
    return value


def _unique_notes(notes: list[str]) -> list[str]:
    unique: list[str] = []
    for note in notes:
        if note not in unique:
            unique.append(note)
    return unique
