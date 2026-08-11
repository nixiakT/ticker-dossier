"""Parsing and normalization helpers shared by concrete market-data adapters."""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from ticker_dossier.research.models import Candle, Financials
from ticker_dossier.security import redact_sensitive_text


_FINANCIAL_FIELDS = (
    "market_cap",
    "pe_ratio",
    "forward_pe",
    "eps",
    "revenue",
    "gross_profit",
    "net_income",
    "free_cash_flow",
    "debt_to_equity",
    "return_on_equity",
    "profit_margin",
)


def _financials_have_data(financials: Financials) -> bool:
    return any(getattr(financials, field_name) is not None for field_name in _FINANCIAL_FIELDS)


def _field_value(row: dict[str, Any], candidates: str | tuple[str, ...]) -> Any:
    names = (candidates,) if isinstance(candidates, str) else candidates
    for name in names:
        if name and row.get(name) not in (None, "", "-"):
            return row.get(name)
    return None


def _debt_to_equity_from_debt_to_assets(value: Any) -> float | None:
    """Convert debt/assets percent to downstream debt/equity percent."""
    debt_to_assets = _to_float(value)
    if debt_to_assets is None or debt_to_assets < 0:
        return None
    if debt_to_assets >= 100:
        return 1_000_000.0  # zero/negative equity; finite sentinel reliably trips risk gates
    return debt_to_assets / (100 - debt_to_assets) * 100


def _lots_to_shares(value: Any) -> int | None:
    lots = _to_int(value)
    return lots * 100 if lots is not None else None


def _compact_provider_error(exc: Exception, limit: int = 180) -> str:
    text = " ".join(redact_sensitive_text(str(exc)).split()) or exc.__class__.__name__
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _raw(value: Any) -> float | None:
    if isinstance(value, dict):
        value = value.get("raw")
    return _to_float(value)


def _text_value(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("raw") or value.get("fmt")
    return str(value or "")


def _unix_date(value: float | None) -> str:
    if value is None:
        return ""
    try:
        timestamp = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(timestamp, UTC).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return ""


def _latest_report_date(*rows: dict[str, Any]) -> str:
    values: list[str] = []
    for row in rows:
        for key in ("end_date", "report_date", "ann_date"):
            raw = str(row.get(key) or "").strip()
            if raw:
                values.append(_format_trade_date(raw[:10].replace("-", "")))
                break
    return max(values) if values else ""


def _to_float(value: Any) -> float | None:
    if value in (None, "", "None", "N/A", "-"):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    if value in (None, "", "None", "N/A", "-"):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _percent_to_float(value: Any) -> float | None:
    if isinstance(value, str):
        value = value.replace("%", "")
    return _to_float(value)


def _list_float(values: list[Any] | None, index: int) -> float | None:
    if not values or index >= len(values):
        return None
    return _to_float(values[index])


def _list_int(values: list[Any] | None, index: int) -> int | None:
    if not values or index >= len(values):
        return None
    return _to_int(values[index])


def _news_keywords(normalized: str, query_symbol: str, quote_name: str = "") -> list[str]:
    raw = [
        normalized,
        query_symbol,
        normalized.split(".", 1)[0],
        query_symbol.split(".", 1)[0],
        quote_name,
    ]
    if normalized.endswith(".HK"):
        raw.append(normalized[:-3].lstrip("0") or normalized[:-3])
    generic_words = {
        "co", "company", "corp", "corporation", "group", "holding", "holdings",
        "inc", "limited", "ltd", "plc", "tech", "technologies", "technology",
    }
    for part in quote_name.replace(",", " ").replace(".", " ").split():
        if len(part) >= 4 and part.lower() not in generic_words:
            raw.append(part)
    keywords: list[str] = []
    for value in raw:
        cleaned = str(value).strip().lower()
        if cleaned and cleaned not in keywords:
            keywords.append(cleaned)
    return keywords


def _news_matches(row: dict[str, Any], keywords: list[str]) -> bool:
    # Publisher names and URL paths are weak metadata: neither may establish
    # article relevance on its own (e.g. ticker FOX vs publisher Fox Business).
    haystack = " ".join(str(row.get(key, "")) for key in ("title", "summary")).lower()
    for keyword in keywords:
        if re.fullmatch(r"[a-z0-9]+", keyword):
            if re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", haystack):
                return True
        elif keyword in haystack:
            return True
    return False


def _period_to_days(period: str) -> int:
    normalized = period.lower().strip()
    table = {"1mo": 22, "3mo": 66, "6mo": 126, "1y": 252, "2y": 504, "5y": 1260}
    if normalized in table:
        return table[normalized]
    if normalized.endswith("d") and normalized[:-1].isdigit():
        return max(int(normalized[:-1]), 5)
    return 252


def _trim_period(candles: list[Candle], period: str) -> list[Candle]:
    days = _period_to_days(period)
    return candles[-days:]


def _date_window(period: str) -> tuple[str, str]:
    end = datetime.now(UTC).date()
    start = end - timedelta(days=_period_to_days(period) * 7 // 5 + 10)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def _format_trade_date(value: str) -> str:
    text = value.strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text
