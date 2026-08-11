"""Technical indicators for stock research."""
from __future__ import annotations

from typing import Any

import pandas as pd

from .models import Candle


def candles_to_frame(candles: list[Candle]) -> pd.DataFrame:
    rows = [
        {
            "date": candle.date,
            "open": candle.open,
            "high": candle.high,
            "low": candle.low,
            "close": candle.close,
            "volume": candle.volume,
        }
        for candle in candles
        if candle.close is not None
    ]
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.sort_values("date").reset_index(drop=True)
    for column in ["open", "high", "low", "close", "volume"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def calculate_indicators(candles: list[Candle]) -> dict[str, Any]:
    frame = candles_to_frame(candles)
    if frame.empty or frame["close"].dropna().empty:
        return {"error": "没有足够价格数据计算技术指标"}

    close = frame["close"].dropna()
    returns = close.pct_change().dropna()
    latest = close.iloc[-1]
    indicators: dict[str, Any] = {
        "last_close": _round(latest),
        "data_points": int(close.shape[0]),
        "date_start": frame["date"].iloc[0].date().isoformat(),
        "date_end": frame["date"].iloc[-1].date().isoformat(),
    }

    for window in (5, 20, 60):
        indicators[f"ma{window}"] = _round(close.rolling(window).mean().iloc[-1]) if len(close) >= window else None
        if indicators[f"ma{window}"]:
            indicators[f"price_vs_ma{window}_pct"] = _round((latest / indicators[f"ma{window}"] - 1) * 100, 2)

    indicators["rsi14"] = _round(_rsi(close, 14))
    macd_line, signal_line, histogram = _macd(close)
    indicators["macd"] = _round(macd_line)
    indicators["macd_signal"] = _round(signal_line)
    indicators["macd_histogram"] = _round(histogram)
    indicators["annualized_volatility_pct"] = _round(returns.std() * (252 ** 0.5) * 100, 2) if len(returns) > 2 else None

    for label, window in (("return_1m_pct", 21), ("return_3m_pct", 63), ("return_1y_pct", 252)):
        indicators[label] = _window_return(close, window)
    if indicators["return_1y_pct"] is None and len(close) >= 180:
        indicators["return_1y_pct"] = _window_return(close, len(close) - 1)

    indicators["trend_summary"] = trend_summary(indicators)
    return indicators


def trend_summary(indicators: dict[str, Any]) -> str:
    last = indicators.get("last_close")
    ma20 = indicators.get("ma20")
    ma60 = indicators.get("ma60")
    rsi = indicators.get("rsi14")
    macd_hist = indicators.get("macd_histogram")
    points: list[str] = []

    if last is not None and ma20 is not None and ma60 is not None:
        if last > ma20 > ma60:
            points.append("价格位于 MA20 和 MA60 上方，短中期趋势偏强")
        elif last < ma20 < ma60:
            points.append("价格位于 MA20 和 MA60 下方，短中期趋势偏弱")
        else:
            points.append("均线结构不一致，趋势信号偏混合")
    else:
        points.append("均线数据不足，趋势判断需要更多历史价格")

    if rsi is not None:
        if rsi >= 70:
            points.append("RSI 高于 70，短期可能偏热")
        elif rsi <= 30:
            points.append("RSI 低于 30，短期可能超卖")
        else:
            points.append("RSI 处于中性区间")

    if macd_hist is not None:
        points.append("MACD 柱为正，动量偏正" if macd_hist > 0 else "MACD 柱为负，动量偏弱")

    return "；".join(points)


def format_indicators(indicators: dict[str, Any]) -> str:
    if "error" in indicators:
        return indicators["error"]
    fields = [
        ("最近收盘价", "last_close"),
        ("MA5", "ma5"),
        ("MA20", "ma20"),
        ("MA60", "ma60"),
        ("RSI14", "rsi14"),
        ("MACD", "macd"),
        ("MACD Signal", "macd_signal"),
        ("MACD Histogram", "macd_histogram"),
        ("年化波动率", "annualized_volatility_pct"),
        ("近 1 月收益率", "return_1m_pct"),
        ("近 3 月收益率", "return_3m_pct"),
        ("近 1 年收益率", "return_1y_pct"),
    ]
    lines = []
    for label, key in fields:
        value = indicators.get(key)
        if value is None:
            continue
        suffix = "%" if key.endswith("_pct") or key == "annualized_volatility_pct" else ""
        lines.append(f"- {label}: {value}{suffix}")
    lines.append(f"- 趋势摘要: {indicators.get('trend_summary', '无')}")
    return "\n".join(lines)


def _rsi(close: pd.Series, period: int = 14) -> float | None:
    if len(close) <= period:
        return None
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))
    value = rsi.iloc[-1]
    return float(value) if pd.notna(value) else None


def _macd(close: pd.Series) -> tuple[float | None, float | None, float | None]:
    if len(close) < 35:
        return None, None, None
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    histogram = macd_line - signal_line
    return float(macd_line.iloc[-1]), float(signal_line.iloc[-1]), float(histogram.iloc[-1])


def _window_return(close: pd.Series, window: int) -> float | None:
    if len(close) <= window or window <= 0:
        return None
    start = close.iloc[-window - 1]
    end = close.iloc[-1]
    if not start:
        return None
    return _round((end / start - 1) * 100, 2)


def _round(value: Any, digits: int = 2) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None
