"""Deterministic offline sample-data adapter."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any

from ticker_dossier.research.models import Candle, Financials, NewsItem, Quote, utc_now_iso
from ticker_dossier.research.symbols import normalize_symbol

from .._normalization import _period_to_days
from ..base import ProviderError


class SampleDataProvider:
    name = "SAMPLE_FALLBACK"

    def get_quote(self, symbol: str) -> Quote:
        normalized = normalize_symbol(symbol)
        profile = _sample_profile(normalized)
        history = self.get_history(normalized, "1y", "1d")
        last = history[-1].close if history else profile["price"]
        prev = history[-2].close if len(history) > 1 else profile["price"] * 0.99
        change = last - prev if last is not None and prev is not None else None
        change_percent = change / prev * 100 if change is not None and prev else None
        return Quote(
            symbol=normalized,
            name=profile["name"],
            currency=profile["currency"],
            price=last,
            previous_close=prev,
            change=change,
            change_percent=change_percent,
            volume=profile["volume"],
            market_cap=profile["market_cap"],
            pe_ratio=profile["pe_ratio"],
            eps=profile["eps"],
            source=self.name,
            as_of=utc_now_iso(),
            is_realtime=False,
            notes=["样例 fallback 数据，仅用于离线演示；请勿当作真实行情。"],
        )

    def get_history(self, symbol: str, period: str = "1y", interval: str = "1d") -> list[Candle]:
        normalized = normalize_symbol(symbol)
        profile = _sample_profile(normalized)
        days = _period_to_days(period)
        base_price = profile["price"]
        trend = profile["trend"]
        volatility = profile["volatility"]
        candles: list[Candle] = []
        start = datetime.now(UTC).date() - timedelta(days=days * 7 // 5 + 10)
        trading_day = 0
        current = start
        while len(candles) < days:
            current += timedelta(days=1)
            if current.weekday() >= 5:
                continue
            progress = trading_day / max(days - 1, 1)
            seasonal = math.sin(trading_day / 9.0) * volatility + math.cos(trading_day / 23.0) * volatility * 0.6
            close = base_price * (1 + trend * (progress - 1) + seasonal)
            open_price = close * (1 - math.sin(trading_day / 5.0) * volatility * 0.25)
            high = max(open_price, close) * (1 + volatility * 0.4)
            low = min(open_price, close) * (1 - volatility * 0.4)
            candles.append(Candle(
                date=current.isoformat(),
                open=round(open_price, 2),
                high=round(high, 2),
                low=round(low, 2),
                close=round(close, 2),
                volume=int(profile["volume"] * (0.7 + 0.3 * (1 + math.sin(trading_day / 11.0)))),
            ))
            trading_day += 1
        return candles

    def get_financials(self, symbol: str) -> Financials:
        normalized = normalize_symbol(symbol)
        profile = _sample_profile(normalized)
        return Financials(
            symbol=normalized,
            source=self.name,
            as_of=utc_now_iso(),
            currency=str(profile.get("currency") or ""),
            period_type="sample",
            fetched_at=utc_now_iso(),
            market_cap=profile["market_cap"],
            pe_ratio=profile["pe_ratio"],
            forward_pe=profile["forward_pe"],
            eps=profile["eps"],
            revenue=profile["revenue"],
            gross_profit=profile["gross_profit"],
            net_income=profile["net_income"],
            free_cash_flow=profile["free_cash_flow"],
            debt_to_equity=profile["debt_to_equity"],
            return_on_equity=profile["return_on_equity"],
            profit_margin=profile["profit_margin"],
            notes=["样例 fallback 数据，仅用于离线演示；请接入真实数据源后再做研究。"],
        )

    def get_news(self, symbol: str, limit: int = 5) -> list[NewsItem]:
        normalized = normalize_symbol(symbol)
        profile = _sample_profile(normalized)
        rows = [
            f"{profile['name']} 发布最新经营数据，市场关注收入增长和利润率变化",
            f"分析师讨论 {profile['name']} 的估值水平与行业竞争格局",
            f"宏观利率和风险偏好变化可能影响 {profile['name']} 的估值倍数",
        ]
        return [
            NewsItem(
                title=row,
                publisher="Sample News",
                published_at=utc_now_iso(),
                source=self.name,
                summary="样例新闻，仅用于离线演示。",
            )
            for row in rows[:limit]
        ]


def _sample_profile(symbol: str) -> dict[str, Any]:
    normalized = normalize_symbol(symbol)
    if normalized not in _SAMPLE_PROFILES:
        raise ProviderError(f"no sample fallback profile for {normalized}")
    return _SAMPLE_PROFILES[normalized]


def _generic_profile(symbol: str) -> dict[str, Any]:
    return {
        "name": symbol,
        "currency": "USD",
        "price": 100.0,
        "volume": 10_000_000,
        "market_cap": 50_000_000_000,
        "pe_ratio": 22.0,
        "forward_pe": 20.0,
        "eps": 4.5,
        "revenue": 20_000_000_000,
        "gross_profit": 9_000_000_000,
        "net_income": 4_000_000_000,
        "free_cash_flow": 3_500_000_000,
        "debt_to_equity": 80.0,
        "return_on_equity": 0.18,
        "profit_margin": 0.2,
        "trend": 0.08,
        "volatility": 0.025,
    }


_SAMPLE_PROFILES: dict[str, dict[str, Any]] = {
    "AAPL": {
        **_generic_profile("AAPL"),
        "name": "Apple Inc.",
        "price": 210.0,
        "market_cap": 3_200_000_000_000,
        "pe_ratio": 31.0,
        "forward_pe": 28.0,
        "eps": 6.7,
        "revenue": 390_000_000_000,
        "gross_profit": 180_000_000_000,
        "net_income": 100_000_000_000,
        "free_cash_flow": 95_000_000_000,
        "debt_to_equity": 150.0,
        "return_on_equity": 1.2,
        "profit_margin": 0.25,
        "trend": 0.10,
        "volatility": 0.018,
    },
    "NVDA": {
        **_generic_profile("NVDA"),
        "name": "NVIDIA Corporation",
        "price": 145.0,
        "market_cap": 3_500_000_000_000,
        "pe_ratio": 45.0,
        "forward_pe": 35.0,
        "eps": 3.2,
        "revenue": 130_000_000_000,
        "gross_profit": 95_000_000_000,
        "net_income": 70_000_000_000,
        "free_cash_flow": 60_000_000_000,
        "debt_to_equity": 25.0,
        "return_on_equity": 0.85,
        "profit_margin": 0.54,
        "trend": 0.32,
        "volatility": 0.035,
    },
    "AMD": {
        **_generic_profile("AMD"),
        "name": "Advanced Micro Devices, Inc.",
        "price": 165.0,
        "market_cap": 270_000_000_000,
        "pe_ratio": 48.0,
        "forward_pe": 29.0,
        "eps": 3.4,
        "revenue": 28_000_000_000,
        "gross_profit": 14_000_000_000,
        "net_income": 4_200_000_000,
        "free_cash_flow": 3_000_000_000,
        "debt_to_equity": 7.0,
        "return_on_equity": 0.08,
        "profit_margin": 0.15,
        "trend": 0.18,
        "volatility": 0.04,
    },
    "TSLA": {
        **_generic_profile("TSLA"),
        "name": "Tesla, Inc.",
        "price": 260.0,
        "market_cap": 830_000_000_000,
        "pe_ratio": 70.0,
        "forward_pe": 55.0,
        "eps": 3.7,
        "revenue": 100_000_000_000,
        "gross_profit": 18_000_000_000,
        "net_income": 12_000_000_000,
        "free_cash_flow": 4_500_000_000,
        "debt_to_equity": 15.0,
        "return_on_equity": 0.18,
        "profit_margin": 0.12,
        "trend": 0.02,
        "volatility": 0.045,
    },
    "MSFT": {
        **_generic_profile("MSFT"),
        "name": "Microsoft Corporation",
        "price": 480.0,
        "market_cap": 3_600_000_000_000,
        "pe_ratio": 36.0,
        "forward_pe": 31.0,
        "eps": 13.2,
        "revenue": 260_000_000_000,
        "gross_profit": 180_000_000_000,
        "net_income": 95_000_000_000,
        "free_cash_flow": 75_000_000_000,
        "debt_to_equity": 35.0,
        "return_on_equity": 0.35,
        "profit_margin": 0.36,
        "trend": 0.15,
        "volatility": 0.02,
    },
    "600519.SS": {
        **_generic_profile("600519.SS"),
        "name": "Kweichow Moutai Co., Ltd.",
        "currency": "CNY",
        "price": 1500.0,
        "market_cap": 1_900_000_000_000,
        "pe_ratio": 23.0,
        "forward_pe": 21.0,
        "eps": 65.0,
        "revenue": 170_000_000_000,
        "gross_profit": 155_000_000_000,
        "net_income": 85_000_000_000,
        "free_cash_flow": 75_000_000_000,
        "debt_to_equity": 10.0,
        "return_on_equity": 0.32,
        "profit_margin": 0.50,
        "trend": 0.04,
        "volatility": 0.018,
    },
}
