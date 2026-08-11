"""Environment parsing and provider-set construction for market-data research."""

from __future__ import annotations

import math
import os
from importlib.util import find_spec

from ticker_dossier.integrations.market_data import (
    AKShareProvider,
    AlphaVantageProvider,
    MarketDataProvider,
    SampleDataProvider,
    TushareProvider,
    YahooFinanceProvider,
)


def build_default_providers(
    sample_fallback: bool,
) -> tuple[
    list[MarketDataProvider],
    list[dict[str, str]],
    list[MarketDataProvider],
]:
    """Build adapters and the matching diagnostics from one configuration snapshot."""
    alpha = AlphaVantageProvider()
    tushare = TushareProvider()
    akshare_available = find_spec("akshare") is not None
    diagnostics = [
        {
            "name": alpha.name,
            "status": "enabled" if alpha.available() else "disabled",
            "detail": "" if alpha.available() else "requires ALPHAVANTAGE_API_KEY",
        },
        {
            "name": tushare.name,
            "status": "enabled" if tushare.available() else "disabled",
            "detail": "" if tushare.available() else "requires TUSHARE_TOKEN",
        },
        {
            "name": "AKShare",
            "status": "enabled" if akshare_available else "disabled",
            "detail": (
                "A/HK/US public financial indicators; A-share quote/history/news"
                if akshare_available
                else "install ticker-dossier[providers] to enable"
            ),
        },
        {
            "name": "Yahoo Finance public endpoints",
            "status": "enabled",
            "detail": "public endpoints may be delayed",
        },
        {
            "name": "SAMPLE_FALLBACK",
            "status": "enabled" if sample_fallback else "disabled",
            "detail": "demo-only fallback" if sample_fallback else "FINANCE_ALLOW_SAMPLE_FALLBACK=0",
        },
    ]
    providers: list[MarketDataProvider] = []
    if alpha.available():
        providers.append(alpha)
    if tushare.available():
        providers.append(tushare)
    if akshare_available:
        providers.append(AKShareProvider())
    providers.append(YahooFinanceProvider())
    if sample_fallback:
        providers.append(SampleDataProvider())
    owned_providers: list[MarketDataProvider] = [
        alpha,
        tushare,
    ]
    for provider in providers:
        if not any(existing is provider for existing in owned_providers):
            owned_providers.append(provider)
    return providers, diagnostics, owned_providers


def provider_diagnostics(
    providers: list[MarketDataProvider],
    allow_sample_fallback: bool,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for provider in providers:
        status = "enabled"
        detail = ""
        if isinstance(provider, SampleDataProvider):
            status = "enabled" if allow_sample_fallback else "disabled"
            detail = "demo-only fallback"
        rows.append({"name": provider.name, "status": status, "detail": detail})
    return rows


def _env_truthy(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off", ""}


def _positive_env_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) and value > 0 else default


def _nonnegative_env_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) and value >= 0 else default
