"""Serialize market-data values."""

from __future__ import annotations

import csv
from io import StringIO

from .models import Candle


def export_history_csv(candles: list[Candle]) -> str:
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["date", "open", "high", "low", "close", "volume"])
    for candle in candles:
        writer.writerow(
            [candle.date, candle.open, candle.high, candle.low, candle.close, candle.volume]
        )
    return buffer.getvalue()
