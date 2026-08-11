"""Field definitions for provider selection and diagnostics."""

from __future__ import annotations


_COVERAGE_LABELS = {
    "get_quote": "行情",
    "get_history": "历史价格",
    "get_financials": "基本面",
    "get_news": "新闻",
}

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

_FINANCIAL_MONETARY_FIELDS = {
    "market_cap",
    "eps",
    "revenue",
    "gross_profit",
    "net_income",
    "free_cash_flow",
}
_FINANCIAL_FLOW_FIELDS = {"eps", "revenue", "gross_profit", "net_income", "free_cash_flow"}
_FINANCIAL_PERIOD_FIELDS = _FINANCIAL_FLOW_FIELDS | {
    "debt_to_equity",
    "return_on_equity",
    "profit_margin",
}

_FINANCIAL_FIELD_LABELS = {
    "market_cap": "市值",
    "pe_ratio": "PE",
    "forward_pe": "Forward PE",
    "eps": "EPS",
    "revenue": "营收",
    "gross_profit": "毛利",
    "net_income": "净利润",
    "free_cash_flow": "自由现金流",
    "debt_to_equity": "杠杆",
    "return_on_equity": "ROE",
    "profit_margin": "利润率",
}

_QUOTE_PRIMARY_FIELDS = (
    "name",
    "currency",
    "price",
    "previous_close",
    "change",
    "change_percent",
    "volume",
    "market_cap",
    "pe_ratio",
    "eps",
)
_QUOTE_SUPPLEMENT_FIELDS = ("name", "currency", "market_cap", "pe_ratio", "eps")
_QUOTE_FIELD_LABELS = {
    "name": "名称",
    "currency": "币种",
    "market_cap": "市值",
    "pe_ratio": "PE",
    "eps": "EPS",
}
