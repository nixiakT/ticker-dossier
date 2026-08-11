"""Tushare Pro market-data adapter."""

from __future__ import annotations

import os
from typing import Any

from ticker_dossier.research.models import Candle, Financials, NewsItem, Quote, utc_now_iso
from ticker_dossier.research.symbols import is_a_share, normalize_symbol, to_tushare_symbol

from .._normalization import (
    _compact_provider_error,
    _date_window,
    _debt_to_equity_from_debt_to_assets,
    _format_trade_date,
    _lots_to_shares,
    _to_float,
)
from ..base import ProviderError


class TushareProvider:
    name = "Tushare Pro"

    def __init__(self, token: str | None = None, timeout: float = 20.0):
        self.token = token or os.environ.get("TUSHARE_TOKEN", "")
        self.timeout = timeout
        self._pro = None

    def available(self) -> bool:
        return bool(self.token)

    def supports(self, method: str, symbol: str, *args: Any) -> bool:
        return method != "get_news" and is_a_share(symbol)

    def _client(self) -> Any:
        if not self.token:
            raise ProviderError("missing TUSHARE_TOKEN")
        if self._pro is None:
            try:
                import tushare as ts  # type: ignore
            except ImportError as exc:
                raise ProviderError("tushare package is not installed") from exc
            self._pro = ts.pro_api(self.token, timeout=self.timeout)
        return self._pro

    def _require_a_share(self, symbol: str) -> tuple[str, str]:
        normalized = normalize_symbol(symbol)
        if not is_a_share(normalized):
            raise ProviderError("TushareProvider supports A-share symbols only")
        return normalized, to_tushare_symbol(normalized)

    def get_quote(self, symbol: str) -> Quote:
        normalized, ts_code = self._require_a_share(symbol)
        pro = self._client()
        start_date, end_date = _date_window("1mo")
        daily = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if daily is None or daily.empty:
            raise ProviderError("empty Tushare daily quote")
        daily = daily.sort_values("trade_date")
        latest = daily.iloc[-1]
        previous = daily.iloc[-2] if len(daily) > 1 else None
        basics, basics_error = self._daily_basic(pro, ts_code)
        name = self._stock_name(pro, ts_code)
        price = _to_float(latest.get("close"))
        previous_close = _to_float(previous.get("close")) if previous is not None else _to_float(latest.get("pre_close"))
        change = price - previous_close if price is not None and previous_close not in (None, 0) else _to_float(latest.get("change"))
        change_percent = (
            change / previous_close * 100
            if change is not None and previous_close
            else _to_float(latest.get("pct_chg"))
        )
        trade_date = str(latest.get("trade_date", ""))
        notes = ["Tushare daily data is end-of-day data; volume was converted from lots (100 shares) to shares."]
        if basics_error:
            notes.append(f"Tushare daily_basic enrichment unavailable: {basics_error}")
        return Quote(
            symbol=normalized,
            name=name,
            currency="CNY",
            price=price,
            previous_close=previous_close,
            change=change,
            change_percent=change_percent,
            volume=_lots_to_shares(latest.get("vol")),
            market_cap=_to_float(basics.get("total_mv")) * 10_000 if basics.get("total_mv") is not None else None,
            pe_ratio=_to_float(basics.get("pe_ttm") or basics.get("pe")),
            source=self.name,
            as_of=_format_trade_date(trade_date),
            is_realtime=False,
            notes=notes,
        )

    def get_history(self, symbol: str, period: str = "1y", interval: str = "1d") -> list[Candle]:
        normalized, ts_code = self._require_a_share(symbol)
        pro = self._client()
        start_date, end_date = _date_window(period)
        data = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if data is None or data.empty:
            raise ProviderError("empty Tushare history")
        data = data.sort_values("trade_date")
        candles: list[Candle] = []
        for _, row in data.iterrows():
            candles.append(Candle(
                date=_format_trade_date(str(row.get("trade_date", ""))),
                open=_to_float(row.get("open")),
                high=_to_float(row.get("high")),
                low=_to_float(row.get("low")),
                close=_to_float(row.get("close")),
                volume=_lots_to_shares(row.get("vol")),
            ))
        return candles

    def get_financials(self, symbol: str) -> Financials:
        normalized, ts_code = self._require_a_share(symbol)
        pro = self._client()
        basics, basics_error = self._daily_basic(pro, ts_code)
        income, cashflow, fina_indicator, report_date = self._aligned_reports(pro, ts_code)
        revenue = _to_float(income.get("total_revenue") or income.get("revenue"))
        net_income = _to_float(income.get("n_income_attr_p") or income.get("net_profit"))
        return_on_equity = _to_float(fina_indicator.get("roe"))
        explicit_fcf = _to_float(cashflow.get("free_cashflow"))
        operating_cash_flow = _to_float(cashflow.get("n_cashflow_act"))
        capital_expenditure = _to_float(cashflow.get("c_pay_acq_const_fiolta"))
        free_cash_flow = explicit_fcf
        if free_cash_flow is None and operating_cash_flow is not None and capital_expenditure is not None:
            free_cash_flow = operating_cash_flow - capital_expenditure
        return Financials(
            symbol=normalized,
            source=self.name,
            as_of=report_date,
            currency="CNY",
            period_type="REPORTED",
            fetched_at=utc_now_iso(),
            market_cap=_to_float(basics.get("total_mv")) * 10_000 if basics.get("total_mv") is not None else None,
            pe_ratio=_to_float(basics.get("pe_ttm") or basics.get("pe")),
            eps=_to_float(fina_indicator.get("eps")),
            revenue=revenue,
            gross_profit=_to_float(income.get("grossprofit")),
            net_income=net_income,
            free_cash_flow=free_cash_flow,
            debt_to_equity=_debt_to_equity_from_debt_to_assets(fina_indicator.get("debt_to_assets")),
            return_on_equity=return_on_equity / 100 if return_on_equity is not None else None,
            profit_margin=(net_income / revenue if net_income is not None and revenue else None),
            notes=[
                "Tushare fundamentals depend on token permission and reporting availability.",
                "Tushare ROE 已从百分数归一化为比率；资产负债率已换算为债务/权益百分比。",
                "自由现金流仅使用明确 FCF，或由经营现金流减资本开支计算；缺少资本开支时保留为空。",
            ] + ([f"Tushare daily_basic enrichment unavailable: {basics_error}"] if basics_error else []),
        )

    def get_news(self, symbol: str, limit: int = 5) -> list[NewsItem]:
        raise ProviderError("Tushare news is not enabled in this provider")

    def _daily_basic(self, pro: Any, ts_code: str) -> tuple[dict[str, Any], str]:
        try:
            start_date, end_date = _date_window("1mo")
            data = pro.daily_basic(ts_code=ts_code, start_date=start_date, end_date=end_date)
            if data is not None and not data.empty:
                return data.sort_values("trade_date").iloc[-1].to_dict(), ""
        except Exception as exc:
            return {}, _compact_provider_error(exc)
        return {}, "empty result"

    def _stock_name(self, pro: Any, ts_code: str) -> str:
        try:
            data = pro.stock_basic(fields="ts_code,name")
            if data is not None and not data.empty:
                row = data.loc[data["ts_code"].astype(str) == ts_code]
                if not row.empty:
                    return str(row.iloc[0].get("name", ""))
        except Exception:
            return ""
        return ""

    def _aligned_reports(
        self,
        pro: Any,
        ts_code: str,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
        tables: list[dict[str, dict[str, Any]]] = []
        for method in ("income", "cashflow", "fina_indicator"):
            rows: dict[str, dict[str, Any]] = {}
            try:
                data = getattr(pro, method)(ts_code=ts_code)
                if data is not None and not data.empty:
                    date_column = "end_date" if "end_date" in data.columns else data.columns[0]
                    for _, row in data.iterrows():
                        values = row.to_dict()
                        date = _format_trade_date(str(values.get(date_column, "")))
                        if date:
                            rows[date] = values
            except Exception:
                pass
            tables.append(rows)
        dates = set().union(*(table.keys() for table in tables))
        if not dates:
            return {}, {}, {}, ""
        report_date = max(dates, key=lambda date: (sum(date in table for table in tables), date))
        income, cashflow, indicator = (table.get(report_date, {}) for table in tables)
        return income, cashflow, indicator, report_date
