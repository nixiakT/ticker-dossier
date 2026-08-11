"""Simple strategy backtesting utilities."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import pandas as pd

from .indicators import candles_to_frame
from .models import Candle


@dataclass
class StrategyConfig:
    strategy_type: str = "moving_average_cross"
    fast_window: int = 20
    slow_window: int = 60
    exit_window: int | None = 20
    initial_cash: float = 100_000.0
    notes: list[str] | None = None


def parse_strategy(text: str | None = None, **overrides: Any) -> StrategyConfig:
    config = StrategyConfig()
    if text:
        numbers = [int(x) for x in re.findall(r"\d+", text)]
        if len(numbers) >= 2:
            if numbers[0] > numbers[1]:
                config.notes = config.notes or []
                config.notes.append(f"输入窗口 MA{numbers[0]}/MA{numbers[1]} 已规范化为快线 MA{numbers[1]}、慢线 MA{numbers[0]}。")
            config.fast_window = min(numbers[0], numbers[1])
            config.slow_window = max(numbers[0], numbers[1])
        elif len(numbers) == 1:
            config.fast_window = numbers[0]
            config.slow_window = max(numbers[0] * 3, numbers[0] + 20)
        if "跌破" in text or "below" in text.lower():
            config.exit_window = config.fast_window
    for key, value in overrides.items():
        if value is not None and hasattr(config, key):
            setattr(config, key, value)
    if config.fast_window >= config.slow_window:
        original_fast, original_slow = config.fast_window, config.slow_window
        config.fast_window = min(original_fast, original_slow)
        config.slow_window = max(original_fast, original_slow)
        if config.fast_window == config.slow_window:
            config.slow_window = config.fast_window + 1
        config.notes = config.notes or []
        config.notes.append(f"输入窗口 MA{original_fast}/MA{original_slow} 已规范化为快线 MA{config.fast_window}、慢线 MA{config.slow_window}。")
    if config.notes:
        config.notes = list(dict.fromkeys(config.notes))
    return config


def backtest_moving_average_cross(candles: list[Candle], config: StrategyConfig) -> dict[str, Any]:
    frame = candles_to_frame(candles)
    if frame.empty or len(frame) <= config.slow_window + 5:
        return {"error": "历史数据不足，无法回测该均线策略"}

    frame = frame.copy()
    frame["fast_ma"] = frame["close"].rolling(config.fast_window).mean()
    frame["slow_ma"] = frame["close"].rolling(config.slow_window).mean()
    frame["signal"] = 0
    frame.loc[frame["fast_ma"] > frame["slow_ma"], "signal"] = 1
    if config.exit_window:
        frame["exit_ma"] = frame["close"].rolling(config.exit_window).mean()
        frame.loc[frame["close"] < frame["exit_ma"], "signal"] = 0
    frame["position"] = frame["signal"].shift(1).fillna(0)
    frame["asset_return"] = frame["close"].pct_change().fillna(0)
    frame["strategy_return"] = frame["position"] * frame["asset_return"]
    frame["equity"] = config.initial_cash * (1 + frame["strategy_return"]).cumprod()
    frame["buy_hold_equity"] = config.initial_cash * (1 + frame["asset_return"]).cumprod()

    trades = _trades(frame)
    total_return = frame["equity"].iloc[-1] / config.initial_cash - 1
    buy_hold_return = frame["buy_hold_equity"].iloc[-1] / config.initial_cash - 1
    max_drawdown = _max_drawdown(frame["equity"])
    sharpe = _sharpe(frame["strategy_return"])
    win_rate = _win_rate(trades)

    return {
        "strategy": "moving_average_cross",
        "fast_window": config.fast_window,
        "slow_window": config.slow_window,
        "exit_window": config.exit_window,
        "date_start": frame["date"].iloc[0].date().isoformat(),
        "date_end": frame["date"].iloc[-1].date().isoformat(),
        "initial_cash": config.initial_cash,
        "final_equity": round(float(frame["equity"].iloc[-1]), 2),
        "total_return_pct": round(float(total_return * 100), 2),
        "buy_hold_return_pct": round(float(buy_hold_return * 100), 2),
        "max_drawdown_pct": round(float(max_drawdown * 100), 2),
        "sharpe": round(float(sharpe), 2) if sharpe is not None else None,
        "trades": len(trades),
        "win_rate_pct": round(float(win_rate * 100), 2) if win_rate is not None else None,
        "latest_position": "持有" if frame["position"].iloc[-1] > 0 else "空仓",
        "trade_log": trades[-10:],
        "assumptions": [
            *(config.notes or []),
            "使用收盘价信号，下一交易日持仓生效。",
            "未计入交易手续费、滑点、税费、分红和融资成本。",
            "结果仅用于研究，不代表未来收益。",
        ],
    }


def format_backtest(result: dict[str, Any]) -> str:
    if "error" in result:
        return result["error"]
    lines = [
        "# 策略回测结果",
        "",
        f"- 策略: {result['strategy']}",
        f"- 参数: 快线 MA{result['fast_window']}，慢线 MA{result['slow_window']}，退出均线 MA{result['exit_window']}",
        f"- 区间: {result['date_start']} 到 {result['date_end']}",
        f"- 初始资金: {result['initial_cash']:,.2f}",
        f"- 期末权益: {result['final_equity']:,.2f}",
        f"- 策略收益率: {result['total_return_pct']}%",
        f"- 买入持有收益率: {result['buy_hold_return_pct']}%",
        f"- 最大回撤: {result['max_drawdown_pct']}%",
        f"- Sharpe: {result['sharpe']}",
        f"- 交易次数: {result['trades']}",
        f"- 胜率: {result['win_rate_pct']}%",
        f"- 最新状态: {result['latest_position']}",
        "",
        "## 最近交易",
    ]
    if result["trade_log"]:
        for trade in result["trade_log"]:
            lines.append(f"- {trade['date']} {trade['action']} @ {trade['price']}")
    else:
        lines.append("- 无交易。")
    lines.append("")
    lines.append("## 假设与限制")
    for assumption in result["assumptions"]:
        lines.append(f"- {assumption}")
    return "\n".join(lines)


def _trades(frame: pd.DataFrame) -> list[dict[str, Any]]:
    position_change = frame["position"].diff().fillna(frame["position"])
    trades = []
    for _, row in frame.loc[position_change != 0].iterrows():
        action = "BUY" if row["position"] > 0 else "SELL"
        trades.append({"date": row["date"].date().isoformat(), "action": action, "price": round(float(row["close"]), 2)})
    return trades


def _max_drawdown(equity: pd.Series) -> float:
    running_max = equity.cummax()
    drawdown = equity / running_max - 1
    return abs(float(drawdown.min()))


def _sharpe(returns: pd.Series) -> float | None:
    std = returns.std()
    if std == 0 or pd.isna(std):
        return None
    return float(returns.mean() / std * (252 ** 0.5))


def _win_rate(trades: list[dict[str, Any]]) -> float | None:
    completed = []
    current_buy: dict[str, Any] | None = None
    for trade in trades:
        if trade["action"] == "BUY":
            current_buy = trade
        elif trade["action"] == "SELL" and current_buy:
            completed.append(trade["price"] > current_buy["price"])
            current_buy = None
    if not completed:
        return None
    return sum(completed) / len(completed)
