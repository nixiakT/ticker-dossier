"""Deterministic debate used when the model debate is unavailable."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import StockSnapshot
from .rendering import _fmt_big, _fmt_number, _fmt_percent, _ratio_to_pct


@dataclass
class DebateRoleResult:
    role: str
    stance: str
    arguments: list[str]
    concerns: list[str]


def debate_stock(snapshot: StockSnapshot) -> str:
    roles = [
        _bull(snapshot),
        _bear(snapshot),
        _value(snapshot),
        _buffett(snapshot),
        _munger(snapshot),
        _duan(snapshot),
        _li_lu(snapshot),
        _dalio(snapshot),
        _anti_bias(snapshot),
        _macro(snapshot),
        _risk(snapshot),
    ]
    return render_debate(snapshot, roles)


def rule_based_decision(snapshot: StockSnapshot) -> dict[str, Any]:
    """Return the deterministic fallback decision in the model judge shape."""
    trend = snapshot.indicators.get("return_3m_pct")
    if trend is None or abs(float(trend)) < 2:
        direction, confidence = "neutral", 0.5
    else:
        direction = "up" if float(trend) > 0 else "down"
        confidence = min(0.5 + abs(float(trend)) / 100, 0.85)
    return {
        "score": float(_judge_score(snapshot)),
        "conclusion": _judge_summary(snapshot),
        "core_disagreement": "规则模式未执行模型交叉质询；" + _core_disagreement([
            _bull(snapshot),
            _bear(snapshot),
            _value(snapshot),
            _macro(snapshot),
            _anti_bias(snapshot),
        ]),
        "direction": direction,
        "confidence": confidence,
        "horizon_days": 30,
        "supporting_evidence": [],
        "invalid_or_unverified_claims": ["未执行独立模型分析和交叉反驳。"],
        "next_checks": ["恢复模型服务后重新运行 /debate。"],
    }


def debate_stocks(snapshots: list[StockSnapshot]) -> str:
    lines = ["# 多智能体辩论选股", ""]
    for snapshot in snapshots:
        lines.append(debate_stock(snapshot))
        lines.append("")
    lines.append("## 裁判横向结论")
    ranking = sorted(snapshots, key=_judge_score, reverse=True)
    for index, snapshot in enumerate(ranking, start=1):
        lines.append(f"{index}. {snapshot.symbol}: {_judge_summary(snapshot)}")
    lines.append("")
    lines.append("注意：排序只代表研究优先级，不代表买入建议。")
    return "\n".join(lines)


def render_debate(snapshot: StockSnapshot, roles: list[DebateRoleResult]) -> str:
    lines = [f"## {snapshot.symbol} 辩论", ""]
    for role in roles:
        lines.append(f"### {role.role}")
        lines.append(f"- 立场: {role.stance}")
        for arg in role.arguments:
            lines.append(f"- 支持理由: {arg}")
        for concern in role.concerns:
            lines.append(f"- 质疑点: {concern}")
        lines.append("")
    lines.append("### Judge Agent")
    lines.append(f"- 综合评分: {_judge_score(snapshot)}/10")
    lines.append(f"- 纪律结论: {_discipline_label(snapshot)}")
    lines.append(f"- 研究结论: {_judge_summary(snapshot)}")
    lines.append(f"- 核心分歧: {_core_disagreement(roles)}")
    lines.append(f"- 镜子测试: {_mirror_test(snapshot)}")
    lines.append(f"- 可检验预测: {_testable_prediction(snapshot)}")
    lines.append("- 下一步验证: 阅读最新财报/公告、拆解收入驱动、比较同业估值、做情景估值。")
    return "\n".join(lines).strip()


def _bull(snapshot: StockSnapshot) -> DebateRoleResult:
    q = snapshot.quote
    f = snapshot.financials
    i = snapshot.indicators
    args = []
    concerns = []
    if i.get("return_3m_pct") is not None and i["return_3m_pct"] > 0:
        args.append(f"近 3 月收益率为 {_fmt_percent(i['return_3m_pct'])}，价格动量偏正。")
    if f.profit_margin is not None and f.profit_margin > 0.15:
        args.append(f"利润率约 {_fmt_percent(_ratio_to_pct(f.profit_margin))}，盈利质量有跟踪价值。")
    if f.free_cash_flow is not None and f.free_cash_flow > 0:
        args.append(f"自由现金流为 {_fmt_big(f.free_cash_flow)}，支持长期价值讨论。")
    if not args:
        args.append("当前正面证据不足，多头需要更多业务和财务数据支持。")
    if q.source == "SAMPLE_FALLBACK" or f.source == "SAMPLE_FALLBACK":
        concerns.append("当前是样例数据，多头论证不能用于真实投资。")
    return DebateRoleResult("Bull Agent", "寻找上涨和继续跟踪理由", args, concerns)


def _bear(snapshot: StockSnapshot) -> DebateRoleResult:
    q = snapshot.quote
    f = snapshot.financials
    i = snapshot.indicators
    args = []
    concerns = []
    pe = f.pe_ratio or q.pe_ratio
    if pe is not None and pe > 40:
        args.append(f"PE 约 {_fmt_number(pe)}，估值对增长不及预期较敏感。")
    if i.get("annualized_volatility_pct") is not None and i["annualized_volatility_pct"] > 35:
        args.append(f"年化波动率约 {_fmt_percent(i['annualized_volatility_pct'])}，回撤风险需要重视。")
    if f.free_cash_flow is not None and f.free_cash_flow < 0:
        args.append("自由现金流为负，增长质量需要质疑。")
    if not args:
        args.append("未发现明显量化空头证据，但仍需查行业竞争和监管风险。")
    if snapshot.news:
        concerns.append("新闻事件需要逐条核验，避免只看标题做判断。")
    return DebateRoleResult("Bear Agent", "寻找风险和反例", args, concerns)


def _value(snapshot: StockSnapshot) -> DebateRoleResult:
    f = snapshot.financials
    args = []
    concerns = []
    if f.pe_ratio is not None:
        args.append(f"当前 PE: {_fmt_number(f.pe_ratio)}，需与增长、ROE、同业估值一起看。")
    if f.return_on_equity is not None:
        args.append(f"ROE: {_fmt_percent(_ratio_to_pct(f.return_on_equity))}，反映资本效率。")
    if f.free_cash_flow is not None:
        args.append(f"自由现金流: {_fmt_big(f.free_cash_flow)}。")
    if f.pe_ratio is None or f.return_on_equity is None:
        concerns.append("估值或 ROE 字段缺失，无法形成完整价值判断。")
    return DebateRoleResult("Value Agent", "评估估值、现金流和护城河线索", args or ["基本面数据不足。"], concerns)


def _macro(snapshot: StockSnapshot) -> DebateRoleResult:
    q = snapshot.quote
    i = snapshot.indicators
    args = [
        f"计价货币: {q.currency or '未知'}，需要结合利率、汇率和市场风险偏好。",
        f"近 1 年收益率: {_fmt_percent(i.get('return_1y_pct'))}，可作为周期位置的价格线索。",
    ]
    concerns = ["当前尚未接入利率、通胀和行业景气度数据，宏观判断只是框架提示。"]
    return DebateRoleResult("Macro Agent", "评估宏观周期和流动性影响", args, concerns)


def _buffett(snapshot: StockSnapshot) -> DebateRoleResult:
    f = snapshot.financials
    args = []
    concerns = []
    if f.free_cash_flow is not None and f.free_cash_flow > 0:
        args.append("自由现金流为正，可进入 owner earnings 和长期护城河讨论。")
    if f.profit_margin is not None and f.profit_margin > 0.15:
        args.append(f"利润率约 {_fmt_percent(_ratio_to_pct(f.profit_margin))}，显示生意质量线索。")
    if f.pe_ratio is not None and f.pe_ratio < 30:
        args.append(f"PE 约 {_fmt_number(f.pe_ratio)}，估值没有明显进入极端区间。")
    if f.free_cash_flow is None:
        concerns.append("缺少自由现金流，无法判断真实可分配现金。")
    if f.pe_ratio is not None and f.pe_ratio > 50:
        concerns.append("估值较高，安全边际需要更强增长假设支撑。")
    return DebateRoleResult("Buffett Agent", "好生意、护城河、现金流与安全边际", args or ["需要先证明生意可理解且现金流可持续。"], concerns)


def _munger(snapshot: StockSnapshot) -> DebateRoleResult:
    f = snapshot.financials
    args = []
    concerns = ["需要列出反方证据，避免因为热门叙事而降低质量门槛。"]
    if f.return_on_equity is not None and f.return_on_equity > 0.15:
        args.append(f"ROE 约 {_fmt_percent(_ratio_to_pct(f.return_on_equity))}，资本效率值得进一步验证。")
    if f.debt_to_equity is not None and f.debt_to_equity < 100:
        args.append("债务权益比未显示极端杠杆压力。")
    if not args:
        args.append("目前缺少足够的高质量企业证据。")
    return DebateRoleResult("Munger Agent", "反向思考、机会成本和认知偏差", args, concerns)


def _duan(snapshot: StockSnapshot) -> DebateRoleResult:
    f = snapshot.financials
    args = []
    concerns = ["需要验证用户价值、企业文化和管理层，而不是只看短期价格。"]
    if f.profit_margin is not None and f.profit_margin > 0.12:
        args.append("利润率具备观察价值，可能反映产品/服务有一定用户价值。")
    if f.revenue is not None and f.revenue > 0:
        args.append(f"营收为 {_fmt_big(f.revenue)}，需要拆分增长质量和可持续性。")
    if not args:
        args.append("缺少商业模式和用户价值证据，先保持不懂不投。")
    return DebateRoleResult("Duan Agent", "好生意、用户价值和长期主义", args, concerns)


def _li_lu(snapshot: StockSnapshot) -> DebateRoleResult:
    f = snapshot.financials
    args = []
    concerns = ["需要证明长期确定性和下行保护，而不是只证明短期便宜或热门。"]
    if f.free_cash_flow is not None and f.free_cash_flow > 0:
        args.append("自由现金流为正，可继续讨论内在价值和安全边际。")
    if f.debt_to_equity is not None and f.debt_to_equity < 80:
        args.append("杠杆没有显示极端压力，长期生存风险需要进一步量化。")
    if f.return_on_equity is not None and f.return_on_equity > 0.12:
        args.append(f"ROE 约 {_fmt_percent(_ratio_to_pct(f.return_on_equity))}，具备长期资本回报观察价值。")
    if f.free_cash_flow is None or f.return_on_equity is None:
        concerns.append("缺少现金流或资本回报数据时，应归入灰色地带。")
    return DebateRoleResult("Li Lu Agent", "长期确定性、信息边界和安全边际", args or ["信息边界不足，先不把它放进高确定性清单。"], concerns)


def _dalio(snapshot: StockSnapshot) -> DebateRoleResult:
    q = snapshot.quote
    i = snapshot.indicators
    args = [
        f"货币与市场: {q.currency or '未知'}，需结合利率、美元流动性和风险偏好。",
        f"波动率线索: {_fmt_percent(i.get('annualized_volatility_pct'))}，影响组合风险预算。",
    ]
    concerns = ["单一股票暴露需要放进组合看相关性和最坏情形。"]
    return DebateRoleResult("Dalio Agent", "宏观周期、相关性和风险平价视角", args, concerns)


def _anti_bias(snapshot: StockSnapshot) -> DebateRoleResult:
    concerns = [
        "区分事实和推断；新闻标题不能替代公告、财报和电话会原文。",
        "记录预测并事后评分，否则无法知道研究框架是否真的有效。",
    ]
    args = [
        f"当前数据源: {snapshot.quote.source}/{snapshot.financials.source}。",
        f"行情时间: {snapshot.quote.as_of or '未知'}。",
    ]
    if snapshot.quote.source == "SAMPLE_FALLBACK" or snapshot.financials.source == "SAMPLE_FALLBACK":
        concerns.append("样例数据出现时，必须暂停真实判断。")
    return DebateRoleResult("Anti-Bias Agent", "反确认偏误、反叙事过拟合和可检验性", args, concerns)


def _risk(snapshot: StockSnapshot) -> DebateRoleResult:
    q = snapshot.quote
    f = snapshot.financials
    i = snapshot.indicators
    args = []
    concerns = []
    if i.get("annualized_volatility_pct") is not None:
        args.append(f"年化波动率: {_fmt_percent(i['annualized_volatility_pct'])}。")
    if f.debt_to_equity is not None:
        args.append(f"债务权益比: {_fmt_number(f.debt_to_equity)}。")
    if q.source == "SAMPLE_FALLBACK" or f.source == "SAMPLE_FALLBACK":
        concerns.append("样例数据风险最高，必须先替换为真实行情和财务数据。")
    if not snapshot.news:
        concerns.append("缺少新闻/公告，事件风险覆盖不足。")
    return DebateRoleResult("Risk Agent", "评估回撤、杠杆和数据风险", args or ["风险数据不足。"], concerns)


def _judge_score(snapshot: StockSnapshot) -> int:
    q = snapshot.quote
    f = snapshot.financials
    i = snapshot.indicators
    if q.source == "SAMPLE_FALLBACK" or f.source == "SAMPLE_FALLBACK":
        return 1
    score = 0
    pe = f.pe_ratio or q.pe_ratio
    if pe is not None and pe < 40:
        score += 2
    if f.free_cash_flow is not None and f.free_cash_flow > 0:
        score += 2
    if f.profit_margin is not None and f.profit_margin > 0.15:
        score += 2
    if i.get("return_3m_pct") is not None and i["return_3m_pct"] > 0:
        score += 1
    if i.get("annualized_volatility_pct") is not None and i["annualized_volatility_pct"] < 45:
        score += 1
    if snapshot.news:
        score += 1
    if q.is_realtime:
        score += 1
    return min(score, 10)


def _judge_summary(snapshot: StockSnapshot) -> str:
    score = _judge_score(snapshot)
    if snapshot.quote.source == "SAMPLE_FALLBACK" or snapshot.financials.source == "SAMPLE_FALLBACK":
        return "仅适合演示辩论流程；真实选股前必须替换数据源。"
    if score >= 7:
        return "值得进入深入研究池，但仍需财报、估值和竞争格局验证。"
    if score >= 4:
        return "可继续观察，当前多空证据较均衡。"
    return "暂不进入优先研究池，主要因为数据不足或风险信号偏多。"


def _discipline_label(snapshot: StockSnapshot) -> str:
    score = _judge_score(snapshot)
    if snapshot.quote.source == "SAMPLE_FALLBACK" or snapshot.financials.source == "SAMPLE_FALLBACK":
        return "不通过 - 数据源不可用于真实判断。"
    if score >= 7:
        return "通过深入研究 - 进入候选池，但不等于买入。"
    if score >= 4:
        return "灰色地带 - 继续跟踪，等待关键证据。"
    return "不通过 - 当前证据不足或风险收益不清晰。"


def _mirror_test(snapshot: StockSnapshot) -> str:
    f = snapshot.financials
    missing = []
    if f.revenue is None:
        missing.append("收入驱动")
    if f.free_cash_flow is None:
        missing.append("自由现金流")
    if f.return_on_equity is None:
        missing.append("资本回报")
    if missing:
        return f"未通过；无法用 5 句话讲清 {', '.join(missing)}。"
    return "初步通过；仍需用 5 句话讲清生意、护城河、现金流、估值和反方风险。"


def _core_disagreement(roles: list[DebateRoleResult]) -> str:
    concerns = [concern for role in roles for concern in role.concerns]
    if not concerns:
        return "当前分歧较少，主要需要补充财报和估值验证。"
    return concerns[0]


def _testable_prediction(snapshot: StockSnapshot) -> str:
    i = snapshot.indicators
    trend = i.get("return_3m_pct")
    if trend is not None and trend > 0:
        return f"未来 30 天相对当前价格能否维持正收益；应记录 baseline={_fmt_number(snapshot.quote.price)}。"
    if trend is not None and trend < 0:
        return f"未来 30 天是否继续弱于当前价格；应记录 baseline={_fmt_number(snapshot.quote.price)}。"
    return f"未来 30 天方向不明确；应先记录中性预测和 baseline={_fmt_number(snapshot.quote.price)}。"
