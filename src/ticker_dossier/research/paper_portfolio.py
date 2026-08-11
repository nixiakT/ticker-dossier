"""Paper portfolio account for measurable stock-selection experiments."""
from __future__ import annotations

import json
import math
import os
import shutil
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from .models import Quote, StockSnapshot


LEGACY_PORTFOLIO_DIR = Path.cwd() / ".finance_agent"
DEFAULT_PORTFOLIO_DIR = Path(
    os.getenv("FINANCE_PORTFOLIO_DIR", Path.home() / ".finance-agent" / "portfolios")
).expanduser()
PORTFOLIO_DIR = DEFAULT_PORTFOLIO_DIR
DEFAULT_ACCOUNT = "default"


@dataclass
class Holding:
    symbol: str
    shares: float
    avg_cost: float
    last_price: float
    market_value: float
    weight: float
    thesis: str = ""


@dataclass
class PortfolioAccount:
    name: str
    initial_cash: float
    cash: float
    holdings: list[Holding] = field(default_factory=list)
    transactions: list[dict[str, Any]] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""


@dataclass
class CandidateScore:
    symbol: str
    score: float
    target_weight: float
    price: float | None
    source: str
    thesis: str
    warnings: list[str] = field(default_factory=list)
    components: dict[str, float] = field(default_factory=dict)
    verdict: str = ""


def account_path(name: str = DEFAULT_ACCOUNT, base_dir: Path | None = None) -> Path:
    clean = "".join(ch for ch in (name or DEFAULT_ACCOUNT) if ch.isalnum() or ch in {"-", "_"}) or DEFAULT_ACCOUNT
    path = (base_dir or PORTFOLIO_DIR) / f"portfolio_{clean}.json"
    if base_dir is None and PORTFOLIO_DIR == DEFAULT_PORTFOLIO_DIR and not path.exists():
        legacy_path = LEGACY_PORTFOLIO_DIR / path.name
        if legacy_path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(legacy_path, path)
    return path


def create_account(
    *,
    initial_cash: float = 1_000_000.0,
    name: str = DEFAULT_ACCOUNT,
    overwrite: bool = False,
    base_dir: Path | None = None,
) -> PortfolioAccount:
    path = account_path(name, base_dir)
    if path.exists() and not overwrite:
        return load_account(name, base_dir)
    if path.exists() and overwrite:
        _backup_account(path)
    now = _iso_now()
    account = PortfolioAccount(
        name=name,
        initial_cash=max(float(initial_cash), 0.0),
        cash=max(float(initial_cash), 0.0),
        created_at=now,
        updated_at=now,
    )
    save_account(account, base_dir)
    return account


def load_account(name: str = DEFAULT_ACCOUNT, base_dir: Path | None = None) -> PortfolioAccount:
    path = account_path(name, base_dir)
    if not path.exists():
        return create_account(name=name, base_dir=base_dir)
    data = json.loads(path.read_text(encoding="utf-8"))
    holdings = [Holding(**row) for row in data.get("holdings", [])]
    return PortfolioAccount(
        name=str(data.get("name") or name),
        initial_cash=float(data.get("initial_cash") or 0),
        cash=float(data.get("cash") or 0),
        holdings=holdings,
        transactions=list(data.get("transactions", [])),
        history=list(data.get("history", [])),
        created_at=str(data.get("created_at") or ""),
        updated_at=str(data.get("updated_at") or ""),
    )


