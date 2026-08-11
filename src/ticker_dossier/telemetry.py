"""Token usage accounting shared by backends, the agent loop and CLI trace."""
from __future__ import annotations

import os
from typing import Any


USAGE_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens", "cache_hit_tokens")


def normalize_usage(raw: Any) -> dict[str, int | float]:
    """Normalize OpenAI-compatible usage payloads without trusting extra fields."""
    if not isinstance(raw, dict):
        return {}
    usage: dict[str, int | float] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = raw.get(key)
        if isinstance(value, (int, float)) and value >= 0:
            usage[key] = int(value)
    details = raw.get("prompt_tokens_details")
    if isinstance(details, dict):
        cached = details.get("cached_tokens", details.get("cache_hit_tokens"))
        if isinstance(cached, (int, float)) and cached >= 0:
            usage["cache_hit_tokens"] = int(cached)
    if "total_tokens" not in usage:
        usage["total_tokens"] = int(usage.get("prompt_tokens", 0)) + int(usage.get("completion_tokens", 0))
    cost = estimate_cost_usd(usage)
    if cost is not None:
        usage["estimated_cost_usd"] = cost
    return usage


def add_usage(total: dict[str, int | float], item: Any) -> dict[str, int | float]:
    """Accumulate one model call into a mutable task-level ledger."""
    if not isinstance(item, dict):
        return total
    for key in USAGE_KEYS:
        value = item.get(key)
        if isinstance(value, (int, float)) and value >= 0:
            total[key] = int(total.get(key, 0)) + int(value)
    cost = item.get("estimated_cost_usd")
    if isinstance(cost, (int, float)) and cost >= 0:
        total["estimated_cost_usd"] = float(total.get("estimated_cost_usd", 0.0)) + float(cost)
    return total


def estimate_cost_usd(usage: dict[str, int | float]) -> float | None:
    """Estimate cost only when explicit per-million-token prices are configured."""
    input_rate = _optional_float("DEEPSEEK_INPUT_PRICE_PER_MILLION")
    output_rate = _optional_float("DEEPSEEK_OUTPUT_PRICE_PER_MILLION")
    if input_rate is None or output_rate is None:
        return None
    prompt = float(usage.get("prompt_tokens", 0))
    completion = float(usage.get("completion_tokens", 0))
    return (prompt * input_rate + completion * output_rate) / 1_000_000


def format_usage(usage: Any) -> str:
    if not isinstance(usage, dict) or not usage:
        return "tokens unavailable"
    prompt = int(usage.get("prompt_tokens", 0))
    completion = int(usage.get("completion_tokens", 0))
    total = int(usage.get("total_tokens", prompt + completion))
    text = f"tokens {total:,} (in {prompt:,} / out {completion:,})"
    cached = int(usage.get("cache_hit_tokens", 0))
    if cached:
        text += f" · cached {cached:,}"
    cost = usage.get("estimated_cost_usd")
    if isinstance(cost, (int, float)):
        text += f" · est ${float(cost):.6f}"
    return text


def _optional_float(name: str) -> float | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value >= 0 else None
