"""Candidate scoring, holding diagnosis, and allocation helpers."""
from __future__ import annotations

import math
from typing import Any

from ticker_dossier.market_data.models import StockSnapshot
from ticker_dossier.portfolio.models import CandidateScore, Holding


def score_candidates(snapshots: list[StockSnapshot]) -> list[CandidateScore]:
    scored = [_score_snapshot(snapshot) for snapshot in snapshots]
    return sorted(scored, key=lambda item: item.score, reverse=True)


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
