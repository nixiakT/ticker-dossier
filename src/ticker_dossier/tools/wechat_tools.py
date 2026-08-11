"""Tools for WeChat/WeCom delivery."""
from __future__ import annotations

from ticker_dossier.integrations.wechat import connector_status, send_markdown, send_text
from ticker_dossier.runtime.tools import Tool


def _wechat_status() -> str:
    return connector_status()


def _wechat_send(content: str, msgtype: str = "text", title: str = "") -> str:
    if msgtype == "markdown":
        return send_markdown(content, title=title).render()
    return send_text(content, title=title).render()


wechat_status_tool = Tool(
    name="wechat_status",
    description="查看微信/企业微信群机器人连接状态和配置模式。",
    parameters={"type": "object", "properties": {}},
    run=_wechat_status,
)

wechat_send_tool = Tool(
    name="wechat_send",
    description="把文本或 markdown 发送到微信连接器；未配置 webhook 时写入本地 dry-run outbox。",
    parameters={
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "要发送的内容"},
            "msgtype": {"type": "string", "description": "text 或 markdown"},
            "title": {"type": "string", "description": "可选标题"},
        },
        "required": ["content"],
    },
    run=_wechat_send,
)


wechat_tools = [wechat_status_tool, wechat_send_tool]
