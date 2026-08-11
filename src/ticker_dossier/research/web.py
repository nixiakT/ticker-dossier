"""Lightweight web lookup helpers for finance verification."""
from __future__ import annotations

import html
import json
import re
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx

from ticker_dossier.integrations.http import client as http_client
from .symbols import extract_symbols, normalize_symbol, to_yahoo_symbol
from ticker_dossier.security import guard_outbound_text, guard_web_fetch


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
)
REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}
MAX_REDIRECTS = 5


def web_search(query: str, limit: int = 5) -> str:
    """Search the public web and return compact, source-linked results."""
    cleaned = query.strip()
    if not cleaned:
        raise ValueError("query is required")
    guard_outbound_text(cleaned, label="搜索词")

    search_url = ""
    search_error = ""
    try:
        with http_client(timeout=20.0, follow_redirects=True, headers=_headers()) as client:
            response = client.get("https://html.duckduckgo.com/html/", params={"q": cleaned})
            search_url = str(response.url)
            response.raise_for_status()
        rows = _parse_duckduckgo_results(response.text, limit)
    except httpx.HTTPStatusError as exc:
        rows = []
        search_error = f"搜索入口 HTTP {exc.response.status_code}: {exc.response.reason_phrase}"
        search_url = str(exc.response.url)
    except httpx.RequestError as exc:
        rows = []
        search_error = f"搜索入口连接失败: {_compact_network_error(exc)}"
        search_url = str(exc.request.url) if exc.request else ""
    if not rows:
        rows = _finance_link_fallback(cleaned, limit)
    if not rows:
        lines = [
            f"搜索: {cleaned}",
            "没有解析到搜索结果。可以尝试更具体的关键词，或直接使用 /fetch URL。",
        ]
        if search_error:
            lines.append(f"备注: {search_error}")
        if search_url:
            lines.append(f"搜索页: {search_url}")
        return "\n".join(lines)

    source = f"DuckDuckGo HTML ({search_url})" if not search_error else "本地财经链接 fallback"
    lines = [f"搜索: {cleaned}", f"来源: {source}"]
    if search_error:
        lines.append(f"备注: {search_error}")
        lines.append("备注: 已生成公开财经页面入口，请用 /fetch URL 或浏览器进一步核验。")
    for index, row in enumerate(rows, start=1):
        lines.append(f"{index}. {row['title']}")
        lines.append(f"   {row['url']}")
        if row.get("snippet"):
            lines.append(f"   {row['snippet']}")
    return "\n".join(lines)


def web_fetch(url: str, max_chars: int = 4000) -> str:
    """Fetch a URL and return status, title, metadata and a compact excerpt."""
    cleaned = url.strip()
    if not cleaned:
        raise ValueError("url is required")
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("只支持 http/https URL")

    try:
        with http_client(timeout=20.0, follow_redirects=False, headers=_headers()) as client:
            response = _guarded_web_get(client, cleaned)
    except httpx.RequestError as exc:
        return "\n".join([
            f"URL: {cleaned}",
            f"HTTP: UNAVAILABLE",
            f"备注: 抓取失败: {_compact_network_error(exc)}",
            "建议: 换用 /search 生成公开财经页面入口，或稍后重试该 URL。",
        ])

    content_type = response.headers.get("content-type", "")
    text = response.text
    notes: list[str] = []
    if _looks_like_waf(text):
        notes.append("页面返回了 WAF/JS challenge，无法在无浏览器登录态下读取完整正文。")
    if _looks_dynamic(text):
        notes.append("页面可能依赖 JavaScript 渲染，摘要只基于首屏 HTML。")

    title = _extract_title(text)
    description = _extract_meta(text, "description") or _extract_meta(text, "Description")
    excerpt = "" if _looks_like_waf(text) else _content_excerpt(text, content_type, max_chars)

    lines = [
        f"URL: {cleaned}",
        f"最终 URL: {response.url}",
        f"HTTP: {response.status_code}",
        f"Content-Type: {content_type or '未知'}",
    ]
    if title:
        lines.append(f"标题: {title}")
    if description:
        lines.append(f"描述: {description}")
    for note in notes:
        lines.append(f"备注: {note}")
    if excerpt:
        lines.append("")
        lines.append("内容摘要:")
        lines.append(excerpt)
    return "\n".join(lines)


def _guarded_web_get(client: httpx.Client, url: str) -> httpx.Response:
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        guard_web_fetch(current)
        response = client.get(current, follow_redirects=False)
        if response.status_code not in REDIRECT_STATUS_CODES:
            guard_web_fetch(str(response.url))
            return response
        location = response.headers.get("location", "").strip()
        if not location:
            raise httpx.HTTPError(f"redirect response missing Location: {current}")
        current = urljoin(current, location)
    raise httpx.TooManyRedirects(
        f"web_fetch exceeded {MAX_REDIRECTS} redirects",
        request=httpx.Request("GET", current),
    )


