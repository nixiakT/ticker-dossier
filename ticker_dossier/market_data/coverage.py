"""Source-coverage records and provider diagnostics."""

from __future__ import annotations

from typing import Any

from .constants import _COVERAGE_LABELS, _FINANCIAL_FIELD_LABELS


def _empty_coverage(method: str) -> dict[str, Any]:
    return {
        "method": method,
        "successful_real_sources": [],
        "failed_real_sources": [],
        "selected_source": "",
        "sample_used": False,
        "price_spread_pct": None,
        "field_differences_pct": {},
        "history_close_spread_pct": None,
        "field_sources": {},
        "cache_hit": False,
        "cache_age_seconds": None,
    }


def record_coverage(
    coverage_by_method: dict[str, dict[str, Any]],
    method: str,
    *,
    successful_real_sources: list[str],
    failed_real_sources: list[dict[str, str]],
    selected_source: str,
    sample_used: bool,
    price_spread_pct: float | None = None,
    field_differences_pct: dict[str, float] | None = None,
    history_close_spread_pct: float | None = None,
    field_sources: dict[str, str] | None = None,
) -> None:
    """Replace one operation's coverage with detached caller-owned values."""
    coverage_by_method[method] = {
        "method": method,
        "successful_real_sources": list(dict.fromkeys(successful_real_sources)),
        "failed_real_sources": [dict(item) for item in failed_real_sources],
        "selected_source": selected_source,
        "sample_used": sample_used,
        "price_spread_pct": price_spread_pct,
        "field_differences_pct": dict(field_differences_pct or {}),
        "history_close_spread_pct": history_close_spread_pct,
        "field_sources": dict(field_sources or {}),
        "cache_hit": False,
        "cache_age_seconds": None,
    }


def source_coverage(
    coverage_by_method: dict[str, dict[str, Any]],
    method: str,
) -> dict[str, Any]:
    """Return a defensive copy of one operation's diagnostic record."""
    row = coverage_by_method.get(method) or _empty_coverage(method)
    return {
        **row,
        "successful_real_sources": list(row["successful_real_sources"]),
        "failed_real_sources": [dict(item) for item in row["failed_real_sources"]],
        "field_differences_pct": dict(row["field_differences_pct"]),
        "field_sources": dict(row["field_sources"]),
    }


def report_notes(
    coverage_by_method: dict[str, dict[str, Any]],
    method: str | None = None,
) -> list[str]:
    """Render coverage metadata without depending on the ProviderChain object."""
    methods = (
        [method]
        if method
        else [name for name in _COVERAGE_LABELS if name in coverage_by_method]
    )
    notes: list[str] = []
    for method_name in methods:
        coverage = coverage_by_method.get(method_name)
        if not coverage:
            continue
        label = _COVERAGE_LABELS.get(method_name, method_name)
        sources = coverage["successful_real_sources"]
        if sources:
            notes.append(f"{label}真实来源覆盖: {'、'.join(sources)}。")
        if coverage.get("cache_hit"):
            notes.append(
                f"{label}使用 TTL 缓存，缓存年龄 "
                f"{coverage.get('cache_age_seconds', 0):.1f} 秒。"
            )
        if coverage["sample_used"]:
            notes.append(f"{label}使用 SAMPLE_FALLBACK；样例数据不属于真实来源，也不构成交叉验证。")
        failures = coverage["failed_real_sources"]
        if failures:
            detail = "；".join(f"{item['name']}: {item['error']}" for item in failures)
            notes.append(f"{label}来源失败: {detail}。")
        spread = coverage.get("price_spread_pct")
        if method_name == "get_quote" and spread is not None:
            notes.append(f"跨源行情最大差异: {spread:.2f}%。")
        history_spread = coverage.get("history_close_spread_pct")
        if method_name == "get_history" and history_spread is not None:
            notes.append(f"历史行情重叠窗口收盘价最大差异: {history_spread:.2f}%。")
        differences = coverage.get("field_differences_pct") or {}
        material_differences = [
            (name, value) for name, value in differences.items() if value >= 0.01
        ]
        if method_name == "get_financials" and material_differences:
            detail = "、".join(
                f"{_FINANCIAL_FIELD_LABELS.get(name, name)} {value:.2f}%"
                for name, value in material_differences
            )
            notes.append(f"基本面跨源字段差异: {detail}。")
    return notes


def _coverage_error(
    failures: list[dict[str, str]],
    sample_failures: list[str],
    method: str,
) -> str:
    errors = [f"{item['name']}: {item['error']}" for item in failures]
    errors.extend(sample_failures)
    return "; ".join(errors) or f"no provider supports {method}"