def save_account(account: PortfolioAccount, base_dir: Path | None = None) -> Path:
    path = account_path(account.name, base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "name": account.name,
        "initial_cash": account.initial_cash,
        "cash": account.cash,
        "holdings": [holding.__dict__ for holding in account.holdings],
        "transactions": account.transactions[-1000:],
        "history": account.history[-500:],
        "created_at": account.created_at,
        "updated_at": account.updated_at,
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return path


def _backup_account(path: Path) -> Path:
    backup_dir = path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup = backup_dir / f"{path.stem}_{stamp}{path.suffix}"
    shutil.copy2(path, backup)
    return backup


def score_candidates(snapshots: list[StockSnapshot]) -> list[CandidateScore]:
    scored = [_score_snapshot(snapshot) for snapshot in snapshots]
    return sorted(scored, key=lambda item: item.score, reverse=True)


def construct_portfolio(
    snapshots: list[StockSnapshot],
    *,
    initial_cash: float = 1_000_000.0,
    max_positions: int = 5,
    max_weight: float = 0.30,
    min_score: float = 40.0,
    cash_reserve: float = 0.10,
    name: str = DEFAULT_ACCOUNT,
    overwrite: bool = True,
    base_dir: Path | None = None,
) -> tuple[PortfolioAccount, list[CandidateScore]]:
    scores = score_candidates(snapshots)
    account = create_account(initial_cash=initial_cash, name=name, overwrite=overwrite, base_dir=base_dir)
    selected = [
        score for score in scores
        if score.score >= min_score and score.price is not None and score.price > 0
    ][:max(max_positions, 1)]

    investable = account.initial_cash * (1 - _clamp(cash_reserve, 0.0, 0.8))
    weights = _target_weights(selected, max_weight)
    holdings: list[Holding] = []
    used_cash = 0.0
    transactions: list[dict[str, Any]] = []
    for score, weight in zip(selected, weights, strict=False):
        assert score.price is not None
        market_value = investable * weight
        shares = math.floor(market_value / score.price)
        if shares <= 0:
            continue
        actual_value = shares * score.price
        used_cash += actual_value
        holdings.append(Holding(
            symbol=score.symbol,
            shares=float(shares),
            avg_cost=float(score.price),
            last_price=float(score.price),
            market_value=float(actual_value),
            weight=0.0,
            thesis=score.thesis,
        ))
        transactions.append(_transaction(
            action="BUY",
            symbol=score.symbol,
            shares=float(shares),
            price=float(score.price),
            reason=f"initial build: {score.thesis}",
        ))

    account.cash = account.initial_cash - used_cash
    account.holdings = _normalize_holding_weights(holdings, account.cash)
    account.transactions = transactions
    account.updated_at = _iso_now()
    account.history.append(_history_row(account, event="construct", notes="initial paper portfolio"))
    save_account(account, base_dir)
    return account, scores


def rebalance_portfolio(
    snapshots: list[StockSnapshot],
    *,
    name: str = DEFAULT_ACCOUNT,
    max_positions: int = 5,
    max_weight: float = 0.30,
    min_score: float = 40.0,
    cash_reserve: float = 0.10,
    base_dir: Path | None = None,
) -> tuple[PortfolioAccount, list[CandidateScore]]:
    account = load_account(name, base_dir)
    latest_prices = {
        snapshot.symbol: snapshot.quote.price
        for snapshot in snapshots
        if snapshot.quote.price is not None and snapshot.quote.price > 0
    }
    for holding in account.holdings:
        latest_price = latest_prices.get(holding.symbol)
        if latest_price is not None:
            holding.last_price = float(latest_price)
            holding.market_value = holding.shares * float(latest_price)
    account.holdings = _normalize_holding_weights(account.holdings, account.cash)
    old_holdings = {holding.symbol: replace(holding) for holding in account.holdings}
    total = portfolio_value(account)
    scores = score_candidates(snapshots)
    selected = [
        score for score in scores
        if score.score >= min_score and score.price is not None and score.price > 0
    ][:max(max_positions, 1)]
    investable = total * (1 - _clamp(cash_reserve, 0.0, 0.8))
    weights = _target_weights(selected, max_weight)
    holdings: list[Holding] = []
    used_cash = 0.0
    for score, weight in zip(selected, weights, strict=False):
        assert score.price is not None
        market_value = investable * weight
        shares = math.floor(market_value / score.price)
        if shares <= 0:
            continue
        actual_value = shares * score.price
        used_cash += actual_value
        holdings.append(Holding(
            symbol=score.symbol,
            shares=float(shares),
            avg_cost=_rebalance_avg_cost(old_holdings.get(score.symbol), float(shares), float(score.price)),
            last_price=float(score.price),
            market_value=float(actual_value),
            weight=0.0,
            thesis=score.thesis,
        ))
    _record_rebalance_transactions(account, old_holdings, holdings)
    account.cash = total - used_cash
    account.holdings = _normalize_holding_weights(holdings, account.cash)
    account.updated_at = _iso_now()
    account.history.append(_history_row(account, event="rebalance", notes="paper portfolio rebalance"))
    save_account(account, base_dir)
    return account, scores


def mark_to_market(
    *,
    get_quote: Callable[[str], Quote],
    name: str = DEFAULT_ACCOUNT,
    base_dir: Path | None = None,
    notes: str = "",
) -> PortfolioAccount:
    account = load_account(name, base_dir)
    prices: dict[str, float] = {}
    for holding in account.holdings:
        quote = get_quote(holding.symbol)
        if quote.price is not None and quote.price > 0:
            prices[holding.symbol] = float(quote.price)
            holding.last_price = float(quote.price)
            holding.market_value = holding.shares * float(quote.price)
    account.holdings = _normalize_holding_weights(account.holdings, account.cash)
    account.updated_at = _iso_now()
    account.history.append(_history_row(account, event="mark", notes=notes or "mark to market", prices=prices))
    save_account(account, base_dir)
    return account


def sell_holding(
    symbol: str,
    *,
    shares: float | str = "all",
    price: float | None = None,
    reason: str = "manual sell",
    name: str = DEFAULT_ACCOUNT,
    base_dir: Path | None = None,
) -> PortfolioAccount:
    account = load_account(name, base_dir)
    target = symbol.upper()
    sell_all = str(shares).lower() == "all"
    requested_shares: float | None = None
    if not sell_all:
        try:
            requested_shares = float(shares)
        except (TypeError, ValueError):
            account.history.append(_history_row(account, event="sell_failed", notes=f"invalid shares: {shares}"))
            save_account(account, base_dir)
            return account
        if requested_shares <= 0:
            account.history.append(_history_row(account, event="sell_failed", notes=f"invalid shares: {shares}"))
            save_account(account, base_dir)
            return account
    remaining: list[Holding] = []
    sold = False
    for holding in account.holdings:
        if holding.symbol.upper() != target:
            remaining.append(holding)
            continue
        sell_shares = holding.shares if sell_all else min(requested_shares or 0.0, holding.shares)
        if sell_shares <= 0:
            remaining.append(holding)
            continue
        sell_price = float(price if price is not None and price > 0 else holding.last_price)
        if sell_price <= 0:
            remaining.append(holding)
            continue
        proceeds = sell_shares * sell_price
        realized = (sell_price - holding.avg_cost) * sell_shares
        account.cash += proceeds
        account.transactions.append(_transaction(
            action="SELL",
            symbol=holding.symbol,
            shares=float(sell_shares),
            price=sell_price,
            reason=reason,
            realized_pnl=realized,
        ))
        sold = True
        left = holding.shares - sell_shares
        if left > 0:
            holding.shares = left
            holding.last_price = sell_price
            holding.market_value = left * sell_price
            remaining.append(holding)
    if not sold:
        account.history.append(_history_row(account, event="sell_failed", notes=f"{target} not held"))
    else:
        account.holdings = _normalize_holding_weights(remaining, account.cash)
        account.updated_at = _iso_now()
        account.history.append(_history_row(account, event="sell", notes=reason))
    save_account(account, base_dir)
    return account


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


def render_portfolio_review(account: PortfolioAccount, scores: list[CandidateScore]) -> str:
    total = portfolio_value(account)
    score_by_symbol = {score.symbol.upper(): score for score in scores}
    held_symbols = {holding.symbol.upper() for holding in account.holdings}
    rows: list[tuple[Holding, CandidateScore | None, float]] = []
    for holding in account.holdings:
        pnl_pct = (holding.last_price / holding.avg_cost - 1) * 100 if holding.avg_cost else 0.0
        rows.append((holding, score_by_symbol.get(holding.symbol.upper()), pnl_pct))

    lines = [
        f"# 纸面组合诊断：{account.name}",
        "",
        f"- 当前净值: {_money(total)}",
        f"- 现金占比: {(account.cash / total * 100) if total else 0:.2f}%",
        "- 性质: 只读诊断，不会改仓；真正调整请再执行 `/portfolio rebalance ...` 或 `/portfolio sell ...`。",
        "",
        "## 持仓复盘",
        "| 标的 | 权重 | 持仓收益 | 当前评分 | 诊断 | 主要依据 |",
        "|---|---:|---:|---:|---|---|",
    ]
    for holding, score, pnl_pct in rows:
        diagnosis = _holding_diagnosis(holding, score, pnl_pct)
        score_text = f"{score.score:.1f}" if score else "NA"
        lines.append(
            f"| {holding.symbol} | {holding.weight * 100:.2f}% | {pnl_pct:.2f}% | "
            f"{score_text} | {diagnosis} | "
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
        "",
        "## 持仓",
    ]
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


def _score_snapshot(snapshot: StockSnapshot) -> CandidateScore:
    q = snapshot.quote
    f = snapshot.financials
    i = snapshot.indicators
    score = 50.0
    components: dict[str, float] = {"momentum": 0.0, "quality": 0.0, "risk": 0.0, "data": 0.0}
    thesis: list[str] = []
    warnings: list[str] = []

    ret_1m = _num(i.get("return_1m_pct"))
    ret_3m = _num(i.get("return_3m_pct"))
    ret_1y = _num(i.get("return_1y_pct"))
    price_vs_ma20 = _num(i.get("price_vs_ma20_pct"))
    price_vs_ma60 = _num(i.get("price_vs_ma60_pct"))
    macd_hist = _num(i.get("macd_histogram"))
    rsi = _num(i.get("rsi14"))
    vol = _num(i.get("annualized_volatility_pct"))
    pe = _num(f.pe_ratio or q.pe_ratio)
    roe = _ratio_pct(f.return_on_equity)
    margin = _ratio_pct(f.profit_margin)

    if ret_1m is not None:
        delta = _clamp(ret_1m * 0.25, -4.0, 5.0)
        components["momentum"] += delta
        thesis.append(f"1月收益{ret_1m:.1f}%")
    if ret_3m is not None:
        delta = _clamp(ret_3m * 0.35, -12.0, 14.0)
        components["momentum"] += delta
        thesis.append(f"3月收益{ret_3m:.1f}%")
    if ret_1y is not None:
        delta = _clamp(ret_1y / 8.0, -12.0, 10.0)
        components["momentum"] += delta
        thesis.append(f"1年收益{ret_1y:.1f}%")
        if ret_1y < -15:
            components["momentum"] -= 8
            warnings.append(f"1年相对弱势 {ret_1y:.1f}%")
    if ret_3m is not None and ret_1y is not None:
        if ret_3m > 8 and ret_1y > 15:
            components["momentum"] += 5
            thesis.append("相对强度较好")
        elif ret_3m > 0 and ret_1y < -10:
            components["momentum"] -= 6
            warnings.append("短期反弹但中长期相对强度不足")
    if price_vs_ma20 is not None:
        if price_vs_ma20 > 2:
            components["momentum"] += 2
        elif price_vs_ma20 < -3:
            components["momentum"] -= 3
            warnings.append(f"价格低于 MA20 {abs(price_vs_ma20):.1f}%")
    if price_vs_ma60 is not None:
        if price_vs_ma60 > 4:
            components["momentum"] += 3
        elif price_vs_ma60 < -5:
            components["momentum"] -= 5
            warnings.append(f"价格低于 MA60 {abs(price_vs_ma60):.1f}%")
    if macd_hist is not None:
        components["momentum"] += 2 if macd_hist > 0 else -2
    if rsi is not None:
        if rsi >= 75:
            components["risk"] -= 4
            warnings.append(f"RSI 偏热 {rsi:.1f}")
        elif rsi <= 25:
            components["risk"] -= 3
            warnings.append(f"RSI 偏弱 {rsi:.1f}")
    if vol is not None:
        if vol < 25:
            components["risk"] += 4
            thesis.append("波动率较低")
        elif vol > 65:
            components["risk"] -= 12
            warnings.append(f"年化波动率过高 {vol:.1f}%")
        elif vol > 55:
            components["risk"] -= 8
            warnings.append(f"年化波动率偏高 {vol:.1f}%")
    if pe is not None:
        if 0 < pe < 25:
            components["quality"] += 6
            thesis.append(f"PE {pe:.1f} 不高")
        elif 25 <= pe <= 45:
            components["quality"] += 2
        elif pe > 60:
            components["quality"] -= 8
            warnings.append(f"PE 偏高 {pe:.1f}")
    if f.free_cash_flow is not None:
        if f.free_cash_flow > 0:
            components["quality"] += 6
            thesis.append("自由现金流为正")
        else:
            components["quality"] -= 8
            warnings.append("自由现金流为负")
    if margin is not None:
        if margin > 20:
            components["quality"] += 5
            thesis.append(f"利润率{margin:.1f}%")
        elif margin > 10:
            components["quality"] += 2
        elif margin < 5:
            components["quality"] -= 5
            warnings.append(f"利润率偏低 {margin:.1f}%")
    if roe is not None:
        if roe > 20:
            components["quality"] += 5
            thesis.append(f"ROE {roe:.1f}%")
        elif roe > 15:
            components["quality"] += 3
            thesis.append(f"ROE {roe:.1f}%")
        elif roe < 5:
            components["quality"] -= 5
            warnings.append(f"ROE 偏低 {roe:.1f}%")

    components["data"] += _source_adjustment(q.source, "行情", warnings)
    components["data"] += _source_adjustment(f.source, "基本面", warnings)
    if q.as_of and not q.is_realtime:
        components["data"] -= 5
        warnings.append("行情时间可能延迟")
    if q.price is None or q.price <= 0:
        components["data"] -= 50
        warnings.append("缺少有效价格，不能建仓")

    for key in components:
        components[key] = round(components[key], 2)
        score += components[key]
    score = _clamp(score, 0.0, 100.0)
    verdict = _score_verdict(score, warnings)
    return CandidateScore(
        symbol=snapshot.symbol,
        score=score,
        target_weight=0.0,
        price=q.price,
        source=f"{q.source}/{f.source}",
        thesis="；".join(thesis[:5]) or "正面证据不足",
        warnings=warnings,
        components=components,
        verdict=verdict,
    )


def _source_adjustment(source: str, label: str, warnings: list[str]) -> float:
    if source == "SKIPPED":
        warnings.append(f"{label}未抓取，诊断置信度下降")
        return -6
    if source == "UNAVAILABLE" or source == "":
        warnings.append(f"{label}数据不可用")
        return -16 if label == "行情" else -12
    if source == "SAMPLE_FALLBACK":
        warnings.append(f"{label}数据源为样例 fallback")
        return -25 if label == "行情" else -10
    return 0.0


def _score_verdict(score: float, warnings: list[str]) -> str:
    weak_signal = any("相对弱势" in warning or "中长期相对强度不足" in warning for warning in warnings)
    if weak_signal:
        return "相对弱势"
    if score >= 65 and not weak_signal:
        return "核心候选"
    if score >= 52 and not weak_signal:
        return "可持有/跟踪"
    if score >= 42:
        return "边际候选"
    return "弱候选"


def _holding_diagnosis(holding: Holding, score: CandidateScore | None, pnl_pct: float) -> str:
    if score is None:
        return "缺少新评分，先核验数据"
    if _is_weak_holding(score, pnl_pct):
        return "低置信持仓，优先复核/替换"
    if score.score >= 60 and pnl_pct >= -3:
        return "继续持有观察"
    if pnl_pct < -5:
        return "跑输明显，设置减仓观察"
    if score.score < 48:
        return "边际持仓，等待替代"
    return "中性持仓"


def _is_weak_holding(score: CandidateScore, pnl_pct: float) -> bool:
    if score.score < 42:
        return True
    if score.score < 50 and pnl_pct < 0:
        return True
    return any("相对弱势" in warning or "中长期相对强度不足" in warning for warning in score.warnings)


def _review_basis(score: CandidateScore | None) -> str:
    if score is None:
        return "无评分"
    pieces = [score.verdict or "未分级", _component_text(score)]
    if score.thesis:
        pieces.append(score.thesis[:70])
    return "；".join(piece for piece in pieces if piece)


def _component_text(score: CandidateScore) -> str:
    if not score.components:
        return ""
    order = (("momentum", "动量"), ("quality", "质量"), ("risk", "风险"), ("data", "数据"))
    return " ".join(f"{label}{score.components.get(key, 0):+.1f}" for key, label in order)


def _target_weights(scores: list[CandidateScore], max_weight: float) -> list[float]:
    if not scores:
        return []
    raw = [max(score.score - 30.0, 1.0) for score in scores]
    total = sum(raw)
    weights = [min(value / total, max_weight) for value in raw]
    remaining = 1.0 - sum(weights)
    uncapped = [idx for idx, weight in enumerate(weights) if weight < max_weight]
    while remaining > 0.0001 and uncapped:
        add = remaining / len(uncapped)
        next_uncapped = []
        for idx in uncapped:
            room = max_weight - weights[idx]
            actual = min(add, room)
            weights[idx] += actual
            remaining -= actual
            if weights[idx] < max_weight - 0.0001:
                next_uncapped.append(idx)
        if len(next_uncapped) == len(uncapped):
            break
        uncapped = next_uncapped
    for score, weight in zip(scores, weights, strict=False):
        score.target_weight = weight
    return weights


def _normalize_holding_weights(holdings: list[Holding], cash: float) -> list[Holding]:
    total = cash + sum(holding.market_value for holding in holdings)
    if total <= 0:
        return holdings
    for holding in holdings:
        holding.weight = holding.market_value / total
    return holdings


def _record_rebalance_transactions(
    account: PortfolioAccount,
    old_holdings: dict[str, Holding],
    new_holdings: list[Holding],
) -> None:
    new_by_symbol = {holding.symbol: holding for holding in new_holdings}
    for symbol, old in old_holdings.items():
        new = new_by_symbol.get(symbol)
        new_shares = new.shares if new else 0.0
        if old.shares > new_shares:
            sold = old.shares - new_shares
            price = new.last_price if new else old.last_price
            account.transactions.append(_transaction(
                action="SELL",
                symbol=symbol,
                shares=sold,
                price=price,
                reason="rebalance reduce/exit",
                realized_pnl=(price - old.avg_cost) * sold,
            ))
    for new in new_holdings:
        old = old_holdings.get(new.symbol)
        old_shares = old.shares if old else 0.0
        if new.shares > old_shares:
            bought = new.shares - old_shares
            account.transactions.append(_transaction(
                action="BUY",
                symbol=new.symbol,
                shares=bought,
                price=new.last_price,
                reason=f"rebalance add: {new.thesis}",
            ))


def _rebalance_avg_cost(old: Holding | None, new_shares: float, trade_price: float) -> float:
    if old is None or old.shares <= 0 or new_shares <= 0:
        return trade_price
    if new_shares <= old.shares:
        return old.avg_cost
    bought = new_shares - old.shares
    return ((old.avg_cost * old.shares) + (trade_price * bought)) / new_shares


def _transaction(
    *,
    action: str,
    symbol: str,
    shares: float,
    price: float,
    reason: str,
    realized_pnl: float = 0.0,
) -> dict[str, Any]:
    return {
        "as_of": _iso_now(),
        "action": action,
        "symbol": symbol,
        "shares": shares,
        "price": price,
        "amount": shares * price,
        "realized_pnl": realized_pnl,
        "reason": reason,
    }


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


def _history_row(
    account: PortfolioAccount,
    *,
    event: str,
    notes: str = "",
    prices: dict[str, float] | None = None,
) -> dict[str, Any]:
    total = portfolio_value(account)
    ret = (total - account.initial_cash) / account.initial_cash * 100 if account.initial_cash else 0.0
    return {
        "as_of": _iso_now(),
        "event": event,
        "total_value": total,
        "cash": account.cash,
        "return_pct": ret,
        "positions": [
            {
                "symbol": holding.symbol,
                "shares": holding.shares,
                "price": holding.last_price,
                "market_value": holding.market_value,
                "weight": holding.weight,
            }
            for holding in account.holdings
        ],
        "prices": prices or {},
        "notes": notes,
    }


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number):
        return None
    return number


def _ratio_pct(value: Any) -> float | None:
    number = _num(value)
    if number is None:
        return None
    if abs(number) <= 2:
        return number * 100
    return number


def _clamp(value: float, low: float, high: float) -> float:
    return min(max(value, low), high)


def _money(value: Any) -> str:
    if value is None:
        return "NA"
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def _iso_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
