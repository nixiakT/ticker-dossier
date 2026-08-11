"""HTTP helpers with optional finance-data proxy support."""
from __future__ import annotations

import os
from urllib.parse import urlsplit

import httpx

from ticker_dossier.config import load_local_env


PROXY_ENV_KEYS = ("FINANCE_HTTP_PROXY", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY")


def proxy_url() -> str:
    load_local_env()
    for key in PROXY_ENV_KEYS:
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return ""


def proxy_label() -> str:
    value = proxy_url()
    if not value:
        return "disabled"
    return _safe_url(value)


def client(
    *,
    timeout: float = 20.0,
    follow_redirects: bool = True,
    headers: dict[str, str] | None = None,
) -> httpx.Client:
    proxy = proxy_url()
    kwargs: dict[str, object] = {
        "timeout": timeout,
        "follow_redirects": follow_redirects,
        "headers": headers,
    }
    if proxy:
        kwargs["proxy"] = proxy
        kwargs["trust_env"] = False
    return httpx.Client(**kwargs)


def get(url: str, **kwargs: object) -> httpx.Response:
    proxy = proxy_url()
    if proxy:
        kwargs.setdefault("proxy", proxy)
        kwargs.setdefault("trust_env", False)
    return httpx.get(url, **kwargs)


def test_connectivity(url: str = "https://html.duckduckgo.com/html/?q=ticker-dossier") -> str:
    try:
        with client(timeout=12.0, follow_redirects=True) as http:
            response = http.get(url)
        return "\n".join([
            "Proxy connectivity test:",
            f"- proxy: {proxy_label()}",
            f"- url: {url}",
            f"- status: {response.status_code}",
            f"- final_url: {response.url}",
        ])
    except Exception as exc:  # noqa: BLE001
        return "\n".join([
            "Proxy connectivity test:",
            f"- proxy: {proxy_label()}",
            f"- url: {url}",
            f"- error: {type(exc).__name__}: {exc}",
        ])


def _safe_url(value: str) -> str:
    parsed = urlsplit(value)
    if not parsed.netloc:
        return value.split("?")[0]
    host = parsed.hostname or parsed.netloc
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{host}{port}"
