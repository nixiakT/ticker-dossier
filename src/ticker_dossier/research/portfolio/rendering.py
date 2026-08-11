"""Pure paper-portfolio metrics and text rendering."""
from __future__ import annotations

from typing import Any

from ticker_dossier.research.portfolio.models import (
    CandidateScore,
    Holding,
    PortfolioAccount,
    PortfolioValuation,
)
from ticker_dossier.research.portfolio.scoring import (
    _component_text,
    _holding_diagnosis,
    _is_weak_holding,
    _review_basis,
)


def portfolio_value(account: PortfolioAccount, prices: dict[str, float] | None = None) -> float:
    prices = prices or {}
    holding_value = 0.0
    for holding in account.holdings:
        price = prices.get(holding.symbol, holding.last_price)
        holding_value += holding.shares * price
    return account.cash + holding_value


def render_transactions(account: PortfolioAccount, limit: int = 30) -> str:
    if not account.transactions:
        return "交易流水：暂无。"
    lines = ["# 纸面交易流水", ""]
    lines.append("| 时间 | 动作 | 标的 | 股数 | 价格 | 金额 | 实现盈亏 | 理由 |")
    lines.append("|---|---|---|---:|---:|---:|---:|---|")
    for item in account.transactions[-max(limit, 1):]:
        lines.append(
            f"| {item.get('as_of', '')} | {item.get('action', '')} | {item.get('symbol', '')} | "
            f"{float(item.get('shares') or 0):,.0f} | {_money(item.get('price'))} | "
            f"{_money(item.get('amount'))} | {_money(item.get('realized_pnl'))} | "
            f"{str(item.get('reason') or '')[:90]} |"
        )
    return "\n".join(lines)


