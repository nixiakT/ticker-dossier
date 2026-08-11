"""Ticker normalization helpers."""
from __future__ import annotations

import re

CHINESE_SYMBOLS = {
    "apple": "AAPL",
    "apple inc": "AAPL",
    "nvidia": "NVDA",
    "microsoft": "MSFT",
    "tesla": "TSLA",
    "amazon": "AMZN",
    "alphabet": "GOOGL",
    "google": "GOOGL",
    "tencent": "00700.HK",
    "alibaba": "BABA",
    "meituan": "3690.HK",
    "advanced micro devices": "AMD",
    "苹果": "AAPL",
    "英伟达": "NVDA",
    "辉达": "NVDA",
    "微软": "MSFT",
    "特斯拉": "TSLA",
    "亚马逊": "AMZN",
    "谷歌": "GOOGL",
    "谷歌a": "GOOGL",
    "谷歌c": "GOOG",
    "amd": "AMD",
    "超威": "AMD",
    "贵州茅台": "600519.SS",
    "茅台": "600519.SS",
    "平安银行": "000001.SZ",
    "招商银行": "600036.SS",
    "腾讯": "00700.HK",
    "阿里巴巴": "BABA",
    "美团": "3690.HK",
    "智谱": "02513.HK",
    "智谱ai": "02513.HK",
    "智谱清言": "02513.HK",
    "knowledge atlas": "02513.HK",
    "spacex": "SPCX",
    "space x": "SPCX",
}


def normalize_symbol(symbol: str) -> str:
    raw = symbol.strip()
    if not raw:
        return raw
    lowered = raw.lower()
    if lowered in CHINESE_SYMBOLS:
        return CHINESE_SYMBOLS[lowered]

    normalized = raw.upper().replace("。", ".")
    hk_match = re.fullmatch(r"(\d{1,5})\.HK", normalized)
    if hk_match:
        code = hk_match.group(1)
        return f"{code.zfill(4)}.HK" if len(code) <= 4 else f"{code}.HK"
    if re.fullmatch(r"\d{6}", normalized):
        if normalized.startswith(("6", "9")):
            return f"{normalized}.SS"
        return f"{normalized}.SZ"
    if re.fullmatch(r"\d{1,5}", normalized):
        return f"{normalized.zfill(4)}.HK"
    return normalized


def is_a_share(symbol: str) -> bool:
    normalized = normalize_symbol(symbol)
    return bool(re.fullmatch(r"\d{6}\.(SS|SZ|SH|BJ)", normalized))


def to_tushare_symbol(symbol: str) -> str:
    normalized = normalize_symbol(symbol)
    if normalized.endswith(".SS"):
        return normalized[:-3] + ".SH"
    return normalized


def to_akshare_symbol(symbol: str) -> str:
    normalized = normalize_symbol(symbol)
    return normalized.split(".", 1)[0]


def to_yahoo_symbol(symbol: str) -> str:
    """Return the Yahoo Finance query symbol for a normalized ticker.

    HK tickers are usually displayed with 4 digits, while some Chinese
    finance sites display newly listed names with a leading zero as 5 digits
    (for example Snowball 02513). Yahoo expects 2513.HK for that case.
    """
    normalized = normalize_symbol(symbol)
    if normalized.endswith(".HK"):
        code = normalized[:-3]
        if code.isdigit():
            yahoo_code = (code.lstrip("0") or "0").zfill(4)
            return f"{yahoo_code}.HK"
    return normalized


def extract_symbols(text: str) -> list[str]:
    found: list[str] = []
    lowered = text.lower()
    for name, symbol in sorted(CHINESE_SYMBOLS.items(), key=lambda item: len(item[0]), reverse=True):
        if _alias_in_text(name, lowered):
            found.append(symbol)

    for match in re.finditer(r"(?:xueqiu\.com/S/|雪球[:：]?\s*)(\d{4,5})", text, flags=re.IGNORECASE):
        found.append(normalize_symbol(match.group(1)))

    text_without_urls = re.sub(r"https?://\S+", " ", text)
    pattern = re.compile(
        r"(?<![\w$])\$?[A-Za-z]{1,6}(?:\.[A-Za-z]{1,4})?(?!\w)"
        r"|(?<![\w$])\$?\d{4,6}(?:\.[A-Za-z]{1,4})?(?!\w)"
    )
    ignored = {
        "MA", "MACD", "RSI", "PE", "EPS", "ROE", "ETF", "API", "MVP", "CSV",
        "HTTP", "HTTPS", "URL", "WWW", "COM", "AI", "AGENT", "DAY", "BUY", "SELL",
        "IPO", "I", "IS", "AM", "ARE", "THE", "THIS", "THAT", "THINK", "GREAT",
        "GOOD", "BEST", "WILL", "WOULD", "COULD", "SHOULD", "RISE", "FALL", "STOCK",
        "PRICE", "ONLY", "JUST", "IGNORE", "RISK", "RISKS", "NEWS", "SAYS", "SAID",
        "UP", "DOWN", "AND", "OR", "OF", "TO", "IN", "ON", "FOR", "WITH", "IT",
        "ITS", "A", "AN", "AS", "BE", "ME", "MY", "YOU", "YOUR", "WE", "THEY",
        "HE", "SHE", "ALL", "MUST", "SURE", "CERTAIN", "GUARANTEED", "OUTLOOK",
    }
    known_tickers = _known_ticker_tokens()
    priority: list[str] = []
    other: list[str] = []
    for match in pattern.finditer(text_without_urls):
        raw_token = match.group(0)
        explicit = raw_token.startswith("$") or "." in raw_token
        token = raw_token.removeprefix("$")
        upper = token.upper()
        if upper in ignored and not explicit:
            continue
        numeric_part = token.split(".", 1)[0]
        if numeric_part.isdigit() and 1900 <= int(numeric_part) <= 2099 and not explicit:
            continue
        if token[0].isalpha() and token != upper and token.lower() not in known_tickers and not explicit:
            continue
        normalized = normalize_symbol(token)
        if not normalized or normalized in ignored:
            continue
        target = priority if token.lower() in known_tickers or explicit else other
        target.append(normalized)

    found.extend(priority)
    found.extend(other)

    unique: list[str] = []
    seen: set[str] = set()
    for symbol in found:
        # HK display codes may contain four or five digits (0700.HK/00700.HK),
        # but both address the same Yahoo market symbol and must not duplicate a report.
        key = to_yahoo_symbol(symbol)
        if key in seen:
            continue
        seen.add(key)
        unique.append(symbol)
    return unique


def _alias_in_text(alias: str, lowered_text: str) -> bool:
    if re.search(r"[a-z]", alias):
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", lowered_text))
    return alias in lowered_text


def _known_ticker_tokens() -> set[str]:
    tokens: set[str] = set()
    for symbol in CHINESE_SYMBOLS.values():
        lowered = symbol.lower()
        tokens.add(lowered)
        tokens.add(lowered.split(".", 1)[0])
    return tokens
