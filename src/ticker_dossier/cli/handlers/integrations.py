"""External connectivity, data-source, and web command handlers."""
from __future__ import annotations

import os
from pathlib import Path

from ticker_dossier.cli.command_types import HandlerResult
from ticker_dossier.cli.ui import current_lang
from ticker_dossier.runtime import permissions
from ticker_dossier.security import guard_outbound_text, guard_web_fetch, untrusted_block

from ._shared import msg, require_arg


INTEGRATION_HANDLER_METHODS = {
    "integrations.proxy": "handle_proxy",
    "integrations.wechat": "handle_wechat",
    "integrations.mcp": "handle_mcp",
    "integrations.sources": "handle_sources",
    "integrations.search": "handle_search",
    "integrations.fetch": "handle_fetch",
}


class IntegrationCommandHandlers:
    def handle_mcp(self, _args: list[str], _think_enabled: str | bool) -> HandlerResult:
        names = self._mcp_tool_names()
        statuses = self.registry.mcp_statuses()
        prompts = self.registry.mcp_prompts()
        if not names and not statuses and not prompts:
            return msg(
                "MCP: no configured servers, tools, or prompts.",
                "MCP：未配置服务、工具或 prompt。",
            )
        lines = [msg("MCP runtime:", "MCP 运行状态：")]
        if statuses:
            lines.append(msg("Servers:", "服务："))
            for row in statuses:
                detail = f" - {row.get('detail')}" if row.get("detail") else ""
                lines.append(f"- {row.get('name')}: {row.get('status')}{detail}")
        if names:
            lines.append(msg("Tools:", "工具："))
            lines.extend(f"- {name}" for name in names)
        if prompts:
            lines.append(msg("Prompt commands:", "Prompt 命令："))
            lines.extend(
                f"- /mcp:{row.get('server')}:{row.get('name')}: {row.get('description', '')}".rstrip()
                for row in prompts
            )
        return "\n".join(lines)

    def handle_proxy(self, args: list[str], _think_enabled: str | bool) -> HandlerResult:
        if not args or args[0].lower() in {"status", "show"}:
            if current_lang() == "en":
                return "\n".join([
                    f"Proxy: {self.proxy_label()}",
                    "Set FINANCE_HTTP_PROXY for persistence, for example http://127.0.0.1:7897.",
                ])
            return "\n".join([
                f"Proxy: {self.proxy_label()}",
                "配置环境变量 FINANCE_HTTP_PROXY 可持久启用，例如 http://127.0.0.1:7897。",
            ])
        action = args[0].lower()
        if action == "test":
            url = args[1] if len(args) > 1 else "https://html.duckduckgo.com/html/?q=SpaceX+SPCX"
            return self.test_connectivity(url)
        if action == "set":
            if len(args) < 2:
                return "用法：/proxy set http://127.0.0.1:7897"
            os.environ["FINANCE_HTTP_PROXY"] = args[1]
            if current_lang() == "en":
                return (
                    f"Proxy set for current process: {self.proxy_label()}\n"
                    f"For persistence, write this to .env.local: FINANCE_HTTP_PROXY={args[1]}"
                )
            return (
                f"当前进程代理已设置为: {self.proxy_label()}\n"
                f"如需持久化，请写入 .env.local：FINANCE_HTTP_PROXY={args[1]}"
            )
        if action in {"off", "disable"}:
            os.environ.pop("FINANCE_HTTP_PROXY", None)
            return msg(
                "FINANCE_HTTP_PROXY disabled for current process.",
                "当前进程 FINANCE_HTTP_PROXY 已关闭。",
            )
        return msg(
            "Usage: /proxy status | /proxy test [url] | /proxy set http://127.0.0.1:7897 | /proxy off",
            "用法：/proxy status | /proxy test [url] | /proxy set http://127.0.0.1:7897 | /proxy off",
        )

    def handle_wechat(self, args: list[str], _think_enabled: str | bool) -> HandlerResult:
        if not args or args[0].lower() in {"status", "show"}:
            self._trace_tool("wechat_status", {})
            return self._with_result_trace("wechat_status", self.connector_status())
        action = args[0].lower()
        if action in {"send", "send-md"}:
            confirmed = len(args) > 1 and args[1].lower() == "--confirm"
            content = " ".join(args[2:] if confirmed else args[1:]).strip()
            if not content:
                return msg(
                    f"Usage: /wechat {action} [--confirm] <message>",
                    f"用法：/wechat {action} [--confirm] <内容>",
                )
            self._trace_tool("wechat_status", {})
            status = self._with_result_trace("wechat_status", self.connector_status())
            verdict = permissions.check("wechat_send", {"content": content}, Path.cwd())
            if verdict == "deny":
                return status + "\n" + permissions.denial_message(
                    "wechat_send",
                    {"content": "[redacted]"},
                    Path.cwd(),
                )
            if verdict == "confirm" and not confirmed:
                return "\n".join([
                    status,
                    "[权限层] 当前连接会产生真实外传，尚未发送。",
                    f"确认目标无误后，重新执行 /wechat {action} --confirm <内容>。",
                ])
            msgtype = "markdown" if action == "send-md" else "text"
            self._trace_tool("wechat_send", {"msgtype": msgtype, "content": "[redacted]"})
            result = self.send_markdown(content) if action == "send-md" else self.send_text(content)
            return status + "\n" + self._with_result_trace("wechat_send", result.render())
        return msg(
            "Usage: /wechat status | /wechat send [--confirm] <message> | /wechat send-md [--confirm] <markdown>",
            "用法：/wechat status | /wechat send [--confirm] <内容> | /wechat send-md [--confirm] <markdown>",
        )

    def handle_sources(self, _args: list[str], _think_enabled: str | bool) -> HandlerResult:
        diagnostics = self.finance.provider.diagnostics()
        lines = [msg("Current data sources:", "当前数据源状态：")]
        for index, row in enumerate(diagnostics, start=1):
            detail = f" - {row['detail']}" if row.get("detail") else ""
            lines.append(f"{index}. {row['name']}: {row['status']}{detail}")
        return "\n".join(lines)

    def handle_search(self, args: list[str], _think_enabled: str | bool) -> HandlerResult:
        query = " ".join(args).strip()
        if not query:
            raise ValueError("用法：/search 智谱 02513 股票")
        self._trace_tool("web_search", {"query": query, "limit": 5})
        guard_outbound_text(query, label="搜索词")
        output = untrusted_block("WEB_SEARCH", query, self.web_search_service(query, 5))
        return self._with_result_trace("web_search", output)

    def handle_fetch(self, args: list[str], _think_enabled: str | bool) -> HandlerResult:
        url = require_arg(args, "/fetch https://xueqiu.com/S/02513")
        self._trace_tool("web_fetch", {"url": url})
        guard_web_fetch(url)
        output = untrusted_block("WEB", url, self.web_fetch_service(url, 4000))
        return self._with_result_trace("web_fetch", output)
