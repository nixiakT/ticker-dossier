"""Stable compatibility facade for market-data research APIs.

Provider adapters are implemented under :mod:`ticker_dossier.integrations.market_data`;
provider-chain orchestration lives under :mod:`ticker_dossier.research.market_data`.
Imports from this historical module remain supported by identity.
"""

from __future__ import annotations

from ticker_dossier.config import load_local_env
from ticker_dossier.integrations.market_data import (
    AKShareProvider,
    AlphaVantageProvider,
    MarketDataProvider,
    ProviderError,
    ProviderTimeoutError,
    SampleDataProvider,
    TushareProvider,
    YahooFinanceProvider,
)
from ticker_dossier.integrations.market_data._normalization import (
    _compact_provider_error,
    _date_window,
    _debt_to_equity_from_debt_to_assets,
    _field_value,
    _financials_have_data,
    _format_trade_date,
    _latest_report_date,
    _list_float,
    _list_int,
    _lots_to_shares,
    _news_keywords,
    _news_matches,
    _percent_to_float,
    _period_to_days,
    _raw,
    _text_value,
    _to_float,
    _to_int,
    _trim_period,
    _unix_date,
)
from ticker_dossier.integrations.market_data.providers.sample import (
    _SAMPLE_PROFILES,
    _generic_profile,
    _sample_profile,
)

from .market_data.chain import ProviderChain
from .market_data.configuration import (
    _env_truthy,
    _nonnegative_env_float,
    _positive_env_float,
)
from .market_data.constants import (
    _COVERAGE_LABELS,
    _FINANCIAL_FIELDS,
    _FINANCIAL_FIELD_LABELS,
    _FINANCIAL_FLOW_FIELDS,
    _FINANCIAL_MONETARY_FIELDS,
    _FINANCIAL_PERIOD_FIELDS,
    _QUOTE_FIELD_LABELS,
    _QUOTE_PRIMARY_FIELDS,
    _QUOTE_SUPPLEMENT_FIELDS,
)
from .market_data.coverage import _coverage_error
from .market_data.execution import (
    _collect_provider_calls,
    _is_sample_provider,
    _provider_supports,
)
from .market_data.selection import (
    _canonical_currency,
    _dedupe_news,
    _extend_unique,
    _financial_age_days,
    _financial_basis_note,
    _financial_field_compatible,
    _financial_field_differences,
    _history_close_spread,
    _news_item_matches,
    _news_keywords_for_symbol,
    _news_timestamp,
    _quote_field_compatible,
    _quote_price_spread,
    _quote_value_present,
    _unsafe_derivation_source,
    enrich_financial_pe,
)
from .market_data.serialization import export_history_csv
from .models import Candle, Financials, NewsItem, Quote
from .symbols import CHINESE_SYMBOLS, normalize_symbol, to_yahoo_symbol