def _headers() -> dict[str, str]:
    return {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }


def _parse_duckduckgo_results(body: str, limit: int) -> list[dict[str, str]]:
    blocks = re.findall(r'<div class="result results_links.*?</div>\s*</div>', body, flags=re.S)
    if not blocks:
        blocks = re.findall(r'<a rel="nofollow" class="result__a".*?</a>.*?(?:<div class="result__snippet">.*?</div>)?', body, flags=re.S)

    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for block in blocks:
        link = re.search(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, flags=re.S)
        if not link:
            continue
        url = _decode_duckduckgo_url(html.unescape(link.group(1)))
        title = _clean_html(link.group(2))
        snippet_match = re.search(r'class="result__snippet"[^>]*>(.*?)</', block, flags=re.S)
        snippet = _clean_html(snippet_match.group(1)) if snippet_match else ""
        if not title or not url or url in seen:
            continue
        rows.append({"title": title, "url": url, "snippet": snippet})
        seen.add(url)
        if len(rows) >= max(limit, 1):
            break
    return rows


def _finance_link_fallback(query: str, limit: int) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for symbol in extract_symbols(query):
        normalized = normalize_symbol(symbol)
        if not normalized.endswith(".HK"):
            continue
        code = normalized[:-3]
        yahoo_symbol = to_yahoo_symbol(normalized)
        yahoo_code = yahoo_symbol[:-3]
        name = "智谱" if "智谱" in query else normalized
        candidates = [
            (f"{name} ({code})最新价格_行情_走势图—东方财富网", f"https://quote.eastmoney.com/hk/{code}.html"),
            (f"{name} ({code})股票股价,实时行情,新闻,财报数据_新浪财经_新浪网", f"https://stock.finance.sina.com.cn/hkstock/quotes/{code}.html"),
            (f"{name} ({code}) 股票股价_股价行情_财报_数据报告 - 雪球", f"https://xueqiu.com/S/{code}"),
            (f"{name} ({code})首页概览_港股行情_同花顺金融网", f"https://stockpage.10jqka.com.cn/HK{yahoo_code}/"),
            (f"{name} ({yahoo_symbol}) 股价、新闻、报价和记录 - Yahoo 财经", f"https://hk.finance.yahoo.com/quote/{yahoo_symbol}/"),
        ]
        for title, url in candidates:
            rows.append({"title": title, "url": url, "snippet": "由识别到的港股代码生成的公开财经页面核验入口。"})
            if len(rows) >= max(limit, 1):
                return rows
    return rows


def _decode_duckduckgo_url(url: str) -> str:
    if url.startswith("//"):
        url = "https:" + url
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    if "uddg" in query and query["uddg"]:
        return unquote(query["uddg"][0])
    return url


def _extract_title(body: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", body, flags=re.I | re.S)
    return _clean_html(match.group(1)) if match else ""


def _extract_meta(body: str, name: str) -> str:
    pattern = rf'<meta[^>]+name=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']+)["\']'
    match = re.search(pattern, body, flags=re.I | re.S)
    if not match:
        pattern = rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']{re.escape(name)}["\']'
        match = re.search(pattern, body, flags=re.I | re.S)
    return _clean_html(match.group(1)) if match else ""


def _content_excerpt(body: str, content_type: str, max_chars: int) -> str:
    limit = max(500, min(max_chars, 12_000))
    if "json" in content_type.lower():
        try:
            data: Any = json.loads(body)
            return json.dumps(data, ensure_ascii=False, indent=2)[:limit]
        except json.JSONDecodeError:
            return body[:limit]

    main = re.sub(r"(?is)<script.*?</script>", " ", body)
    main = re.sub(r"(?is)<style.*?</style>", " ", main)
    main = re.sub(r"(?is)<noscript.*?</noscript>", " ", main)
    main = re.sub(r"(?is)<[^>]+>", " ", main)
    main = html.unescape(main)
    lines = [line.strip() for line in re.split(r"[\r\n]+", main)]
    text = "\n".join(line for line in lines if line)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text[:limit]


def _clean_html(value: str) -> str:
    text = re.sub(r"(?is)<[^>]+>", " ", value)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _looks_like_waf(body: str) -> bool:
    lowered = body.lower()
    return any(marker in lowered for marker in ("aliyun_waf", "_waf_", "acw_tc", "captcha"))


def _looks_dynamic(body: str) -> bool:
    lowered = body.lower()
    markers = ("__next_data__", "window.__", "id=\"app\"", "id=\"root\"", "data-reactroot")
    return any(marker in lowered for marker in markers)


def _compact_network_error(exc: Exception, limit: int = 180) -> str:
    text = " ".join(str(exc).split())
    if len(text) <= limit:
        return text
    return text[:limit] + "..."
