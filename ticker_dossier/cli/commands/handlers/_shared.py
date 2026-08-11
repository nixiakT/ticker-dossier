"""Formatting and argument helpers shared by command handlers."""
from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import urlsplit

from ticker_dossier.cli.terminal.ui import current_lang
from ticker_dossier.runtime.context import redact_sensitive_text


def require_arg(args: list[str], usage: str) -> str:
    if not args:
        raise ValueError(f"用法：{usage}")
    return args[0]


def require_many(args: list[str], usage: str) -> list[str]:
    if not args:
        raise ValueError(f"用法：{usage}")
    return args


def period_arg(value: str) -> str:
    normalized = value.lower()
    return normalized if normalized in {"1mo", "3mo", "6mo", "1y", "2y", "5y"} else ""


def preview(value: Any, limit: int = 180) -> str:
    clean = " ".join(redact_sensitive_text(str(value)).split())
    if len(clean) <= limit:
        return clean
    return clean[:limit] + "..."


def json_preview(value: object) -> str:
    try:
        raw = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    except TypeError:
        raw = str(value)
    return redact_sensitive_text(raw)


def safe_base_url(value: str) -> str:
    if not value:
        return msg("not configured", "未配置")
    parsed = urlsplit(value)
    if not parsed.netloc:
        return value.split("?")[0]
    host = parsed.hostname or parsed.netloc
    port = ""
    try:
        if parsed.port:
            port = f":{parsed.port}"
    except ValueError:
        port = ""
    return f"{parsed.scheme}://{host}{port}{parsed.path.rstrip('/')}"


def think_label(value: str | bool) -> str:
    if value is True:
        return "on"
    if value is False or value is None:
        return "off"
    normalized = str(value).lower()
    return normalized if normalized in {"on", "compact", "off"} else "compact"


def msg(en: str, zh: str) -> str:
    return en if current_lang() == "en" else zh


def wechat_mode_label() -> str:
    mode = os.environ.get("FINANCE_WECHAT_MODE", "").strip() or "auto"
    if os.environ.get("FINANCE_WECHAT_WEBHOOK"):
        return f"{mode}/webhook"
    if os.environ.get("FINANCE_WECHAT_RELAY_URL"):
        return f"{mode}/relay"
    return "dry-run"


def is_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True