def render_daily_pnl(account: PortfolioAccount, limit: int = 30) -> str:
    rows = _daily_pnl_rows(account)
    if not rows:
        return "每日买卖盈亏：暂无记录。"
    lines = [
        "# 每日买卖盈亏",
        "",
        "| 日期 | 买入额 | 卖出额 | 已实现盈亏 | 期末净值 | 当日净值变化 | 交易笔数 | 事件 |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows[-max(limit, 1):]:
        lines.append(
            f"| {row['date']} | {_money(row['buy_amount'])} | {_money(row['sell_amount'])} | "
            f"{_money(row['realized_pnl'])} | {_money(row.get('ending_value'))} | "
            f"{_money(row.get('nav_change'))} | {int(row['trade_count'])} | {row.get('events') or ''} |"
        )
    lines.extend([
        "",
        "说明：买入额/卖出额来自纸面交易流水；已实现盈亏只在 SELL 时确认；期末净值来自当日最后一条账户历史记录。",
    ])
    return "\n".join(lines)


def render_portfolio_review(
    account: PortfolioAccount,
    scores: list[CandidateScore],
    *,
    valuation: PortfolioValuation | None = None,
) -> str:
    account = valuation.account if valuation else account
    total = portfolio_value(account)
    pnl = total - account.initial_cash
    total_pnl_pct = (pnl / account.initial_cash * 100) if account.initial_cash else 0.0
    score_by_symbol = {score.symbol.upper(): score for score in scores}
    held_symbols = {holding.symbol.upper() for holding in account.holdings}
    rows: list[tuple[Holding, CandidateScore | None, float]] = []
    for holding in account.holdings:
        holding_pnl_pct = (holding.last_price / holding.avg_cost - 1) * 100 if holding.avg_cost else 0.0
        rows.append((holding, score_by_symbol.get(holding.symbol.upper()), holding_pnl_pct))

    lines = [
        f"# 纸面组合诊断：{account.name}",
        "",
        f"- 当前净值: {_money(total)}",
        f"- 累计收益: {_money(pnl)} ({total_pnl_pct:.2f}%)",
        f"- 现金占比: {(account.cash / total * 100) if total else 0:.2f}%",
        f"- 估值行情时间: {valuation.as_of if valuation else (account.updated_at or '未知（账户记录价）')}",
        "- 性质: 只读诊断，不会改仓；真正调整请再执行 `/portfolio rebalance ...` 或 `/portfolio sell ...`。",
    ]
    if valuation:
        lines.append(f"- 最新行情覆盖: {len(valuation.fresh_symbols)}/{len(account.holdings)} 个持仓。")
        if valuation.stale_symbols:
            lines.append(
                "- **陈旧价格回退**: " + ", ".join(valuation.stale_symbols)
                + " 获取最新价失败，暂用账户上次记录价；净值、盈亏和权重均包含该回退。"
            )
    lines.extend(_storage_warning_lines(account))
    lines.extend([
        "",
        "## 持仓复盘",
        "| 标的 | 最新价 | 市值 | 权重 | 持仓收益 | 行情状态 | 当前评分 | 诊断 | 主要依据 |",
        "|---|---:|---:|---:|---:|---|---:|---|---|",
    ])
    for holding, score, pnl_pct in rows:
        diagnosis = _holding_diagnosis(holding, score, pnl_pct)
        score_text = f"{score.score:.1f}" if score else "NA"
        price_status = _valuation_price_status(holding.symbol, valuation, account)
        lines.append(
            f"| {holding.symbol} | {_money(holding.last_price)} | {_money(holding.market_value)} | "
            f"{holding.weight * 100:.2f}% | {pnl_pct:.2f}% | {price_status} | {score_text} | {diagnosis} | "
            f"{_review_basis(score) if score else holding.thesis[:90]} |"
        )

    ranked = sorted(scores, key=lambda item: item.score, reverse=True)
    weakest_score = min((score.score for _, score, _ in rows if score), default=0.0)
    replacements = [
        score for score in ranked
        if score.symbol.upper() not in held_symbols and score.score >= max(50.0, weakest_score + 5.0)
    ][:5]
    weak_holdings = [
        (holding, score) for holding, score, pnl_pct in rows
        if score and (_is_weak_holding(score, pnl_pct) or pnl_pct < -5)
    ]

    lines.extend(["", "## 替换候选"])
    if replacements:
        lines.append("| 候选 | 评分 | 价格 | 诊断 | 依据 | 风险 |")
        lines.append("|---|---:|---:|---|---|---|")
        for score in replacements:
            lines.append(
                f"| {score.symbol} | {score.score:.1f} | {_money(score.price)} | {score.verdict} | "
                f"{_review_basis(score)} | {'; '.join(score.warnings[:3]) or '无'} |"
            )
    else:
        lines.append("- 没有明显高于当前弱项的替换候选；先继续跟踪或扩大候选池。")

    lines.extend(["", "## 操作建议"])
    if weak_holdings and replacements:
        weak_text = ", ".join(f"{holding.symbol}({score.score:.1f})" for holding, score in weak_holdings)
        replacement_text = ", ".join(f"{score.symbol}({score.score:.1f})" for score in replacements[:3])
        lines.append(f"- 可重点比较弱项 {weak_text} 与候选 {replacement_text}。")
    elif weak_holdings:
        weak_text = ", ".join(f"{holding.symbol}({score.score:.1f})" for holding, score in weak_holdings)
        lines.append(f"- {weak_text} 属于弱持仓，但本轮没有足够强的替换候选；先降低置信度或扩大候选池。")
    else:
        lines.append("- 当前持仓没有触发明确替换信号；按计划继续每日 mark。")
    lines.append("- 若要执行纸面调仓，先用 `/portfolio review AAPL MSFT NVDA GOOGL AVGO ...` 扩大候选，再用 `/portfolio rebalance ...`。")
    return "\n".join(lines)


def render_account(account: PortfolioAccount) -> str:
    total = portfolio_value(account)
    pnl = total - account.initial_cash
    pnl_pct = (pnl / account.initial_cash * 100) if account.initial_cash else 0.0
    lines = [
        f"# 模拟投资账户：{account.name}",
        "",
        f"- 初始资金: {_money(account.initial_cash)}",
        f"- 当前净值: {_money(total)}",
        f"- 现金: {_money(account.cash)}",
        f"- 累计收益: {_money(pnl)} ({pnl_pct:.2f}%)",
        f"- 更新时间: {account.updated_at or '未知'}",
    ]
    lines.extend(_storage_warning_lines(account))
    lines.extend(["", "## 持仓"])
    if not account.holdings:
        lines.append("- 暂无持仓。")
    else:
        lines.append("| 标的 | 股数 | 成本 | 最新价 | 市值 | 权重 | 理由 |")
        lines.append("|---|---:|---:|---:|---:|---:|---|")
        for holding in account.holdings:
            lines.append(
                f"| {holding.symbol} | {holding.shares:,.0f} | {_money(holding.avg_cost)} | "
                f"{_money(holding.last_price)} | {_money(holding.market_value)} | {holding.weight * 100:.2f}% | "
                f"{holding.thesis[:80]} |"
            )
    lines.extend([
        "",
        "## 交易统计",
        f"- 交易笔数: {len(account.transactions)}",
        f"- 已实现盈亏: {_money(_realized_pnl(account))}",
        "",
        "## 最近记录",
    ])
    for row in account.history[-5:]:
        lines.append(
            f"- {row.get('as_of')} {row.get('event')}: "
            f"净值 {_money(row.get('total_value'))}, 收益 {float(row.get('return_pct') or 0):.2f}%"
        )
    lines.append("")
    lines.append("说明：这是纸面组合，用于验证研究和选股框架，不会执行真实交易。")
    return "\n".join(lines)


def render_recommendation(account: PortfolioAccount, scores: list[CandidateScore]) -> str:
    lines = [render_account(account), "", "## 候选评分"]
    lines.append("| 排名 | 标的 | 分数 | 目标权重 | 价格 | 诊断 | 分项 | 关键理由 | 风险提示 |")
    lines.append("|---:|---|---:|---:|---:|---|---|---|---|")
    for index, score in enumerate(scores, start=1):
        lines.append(
            f"| {index} | {score.symbol} | {score.score:.1f} | {score.target_weight * 100:.1f}% | "
            f"{_money(score.price)} | {score.verdict or '未分级'} | {_component_text(score)} | {score.thesis} | "
            f"{'; '.join(score.warnings) or '无'} |"
        )
    lines.extend([
        "",
        "## 风控规则",
        "- 单只股票目标权重受上限约束，默认不超过 30%。",
        "- 默认保留现金，避免满仓和数据误差导致的过度集中。",
        "- SAMPLE_FALLBACK 或 UNAVAILABLE 数据会显著降权；真实投资前必须核验数据源。",
        "- 输出是模拟组合和研究实验，不构成投资建议，也不连接真实交易账户。",
    ])
    return "\n".join(lines)


def _storage_warning_lines(account: PortfolioAccount) -> list[str]:
    return [f"- **账户位置警告**: {warning}" for warning in account.storage_warnings]


def _valuation_price_status(
    symbol: str,
    valuation: PortfolioValuation | None,
    account: PortfolioAccount,
) -> str:
    if valuation is None:
        return f"账户记录 ({account.updated_at or '未知'})"
    normalized = symbol.upper()
    as_of = valuation.price_as_of.get(normalized, "未知")
    source = valuation.price_sources.get(normalized, "未知来源").replace("|", "/")
    if normalized in valuation.stale_symbols:
        return f"**缓存回退** ({as_of})"
    return f"{source} ({as_of})"


def _realized_pnl(account: PortfolioAccount) -> float:
    return sum(float(item.get("realized_pnl") or 0) for item in account.transactions)


def _daily_pnl_rows(account: PortfolioAccount) -> list[dict[str, Any]]:
    days: dict[str, dict[str, Any]] = {}
    for item in account.transactions:
        day = _date_key(item.get("as_of"))
        if not day:
            continue
        row = days.setdefault(day, _empty_daily_row(day))
        action = str(item.get("action") or "").upper()
        amount = float(item.get("amount") or 0)
        if action == "BUY":
            row["buy_amount"] += amount
        elif action == "SELL":
            row["sell_amount"] += amount
            row["realized_pnl"] += float(item.get("realized_pnl") or 0)
        row["trade_count"] += 1
        symbols = row.setdefault("_symbols", set())
        if item.get("symbol"):
            symbols.add(str(item.get("symbol")))

    history_by_day: dict[str, list[dict[str, Any]]] = {}
    for item in account.history:
        day = _date_key(item.get("as_of"))
        if not day:
            continue
        row = days.setdefault(day, _empty_daily_row(day))
        event = str(item.get("event") or "")
        if event:
            events = row.setdefault("_events", set())
            events.add(event)
        history_by_day.setdefault(day, []).append(item)

    previous_value: float | None = None
    rows: list[dict[str, Any]] = []
    for day in sorted(days):
        row = days[day]
        history = history_by_day.get(day) or []
        if history:
            latest = history[-1]
            ending = float(latest.get("total_value") or 0)
            row["ending_value"] = ending
            row["nav_change"] = 0.0 if previous_value is None else ending - previous_value
            previous_value = ending
            # Imported/recovered ledgers may know audited daily totals without
            # retaining every original fill. Prefer those explicit totals over
            # inventing a per-trade allocation.
            for source_key, target_key in (
                ("reported_buy_amount", "buy_amount"),
                ("reported_sell_amount", "sell_amount"),
                ("reported_realized_pnl", "realized_pnl"),
                ("reported_trade_count", "trade_count"),
                ("reported_nav_change", "nav_change"),
            ):
                if latest.get(source_key) is not None:
                    row[target_key] = float(latest[source_key])
        else:
            row["ending_value"] = None
            row["nav_change"] = None
        row["events"] = ",".join(sorted(row.pop("_events", set())))
        row["symbols"] = ",".join(sorted(row.pop("_symbols", set())))
        rows.append(row)
    return rows


def _empty_daily_row(day: str) -> dict[str, Any]:
    return {
        "date": day,
        "buy_amount": 0.0,
        "sell_amount": 0.0,
        "realized_pnl": 0.0,
        "trade_count": 0,
        "ending_value": None,
        "nav_change": None,
    }


def _date_key(value: Any) -> str:
    text = str(value or "")
    return text[:10] if len(text) >= 10 else ""


def _money(value: Any) -> str:
    if value is None:
        return "NA"
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)
