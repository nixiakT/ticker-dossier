"""Investor framework distillation for structured research."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import Financials, Quote


@dataclass
class FrameworkResult:
    name: str
    stance: str
    score: int
    observations: list[str]
    questions: list[str]


def evaluate_frameworks(quote: Quote, financials: Financials, indicators: dict[str, Any]) -> list[FrameworkResult]:
    return [
        _buffett_munger(quote, financials, indicators),
        _duan_yongping(quote, financials, indicators),
        _dalio(quote, financials, indicators),
    ]


def format_framework_results(results: list[FrameworkResult]) -> str:
    lines: list[str] = []
    for result in results:
        lines.append(f"### {result.name}")
        lines.append(f"- 视角结论: {result.stance}（评分 {result.score}/5）")
        for obs in result.observations:
            lines.append(f"- 观察: {obs}")
        for question in result.questions:
            lines.append(f"- 待验证: {question}")
        lines.append("")
    return "\n".join(lines).strip()


def _buffett_munger(quote: Quote, financials: Financials, indicators: dict[str, Any]) -> FrameworkResult:
    score = 0
    observations: list[str] = []
    questions: list[str] = []

    if financials.profit_margin is not None and financials.profit_margin >= 0.2:
        score += 1
        observations.append("利润率较高，可能存在较好的生意质量或定价能力。")
    else:
        questions.append("利润率是否稳定，是否受周期或一次性因素影响？")

    if financials.return_on_equity is not None and financials.return_on_equity >= 0.15:
        score += 1
        observations.append("ROE 较高，值得进一步验证资本效率和可持续性。")
    else:
        questions.append("ROE 是否足以覆盖机会成本？")

    if financials.free_cash_flow is not None and financials.free_cash_flow > 0:
        score += 1
        observations.append("自由现金流为正，符合价值投资对现金创造能力的要求。")
    else:
        questions.append("自由现金流是否长期为正，还是被资本开支侵蚀？")

    if financials.pe_ratio is not None and financials.pe_ratio < 35:
        score += 1
        observations.append("估值倍数没有明显极端化，但仍需结合增长和质量判断安全边际。")
    else:
        questions.append("当前估值是否已透支未来增长，安全边际在哪里？")

    if quote.market_cap or financials.market_cap:
        score += 1
        observations.append("具备可量化市值基础，可进一步做每股价值和情景估值。")

    return FrameworkResult("巴菲特/芒格框架", _stance(score), score, observations, questions)


def _duan_yongping(quote: Quote, financials: Financials, indicators: dict[str, Any]) -> FrameworkResult:
    score = 0
    observations: list[str] = []
    questions: list[str] = []

    if financials.profit_margin is not None and financials.profit_margin >= 0.15:
        score += 1
        observations.append("利润率支持“好生意”假设，但仍要确认用户价值和竞争格局。")
    else:
        questions.append("这个生意是否真的有足够用户价值和长期利润空间？")

    if financials.free_cash_flow is not None and financials.free_cash_flow > 0:
        score += 1
        observations.append("现金流为正，有利于长期持有视角。")
    else:
        questions.append("公司是否需要持续融资才能维持增长？")

    if financials.debt_to_equity is not None and financials.debt_to_equity < 100:
        score += 1
        observations.append("杠杆水平未显著偏高，长期风险相对可控。")
    else:
        questions.append("资产负债表压力是否会破坏长期主义？")

    if indicators.get("return_1y_pct") is not None:
        score += 1
        observations.append("具备过去一年价格表现数据，可对照业务表现验证市场预期。")
    else:
        questions.append("缺少足够长期价格数据，不能只看短期波动。")

    if financials.pe_ratio is not None and financials.pe_ratio <= 50:
        score += 1
        observations.append("估值仍可进入“是否值得长期陪伴”的讨论。")
    else:
        questions.append("如果估值很贵，是否真的懂这家公司和未来十年的确定性？")

    return FrameworkResult("段永平框架", _stance(score), score, observations, questions)


def _dalio(quote: Quote, financials: Financials, indicators: dict[str, Any]) -> FrameworkResult:
    score = 0
    observations: list[str] = []
    questions: list[str] = []

    volatility = indicators.get("annualized_volatility_pct")
    if volatility is not None and volatility < 45:
        score += 1
        observations.append("年化波动率未处于极端高位，组合层面更容易控制风险。")
    else:
        questions.append("若波动较高，仓位和相关性如何控制？")

    if financials.debt_to_equity is not None and financials.debt_to_equity < 150:
        score += 1
        observations.append("杠杆风险未直接亮红灯，但仍需看利率周期和再融资压力。")
    else:
        questions.append("利率和信用周期变化是否会放大资产负债表风险？")

    if indicators.get("return_3m_pct") is not None:
        score += 1
        observations.append("有中期动量数据，可结合宏观风险偏好判断资产价格位置。")
    else:
        questions.append("缺少中期走势数据，难以判断周期位置。")

    if quote.currency:
        score += 1
        observations.append(f"计价货币为 {quote.currency}，需要纳入汇率和利率背景。")
    else:
        questions.append("缺少计价货币信息，宏观分析需要补充市场和汇率背景。")

    score += 1
    observations.append("单一股票不能替代全天候组合，需要和资产配置、相关性一起评估。")
    return FrameworkResult("达利欧宏观/风险框架", _stance(score), score, observations, questions)


def _stance(score: int) -> str:
    if score >= 4:
        return "偏正面，但仍需验证关键假设"
    if score >= 2:
        return "中性，适合继续跟踪"
    return "偏谨慎，当前信息不足或风险较高"
