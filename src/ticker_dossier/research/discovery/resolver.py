"""Resolve company names and aliases into tradable ticker symbols."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx

from ticker_dossier.integrations.http import get as http_get
from ticker_dossier.market_data.symbols import CHINESE_SYMBOLS, normalize_symbol


USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Safari/537.36"


@dataclass
class SymbolCandidate:
    symbol: str
    name: str = ""
    market: str = ""
    source: str = ""


def resolve_symbol(query: str) -> SymbolCandidate:
    """Resolve a user-entered company name/ticker across US, HK and A-share markets."""
    cleaned = query.strip()
    if not cleaned:
        raise ValueError("symbol query is required")

    lowered = cleaned.lower()
    if lowered in CHINESE_SYMBOLS:
        return SymbolCandidate(CHINESE_SYMBOLS[lowered], cleaned, "alias", "local alias")

    direct = _direct_symbol(cleaned)
    if direct and _is_explicit_market_symbol(cleaned):
        return direct

    candidates = [
        *_eastmoney_candidates(cleaned),
        *_yahoo_candidates(cleaned),
        *_akshare_candidates(cleaned),
        *_web_candidates(cleaned),
        *([direct] if direct else []),
    ]
    if not candidates:
        raise LookupError(f"无法解析标的：{cleaned}")
    ranked = _rank_candidates(cleaned, candidates)
    if not ranked or _lexical_relevance(cleaned, ranked[0]) < 40:
        raise LookupError(f"无法可靠解析标的：{cleaned}；请提供 ticker 或用 /resolve 查看候选")
    if len(ranked) > 1:
        first_score = _lexical_relevance(cleaned, ranked[0])
        second_score = _lexical_relevance(cleaned, ranked[1])
        if ranked[0].symbol != ranked[1].symbol and first_score - second_score < 10:
            raise LookupError(
                f"标的解析存在歧义：{cleaned}；请用 /resolve 查看候选并明确 ticker"
            )
    return ranked[0]


def resolve_symbol_text(query: str, limit: int = 8) -> str:
    cleaned = query.strip()
    candidates = [
        *_direct_list(cleaned),
        *_local_alias_list(cleaned),
        *_eastmoney_candidates(cleaned),
        *_yahoo_candidates(cleaned),
        *_akshare_candidates(cleaned),
        *_web_candidates(cleaned),
    ]
    ranked = _dedupe(_rank_candidates(cleaned, candidates))[:max(limit, 1)]
    if not ranked:
        return f"未解析到可交易标的：{cleaned}"
    lines = [f"标的解析: {cleaned}"]
    for index, candidate in enumerate(ranked, start=1):
        detail = f" - {candidate.name}" if candidate.name else ""
        market = f" ({candidate.market})" if candidate.market else ""
        lines.append(f"{index}. {candidate.symbol}{market}{detail} [{candidate.source}]")
    return "\n".join(lines)


def _direct_symbol(value: str) -> SymbolCandidate | None:
    normalized = normalize_symbol(value)
    if _looks_like_symbol(value, normalized) and _is_direct_symbol_input(value):
        return SymbolCandidate(normalized, value, _market(normalized), "direct")
    return None


def _direct_list(value: str) -> list[SymbolCandidate]:
    direct = _direct_symbol(value)
    return [direct] if direct else []


def _local_alias_list(value: str) -> list[SymbolCandidate]:
    lowered = value.lower()
    if lowered in CHINESE_SYMBOLS:
        symbol = CHINESE_SYMBOLS[lowered]
        return [SymbolCandidate(symbol, value, _market(symbol), "local alias")]
    return []


def _looks_like_symbol(raw: str, normalized: str) -> bool:
    text = raw.strip().upper()
    return bool(
        re.fullmatch(r"[A-Z]{1,6}(?:\.[A-Z]{1,4})?", text)
        or re.fullmatch(r"\d{1,6}(?:\.[A-Z]{1,4})?", text)
        or normalized != raw.strip()
    ) and not _looks_like_name(raw)


def _is_explicit_market_symbol(raw: str) -> bool:
    text = raw.strip().upper()
    return bool(re.fullmatch(r"\d{1,6}(?:\.[A-Z]{1,4})?", text) or re.fullmatch(r"[A-Z]{1,6}\.[A-Z]{1,4}", text))


def _is_direct_symbol_input(raw: str) -> bool:
    stripped = raw.strip()
    text = stripped.upper()
    if _is_explicit_market_symbol(stripped):
        return True
    if re.fullmatch(r"[A-Z]{1,6}", stripped) and stripped == text:
        return True
    return False


def _looks_like_name(raw: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", raw) or " " in raw.strip())


def _yahoo_candidates(query: str) -> list[SymbolCandidate]:
    try:
        response = http_get(
            "https://query2.finance.yahoo.com/v1/finance/search",
            params={"q": query, "quotesCount": "12", "newsCount": "0"},
            headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*"},
            timeout=12.0,
            follow_redirects=True,
        )
        response.raise_for_status()
        data = response.json()
    except Exception:
        return []

    rows: list[SymbolCandidate] = []
    for item in data.get("quotes") or []:
        symbol = str(item.get("symbol") or "").strip()
        if not symbol or item.get("quoteType") not in {None, "EQUITY", "ETF"}:
            continue
        rows.append(SymbolCandidate(
            symbol=normalize_symbol(symbol),
            name=str(item.get("shortname") or item.get("longname") or ""),
            market=str(item.get("exchange") or item.get("exchDisp") or ""),
            source="Yahoo Finance search",
        ))
    return rows


def _eastmoney_candidates(query: str) -> list[SymbolCandidate]:
    try:
        response = http_get(
            "https://searchapi.eastmoney.com/api/suggest/get",
            params={
                "input": query,
                "type": "14",
                "token": "D43BF722C8E33B65A330C3F7EB6A333C",
            },
            headers={"User-Agent": USER_AGENT, "Referer": "https://quote.eastmoney.com/"},
            timeout=12.0,
            follow_redirects=True,
        )
        response.raise_for_status()
        data = response.json()
    except Exception:
        return []

    rows: list[SymbolCandidate] = []
    for item in ((data.get("QuotationCodeTable") or {}).get("Data") or []):
        code = str(item.get("Code") or item.get("UnifiedCode") or "").strip()
        name = str(item.get("Name") or "").strip()
        if not code:
            continue
        symbol = _eastmoney_symbol(code, str(item.get("JYS") or ""), str(item.get("Classify") or ""))
        if not symbol:
            continue
        rows.append(SymbolCandidate(
            symbol=symbol,
            name=name,
            market=str(item.get("SecurityTypeName") or item.get("Classify") or ""),
            source="Eastmoney suggest",
        ))
    return rows


def _eastmoney_symbol(code: str, exchange: str, classify: str) -> str:
    exchange_upper = exchange.upper()
    classify_upper = classify.upper()
    if exchange_upper == "HK" or classify_upper == "HK":
        return normalize_symbol(f"{code}.HK")
    if code.isdigit() and len(code) == 6:
        return normalize_symbol(code)
    if code:
        return normalize_symbol(code)
    return ""


def _akshare_candidates(query: str) -> list[SymbolCandidate]:
    try:
        import akshare as ak  # type: ignore
    except ImportError:
        return []
    try:
        data = ak.stock_zh_a_spot_em()
    except Exception:
        return []
    if data is None or data.empty:
        return []
    if "代码" not in data.columns or "名称" not in data.columns:
        return []

    rows: list[SymbolCandidate] = []
    query_lower = query.lower()
    for _, row in data.iterrows():
        code = str(row.get("代码", "")).strip()
        name = str(row.get("名称", "")).strip()
        if not code or not name:
            continue
        if query_lower not in name.lower() and query_lower not in code.lower():
            continue
        rows.append(SymbolCandidate(
            symbol=normalize_symbol(code),
            name=name,
            market="A-share",
            source="AKShare spot",
        ))
        if len(rows) >= 10:
            break
    return rows


def _web_candidates(query: str) -> list[SymbolCandidate]:
    try:
        response = http_get(
            "https://html.duckduckgo.com/html/",
            params={"q": f"{query} 股票代码 stock ticker"},
            headers={"User-Agent": USER_AGENT},
            timeout=12.0,
            follow_redirects=True,
        )
        response.raise_for_status()
    except Exception:
        return []
    text = response.text
    rows: list[SymbolCandidate] = []
    patterns = [
        r"\b(\d{1,5})\.HK\b",
        r"\bHK[:\s]*(\d{4,5})\b",
        r"\b(\d{6})\.(?:SS|SH|SZ)\b",
        r"\b(?:NYSE|NASDAQ|Nasdaq|NYSE American)[:\s]+([A-Z]{1,6})\b",
    ]
    cleaned_text = _clean_html(text)
    for pattern in patterns:
        for match in re.finditer(pattern, cleaned_text):
            if not _context_supports_query(cleaned_text, match.start(), match.end(), query):
                continue
            symbol = normalize_symbol(match.group(1) + ".HK" if "HK" in pattern else match.group(1))
            rows.append(SymbolCandidate(symbol=symbol, name=query, source="web search", market=_market(symbol)))
    for match in re.finditer(r"(?:港股|股票代码|代码|HK)[:：\s]*0?(\d{4,5})", cleaned_text, flags=re.IGNORECASE):
        if not _context_supports_query(cleaned_text, match.start(), match.end(), query):
            continue
        rows.append(SymbolCandidate(
            symbol=normalize_symbol(f"{match.group(1)}.HK"),
            name=query,
            source="web search",
            market="HK",
        ))
    return rows


def _clean_html(value: str) -> str:
    text = re.sub(r"(?is)<script.*?</script>", " ", value)
    text = re.sub(r"(?is)<style.*?</style>", " ", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text)


def _context_supports_query(text: str, start: int, end: int, query: str, radius: int = 180) -> bool:
    """Require a web ticker hit to appear in the same snippet as the requested company."""
    query_key = re.sub(r"\s+", " ", query.strip().lower())
    if not query_key:
        return False
    context = text[max(0, start - radius): min(len(text), end + radius)].lower()
    return query_key in context


def _rank_candidates(query: str, candidates: list[SymbolCandidate]) -> list[SymbolCandidate]:
    def score(candidate: SymbolCandidate) -> tuple[int, str]:
        value = _lexical_relevance(query, candidate)
        if len(candidate.symbol) <= 5 and "." not in candidate.symbol:
            value += 8
        if candidate.symbol.endswith((".HK", ".SS", ".SZ")):
            value += 15
        if candidate.market in {"NMS", "NYQ"}:
            value += 30
        if candidate.market in {"HKG", "美股", "港股", "沪A", "深A", "A-share"}:
            value += 20
        if candidate.source == "Eastmoney suggest":
            value += 25
        if candidate.source == "Yahoo Finance search":
            value += 10
        if candidate.source == "AKShare spot":
            value += 10
        if candidate.source == "local alias":
            value += 200
        return (-value, candidate.symbol)

    return sorted(_dedupe(candidates), key=score)


def _lexical_relevance(query: str, candidate: SymbolCandidate) -> int:
    query_lower = query.strip().lower()
    symbol_lower = candidate.symbol.lower()
    name_lower = candidate.name.lower()
    value = 0
    if symbol_lower == query_lower:
        value += 100
    if name_lower == query_lower:
        value += 90
    if query_lower and query_lower in name_lower:
        value += 70
    if query_lower and query_lower in symbol_lower:
        value += 60
    if query_lower and name_lower.startswith(query_lower):
        value += 20
    if query_lower in name_lower and any(token in name_lower for token in (" inc", " corporation", " corp", " limited", " group")):
        value += 15
    if candidate.source == "local alias":
        value += 200
    return value


def _dedupe(candidates: list[SymbolCandidate]) -> list[SymbolCandidate]:
    rows: list[SymbolCandidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        symbol = normalize_symbol(candidate.symbol)
        key = symbol
        if symbol.endswith(".HK") and symbol[:-3].isdigit():
            key = f"{int(symbol[:-3])}.HK"
        if not symbol or key in seen:
            continue
        rows.append(SymbolCandidate(symbol, candidate.name, candidate.market or _market(symbol), candidate.source))
        seen.add(key)
    return rows


def _market(symbol: str) -> str:
    if symbol.endswith(".HK"):
        return "HK"
    if symbol.endswith((".SS", ".SZ", ".SH")):
        return "A-share"
    if "." not in symbol:
        return "US"
    return ""
