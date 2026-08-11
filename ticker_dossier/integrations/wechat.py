"""Configurable WeChat/WeCom delivery adapter.

The default mode writes messages to a local ignored outbox. Production delivery
uses an official WeCom group bot webhook via FINANCE_WECHAT_WEBHOOK.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from ticker_dossier.config import load_local_env
from ticker_dossier.integrations.http import client as http_client


OUTBOX_DIR = Path(".finance_agent") / "wechat_outbox"


@dataclass
class WeChatMessage:
    content: str
    msgtype: str = "text"
    title: str = ""


@dataclass
class WeChatSendResult:
    mode: str
    destination: str
    status: str
    detail: str = ""
    path: Path | None = None

    def render(self) -> str:
        lines = [
            "WeChat delivery:",
            f"- mode: {self.mode}",
            f"- destination: {self.destination}",
            f"- status: {self.status}",
        ]
        if self.path:
            lines.append(f"- outbox: {self.path}")
        if self.detail:
            lines.append(f"- detail: {self.detail}")
        return "\n".join(lines)


def connector_status() -> str:
    load_local_env()
    webhook = os.environ.get("FINANCE_WECHAT_WEBHOOK", "").strip()
    relay_url = os.environ.get("FINANCE_WECHAT_RELAY_URL", "").strip()
    mode = _mode()
    destination = _destination(webhook, relay_url)
    return "\n".join([
        "WeChat connector status:",
        f"- mode: {mode}",
        f"- destination: {destination}",
        f"- outbox: {OUTBOX_DIR}",
        "- supported modes: dry-run, webhook, relay",
        "- env: FINANCE_WECHAT_MODE, FINANCE_WECHAT_WEBHOOK, FINANCE_WECHAT_RELAY_URL",
    ])


def send_text(content: str, title: str = "") -> WeChatSendResult:
    return send_message(WeChatMessage(content=content, msgtype="text", title=title))


def send_markdown(content: str, title: str = "") -> WeChatSendResult:
    return send_message(WeChatMessage(content=content, msgtype="markdown", title=title))


def send_message(message: WeChatMessage) -> WeChatSendResult:
    load_local_env()
    mode = _mode()
    if mode == "webhook":
        return _send_wecom_webhook(message)
    if mode == "relay":
        return _send_relay(message)
    return _write_outbox(message, mode)


def _mode() -> str:
    value = os.environ.get("FINANCE_WECHAT_MODE", "").strip().lower()
    if value in {"webhook", "relay", "dry-run", "dryrun", "file"}:
        return "dry-run" if value in {"dryrun", "file"} else value
    if os.environ.get("FINANCE_WECHAT_WEBHOOK", "").strip():
        return "webhook"
    if os.environ.get("FINANCE_WECHAT_RELAY_URL", "").strip():
        return "relay"
    return "dry-run"


def _destination(webhook: str, relay_url: str) -> str:
    if webhook:
        return _safe_url(webhook)
    if relay_url:
        return _safe_url(relay_url)
    return str(OUTBOX_DIR)


def _send_wecom_webhook(message: WeChatMessage) -> WeChatSendResult:
    webhook = os.environ.get("FINANCE_WECHAT_WEBHOOK", "").strip()
    if not webhook:
        return WeChatSendResult("webhook", "missing", "error", "FINANCE_WECHAT_WEBHOOK is not configured")
    payload = _wecom_payload(message)
    try:
        with http_client(timeout=15.0, follow_redirects=True) as client:
            response = client.post(webhook, json=payload)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:  # noqa: BLE001
        return WeChatSendResult("webhook", _safe_url(webhook), "error", f"{type(exc).__name__}: {exc}")
    errcode = data.get("errcode")
    status = "sent" if errcode in {0, "0", None} else "error"
    detail = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return WeChatSendResult("webhook", _safe_url(webhook), status, detail)


def _send_relay(message: WeChatMessage) -> WeChatSendResult:
    relay_url = os.environ.get("FINANCE_WECHAT_RELAY_URL", "").strip()
    if not relay_url:
        return WeChatSendResult("relay", "missing", "error", "FINANCE_WECHAT_RELAY_URL is not configured")
    payload = {
        "source": "ticker-dossier",
        "msgtype": message.msgtype,
        "title": message.title,
        "content": message.content,
    }
    try:
        with http_client(timeout=15.0, follow_redirects=True) as client:
            response = client.post(relay_url, json=payload)
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return WeChatSendResult("relay", _safe_url(relay_url), "error", f"{type(exc).__name__}: {exc}")
    return WeChatSendResult("relay", _safe_url(relay_url), "sent", f"HTTP {response.status_code}")


def _write_outbox(message: WeChatMessage, mode: str) -> WeChatSendResult:
    OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = OUTBOX_DIR / f"{stamp}_{message.msgtype}.json"
    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source": "ticker-dossier",
        "msgtype": message.msgtype,
        "title": message.title,
        "content": message.content,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return WeChatSendResult(mode, str(OUTBOX_DIR), "queued", "dry-run outbox only; no network send", path)


def _wecom_payload(message: WeChatMessage) -> dict[str, object]:
    if message.msgtype == "markdown":
        return {"msgtype": "markdown", "markdown": {"content": _limit(message.content, 3900)}}
    content = message.content
    if message.title:
        content = f"{message.title}\n{content}"
    return {"msgtype": "text", "text": {"content": _limit(content, 3900)}}


def _limit(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 24] + "\n...[truncated]"


def _safe_url(value: str) -> str:
    parsed = urlsplit(value)
    if not parsed.netloc:
        return value.split("?")[0]
    host = parsed.hostname or parsed.netloc
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{host}{port}{parsed.path}"
