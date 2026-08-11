"""Selection, merge, and reconciliation rules for provider results."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import re
from typing import Any

from ticker_dossier.market_data.providers.normalization import (
    _news_keywords,
    _news_matches,
    _to_float,
)

from .models import Candle, Financials, NewsItem, Quote
from .symbols import CHINESE_SYMBOLS, normalize_symbol, to_yahoo_symbol
from .constants import (
    _FINANCIAL_FIELDS,
    _FINANCIAL_FIELD_LABELS,
    _FINANCIAL_MONETARY_FIELDS,
    _FINANCIAL_PERIOD_FIELDS,
    _QUOTE_FIELD_LABELS,
    _QUOTE_PRIMARY_FIELDS,
    _QUOTE_SUPPLEMENT_FIELDS,
)


def select_quote(successful: list[tuple[str, Quote]]) -> tuple[str, Quote, float | None]:
    """Choose the freshest quote and safely fill compatible missing metadata."""
    selected_name, selected = max(
        successful,
        key=lambda row: (row[1].is_realtime, row[1].as_of or ""),
    )
    selected = deepcopy(selected)
    selected.field_sources = {
        field_name: selected.field_sources.get(field_name, selected_name)
        for field_name in _QUOTE_PRIMARY_FIELDS
        if _quote_value_present(getattr(selected, field_name))
    }
    supplemental_fields: list[str] = []
    for provider_name, candidate in successful:
        if provider_name == selected_name:
            continue
        for field_name in _QUOTE_SUPPLEMENT_FIELDS:
            if _quote_value_present(getattr(selected, field_name)):
                continue
            value = getattr(candidate, field_name)
            if not _quote_value_present(value):
                continue
            if not _quote_field_compatible(selected, candidate, field_name):
                continue
            setattr(selected, field_name, value)
            selected.field_sources[field_name] = candidate.field_sources.get(
                field_name, provider_name
            )
            supplemental_fields.append(field_name)
    if supplemental_fields:
        detail = "、".join(
            f"{_QUOTE_FIELD_LABELS[field_name]}={selected.field_sources[field_name]}"
            for field_name in dict.fromkeys(supplemental_fields)
        )
        selected.notes.append(f"行情缺失字段由其他真实来源补充: {detail}。")
    spread = _quote_price_spread(successful)
    selected.source_spread_pct = spread
    return selected_name, selected, spread


def select_history(
    successful: list[tuple[str, list[Candle]]],
) -> tuple[str, list[Candle], float | None]:
    """Choose the newest, longest history and quantify overlapping disagreement."""
    selected_name, selected = max(
        successful,
        key=lambda row: (row[1][-1].date if row[1] else "", len(row[1])),
    )
    return selected_name, selected, _history_close_spread(successful)


def merge_financials(
    successful: list[tuple[str, Financials]],
) -> tuple[Financials, list[str], dict[str, float]]:
    """Merge compatible missing fields while retaining per-field provenance."""
    differences = _financial_field_differences(successful)
    merged = successful[0][1]
    primary_name = successful[0][0]
    merged.field_sources = {
        field_name: primary_name
        for field_name in _FINANCIAL_FIELDS
        if getattr(merged, field_name) is not None
    }
    merged.notes.append(_financial_basis_note(primary_name, merged))
    for provider_name, financials in successful[1:]:
        merged.notes.append(_financial_basis_note(provider_name, financials))
        for field_name in _FINANCIAL_FIELDS:
            candidate = getattr(financials, field_name)
            if getattr(merged, field_name) is not None or candidate is None:
                continue
            if not _financial_field_compatible(merged, financials, field_name):
                merged.notes.append(
                    f"未合并 {provider_name} 的 "
                    f"{_FINANCIAL_FIELD_LABELS.get(field_name, field_name)}："
                    "币种或报告期/口径与优先源不一致。"
                )
                continue
            setattr(merged, field_name, candidate)
            merged.field_sources[field_name] = provider_name
        _extend_unique(merged.notes, financials.notes)
    source_names = list(dict.fromkeys(name for name, _ in successful))
    merged.source = " / ".join(source_names)
    return merged, source_names, differences


def _quote_value_present(value: Any) -> bool:
    return value is not None and value != ""


def _quote_field_compatible(primary: Quote, candidate: Quote, field_name: str) -> bool:
    if normalize_symbol(primary.symbol) != normalize_symbol(candidate.symbol):
        return False
    if (
        field_name in {"market_cap", "eps"}
        and primary.currency
        and candidate.currency
        and primary.currency != candidate.currency
    ):
        return False
    return True


def enrich_financial_pe(financials: Financials, quote: Quote) -> bool:
    """Fill a missing PE from a reported quote value or a strictly guarded derivation."""
    if financials.pe_ratio is not None:
        return False
    if normalize_symbol(financials.symbol) != normalize_symbol(quote.symbol):
        return False
    if quote.pe_ratio is not None and float(quote.pe_ratio) > 0:
        financials.pe_ratio = float(quote.pe_ratio)
        financials.field_sources["pe_ratio"] = (
            quote.field_sources.get("pe_ratio") or quote.source or "UNKNOWN_QUOTE_SOURCE"
        )
        return True

    price = _to_float(quote.price)
    eps = _to_float(financials.eps)
    if price is None or price <= 0 or eps is None or eps <= 0:
        return False
    if _unsafe_derivation_source(quote.source) or _unsafe_derivation_source(financials.source):
        return False
    quote_currency = _canonical_currency(quote.currency)
    financial_currency = _canonical_currency(financials.currency)
    if not quote_currency or quote_currency != financial_currency:
        return False
    period_type = str(financials.period_type or "").strip().upper()
    if period_type not in {"TTM", "ANNUAL"}:
        return False
    age_days = _financial_age_days(financials.as_of)
    if age_days is None or not 0 <= age_days <= 550:
        return False

    estimate = price / eps
    price_source = quote.field_sources.get("price") or quote.source or "UNKNOWN_QUOTE_SOURCE"
    eps_source = (
        financials.field_sources.get("eps") or financials.source or "UNKNOWN_FINANCIAL_SOURCE"
    )
    financials.pe_ratio = round(estimate, 4)
    financials.field_sources["pe_ratio"] = (
        f"DERIVED: {price_source} price / {eps_source} {period_type} EPS"
    )
    financials.notes.append(
        f"估算 PE {estimate:.2f} = 当前价格 {price:g}（{price_source}，{quote.as_of or '未知时点'}）"
        f" ÷ {period_type} EPS {eps:g}（{eps_source}，报告期 {financials.as_of}）；"
        "程序推导值，不是数据商直接报告的 PE。"
    )
    return True


def _unsafe_derivation_source(source: str) -> bool:
    upper = str(source or "").upper()
    return any(token in upper for token in ("SAMPLE_FALLBACK", "UNAVAILABLE", "SKIPPED"))


def _canonical_currency(value: str) -> str:
    compact = str(value or "").strip().upper().replace(" ", "")
    aliases = {
        "USD": "USD",
        "US$": "USD",
        "美元": "USD",
        "CNY": "CNY",
        "RMB": "CNY",
        "人民币": "CNY",
        "人民币元": "CNY",
        "HKD": "HKD",
        "港元": "HKD",
        "港币": "HKD",
    }
    return aliases.get(compact, compact if len(compact) == 3 and compact.isalpha() else "")


def _financial_age_days(value: str) -> int | None:
    try:
        report_date = datetime.fromisoformat(str(value).strip()[:10]).date()
    except (TypeError, ValueError):
        return None
    return (datetime.now(UTC).date() - report_date).days


def _financial_field_differences(
    financials: list[tuple[str, Financials]],
) -> dict[str, float]:
    differences: dict[str, float] = {}
    primary = financials[0][1] if financials else None
    for field_name in _FINANCIAL_FIELDS:
        values: list[float] = []
        for _, row in financials:
            if primary is not None and not _financial_field_compatible(primary, row, field_name):
                continue
            value = _to_float(getattr(row, field_name))
            if value is not None:
                values.append(value)
        if len(values) < 2:
            continue
        denominator = max(abs(value) for value in values)
        differences[field_name] = (
            0.0 if denominator == 0 else (max(values) - min(values)) / denominator * 100
        )
    return differences


def _financial_field_compatible(
    primary: Financials,
    candidate: Financials,
    field_name: str,
) -> bool:
    if (
        field_name in _FINANCIAL_MONETARY_FIELDS
        and primary.currency
        and candidate.currency
        and primary.currency != candidate.currency
    ):
        return False
    if field_name in _FINANCIAL_PERIOD_FIELDS:
        if primary.as_of and candidate.as_of and primary.as_of != candidate.as_of:
            return False
        if (
            primary.period_type
            and candidate.period_type
            and primary.period_type != candidate.period_type
        ):
            return False
    return True


def _financial_basis_note(provider_name: str, financials: Financials) -> str:
    return (
        f"{provider_name} 财务口径: report_period={financials.as_of or '未知'}, "
        f"period_type={financials.period_type or '未知'}, currency={financials.currency or '未知'}, "
        f"fetched_at={financials.fetched_at or '未知'}。"
    )


def _history_close_spread(histories: list[tuple[str, list[Candle]]]) -> float | None:
    by_source = [
        {candle.date: float(candle.close) for candle in candles if candle.close is not None}
        for _, candles in histories
    ]
    if len(by_source) < 2:
        return None
    common_dates = set(by_source[0])
    for rows in by_source[1:]:
        common_dates.intersection_update(rows)
    if not common_dates:
        return None
    spreads: list[float] = []
    for date in common_dates:
        values = [rows[date] for rows in by_source]
        low = min(values)
        if low > 0:
            spreads.append((max(values) - low) / low * 100)
    return max(spreads) if spreads else None


def _quote_price_spread(quotes: list[tuple[str, Quote]]) -> float | None:
    prices = [
        float(quote.price)
        for _, quote in quotes
        if quote.price is not None and quote.price > 0
    ]
    if len(prices) < 2:
        return None
    low = min(prices)
    return (max(prices) - low) / low * 100


def _news_keywords_for_symbol(symbol: str) -> list[str]:
    normalized = normalize_symbol(symbol)
    keywords: list[str] = _news_keywords(normalized, to_yahoo_symbol(normalized))
    for alias, target in CHINESE_SYMBOLS.items():
        if normalize_symbol(target) == normalized and alias.lower() not in keywords:
            keywords.append(alias.lower())
    return keywords


def _news_item_matches(item: NewsItem, keywords: list[str]) -> bool:
    return bool(
        _news_matches(
            {
                "title": item.title,
                "summary": item.summary,
                "link": item.link,
                "publisher": item.publisher,
            },
            keywords,
        )
    )


def _dedupe_news(items: list[NewsItem], limit: int) -> list[NewsItem]:
    if limit <= 0:
        return []
    candidates: list[NewsItem] = []
    seen_titles: set[str] = set()
    seen_links: set[str] = set()
    for item in sorted(items, key=_news_timestamp, reverse=True):
        title_key = re.sub(r"[\W_]+", "", item.title.lower())
        link_key = item.link.split("?", 1)[0].rstrip("/").lower()
        if not title_key and not link_key:
            continue
        if title_key and title_key in seen_titles:
            continue
        if link_key and link_key in seen_links:
            continue
        if title_key:
            seen_titles.add(title_key)
        if link_key:
            seen_links.add(link_key)
        candidates.append(item)

    by_source: dict[str, list[NewsItem]] = {}
    for item in candidates:
        by_source.setdefault(item.source or item.publisher or "unknown", []).append(item)
    rows: list[NewsItem] = []
    while len(rows) < limit and any(by_source.values()):
        for source_items in by_source.values():
            if source_items and len(rows) < limit:
                rows.append(source_items.pop(0))
    return rows


def _news_timestamp(item: NewsItem) -> float:
    value = str(item.published_at or "").strip()
    if not value:
        return 0.0
    for candidate in (value, value.replace("Z", "+00:00")):
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.timestamp()
        except ValueError:
            pass
    for pattern in ("%Y%m%dT%H%M%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, pattern).replace(tzinfo=UTC).timestamp()
        except ValueError:
            continue
    return 0.0


def _extend_unique(target: list[str], values: list[str]) -> None:
    for value in values:
        if value not in target:
            target.append(value)
