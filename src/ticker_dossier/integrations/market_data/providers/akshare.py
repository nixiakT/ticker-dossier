"""AKShare market-data adapter."""

from __future__ import annotations

from typing import Any

from ticker_dossier.research.models import Candle, Financials, NewsItem, Quote, utc_now_iso
from ticker_dossier.research.symbols import (
    is_a_share,
    normalize_symbol,
    to_akshare_symbol,
    to_tushare_symbol,
)

from .._normalization import (
    _date_window,
    _debt_to_equity_from_debt_to_assets,
    _field_value,
    _lots_to_shares,
    _to_float,
)
from ..base import ProviderError


class AKShareProvider:
    name = "AKShare"
    news_is_symbol_scoped = True

    def __init__(self, timeout: float = 20.0):
        self.timeout = timeout
        self._ak = None

    def supports(self, method: str, symbol: str, *args: Any) -> bool:
        normalized = normalize_symbol(symbol)
        if method == "get_financials":
            return is_a_share(normalized) or normalized.endswith(".HK") or "." not in normalized
        return is_a_share(normalized)

    def _client(self) -> Any:
        if self._ak is None:
            try:
                import akshare as ak  # type: ignore
            except ImportError as exc:
                raise ProviderError("akshare package is not installed") from exc
            self._ak = ak
        return self._ak

    def _require_a_share(self, symbol: str) -> tuple[str, str]:
        normalized = normalize_symbol(symbol)
        if not is_a_share(normalized):
            raise ProviderError("AKShareProvider supports A-share symbols only")
        return normalized, to_akshare_symbol(normalized)

    def get_quote(self, symbol: str) -> Quote:
        normalized, code = self._require_a_share(symbol)
        ak = self._client()
        data = ak.stock_zh_a_spot_em()
        if data is None or data.empty:
            raise ProviderError("empty AKShare spot data")
        row = data.loc[data["代码"].astype(str) == code]
        if row.empty:
            raise ProviderError(f"{code} not found in AKShare spot data")
        item = row.iloc[0].to_dict()
        return Quote(
            symbol=normalized,
            name=str(item.get("名称", "")),
            currency="CNY",
            price=_to_float(item.get("最新价")),
            previous_close=None,
            change=_to_float(item.get("涨跌额")),
            change_percent=_to_float(item.get("涨跌幅")),
            volume=_lots_to_shares(item.get("成交量")),
            market_cap=_to_float(item.get("总市值")),
            pe_ratio=_to_float(item.get("市盈率-动态") or item.get("市盈率")),
            source=self.name,
            as_of="",
            is_realtime=False,
            notes=[
                "AKShare/Eastmoney spot data exposes no exchange timestamp here; fetch time is not used as market time.",
                "A-share volume was converted from lots (100 shares) to shares.",
            ],
        )

    def get_history(self, symbol: str, period: str = "1y", interval: str = "1d") -> list[Candle]:
        normalized, code = self._require_a_share(symbol)
        ak = self._client()
        start_date, end_date = _date_window(period)
        data = ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust="",
            timeout=self.timeout,
        )
        if data is None or data.empty:
            raise ProviderError("empty AKShare history")
        candles: list[Candle] = []
        for _, row in data.iterrows():
            candles.append(Candle(
                date=str(row.get("日期", "")),
                open=_to_float(row.get("开盘")),
                high=_to_float(row.get("最高")),
                low=_to_float(row.get("最低")),
                close=_to_float(row.get("收盘")),
                volume=_lots_to_shares(row.get("成交量")),
            ))
        return candles

    def get_financials(self, symbol: str) -> Financials:
        normalized = normalize_symbol(symbol)
        ak = self._client()
        if is_a_share(normalized):
            query_symbol = to_tushare_symbol(normalized)
            data = ak.stock_financial_analysis_indicator_em(symbol=query_symbol, indicator="按报告期")
            fields = {
                "eps": "EPSJB",
                "revenue": "TOTALOPERATEREVE",
                "gross_profit": "MLR",
                "net_income": "PARENTNETPROFIT",
                "free_cash_flow": "FCFF_BACK",
                "debt_to_equity": "CQBL",
                "debt_to_assets": "ZCFZL",
                "return_on_equity": "ROEJQ",
                "profit_margin": "XSJLL",
            }
        elif normalized.endswith(".HK"):
            query_symbol = normalized[:-3].zfill(5)
            data = ak.stock_financial_hk_analysis_indicator_em(symbol=query_symbol, indicator="年度")
            fields = {
                "eps": "BASIC_EPS",
                "revenue": "OPERATE_INCOME",
                "gross_profit": "GROSS_PROFIT",
                "net_income": "HOLDER_PROFIT",
                "debt_to_assets": "DEBT_ASSET_RATIO",
                "return_on_equity": "ROE_AVG",
                "profit_margin": "NET_PROFIT_RATIO",
            }
        elif "." not in normalized:
            query_symbol = normalized
            data = ak.stock_financial_us_analysis_indicator_em(symbol=query_symbol, indicator="年报")
            fields = {
                "eps": ("BASIC_EPS", "BASIC_EPS_CS"),
                "revenue": ("OPERATE_INCOME", "TOTAL_INCOME"),
                "gross_profit": "GROSS_PROFIT",
                "net_income": "PARENT_HOLDER_NETPROFIT",
                "debt_to_assets": ("DEBT_ASSET_RATIO", "DEBT_RATIO"),
                "return_on_equity": ("ROE_AVG", "ROE"),
                "profit_margin": "NET_PROFIT_RATIO",
            }
        else:
            raise ProviderError(f"AKShare financial indicators do not support {normalized}")

        if data is None or data.empty:
            raise ProviderError(f"empty AKShare financial indicators for {query_symbol}")
        if "REPORT_DATE" in data.columns:
            data = data.sort_values("REPORT_DATE", ascending=False)
        row = data.iloc[0].to_dict()
        report_date = str(row.get("REPORT_DATE") or "").split(" ", 1)[0]
        return_on_equity = _to_float(_field_value(row, fields["return_on_equity"]))
        profit_margin = _to_float(_field_value(row, fields["profit_margin"]))
        debt_to_equity = _to_float(_field_value(row, fields.get("debt_to_equity", "")))
        if debt_to_equity is None:
            debt_to_equity = _debt_to_equity_from_debt_to_assets(
                _field_value(row, fields.get("debt_to_assets", ""))
            )
        return Financials(
            symbol=normalized,
            source=self.name,
            as_of=report_date,
            currency=str(row.get("CURRENCY") or ("CNY" if is_a_share(normalized) else "")),
            period_type="REPORTED" if is_a_share(normalized) else "ANNUAL",
            fetched_at=utc_now_iso(),
            eps=_to_float(_field_value(row, fields["eps"])),
            revenue=_to_float(_field_value(row, fields["revenue"])),
            gross_profit=_to_float(_field_value(row, fields["gross_profit"])),
            net_income=_to_float(_field_value(row, fields["net_income"])),
            free_cash_flow=_to_float(_field_value(row, fields.get("free_cash_flow", ""))),
            debt_to_equity=debt_to_equity,
            return_on_equity=return_on_equity / 100 if return_on_equity is not None else None,
            profit_margin=profit_margin / 100 if profit_margin is not None else None,
            notes=[
                f"AKShare 真实财务指标，报告期 {report_date or '未知'}；估值字段需由行情源补充。",
                "杠杆统一为债务/权益百分比；负权益情形标记为极高风险。",
            ],
        )

    def get_news(self, symbol: str, limit: int = 5) -> list[NewsItem]:
        normalized, code = self._require_a_share(symbol)
        ak = self._client()
        if not hasattr(ak, "stock_news_em"):
            raise ProviderError("AKShare stock_news_em is unavailable")
        data = ak.stock_news_em(symbol=code)
        if data is None or data.empty:
            raise ProviderError("empty AKShare news")
        items: list[NewsItem] = []
        for _, row in data.head(limit).iterrows():
            items.append(NewsItem(
                title=str(row.get("新闻标题") or row.get("标题") or ""),
                publisher=str(row.get("文章来源") or row.get("来源") or "Eastmoney"),
                link=str(row.get("新闻链接") or row.get("链接") or ""),
                published_at=str(row.get("发布时间") or row.get("时间") or ""),
                source=self.name,
            ))
        return items
