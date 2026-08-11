"""Session lifecycle, help, status, and observability command handlers."""
from __future__ import annotations

import os

from ticker_dossier.cli.command_types import CommandResult, HandlerResult
from ticker_dossier.cli.ui import current_lang, render_help
from ticker_dossier.security import safety_summary
from ticker_dossier.skills.loader import load_skills

from ._shared import msg, safe_base_url, think_label, wechat_mode_label


SESSION_HANDLER_METHODS = {
    "session.help": "handle_help",
    "session.exit": "handle_exit",
    "session.clear": "handle_clear",
    "session.compact": "handle_compact",
    "session.selfcheck": "handle_selfcheck",
    "session.think": "handle_think",
    "session.trace": "handle_trace",
    "session.lang": "handle_lang",
    "session.tools": "handle_tools",
    "session.skills": "handle_skills",
    "session.status": "handle_status",
    "session.security": "handle_security",
}


class SessionCommandHandlers:
    def handle_help(self, _args: list[str], _think_enabled: str | bool) -> HandlerResult:
        return render_help()

    def handle_exit(self, _args: list[str], _think_enabled: str | bool) -> HandlerResult:
        return CommandResult(True, exit=True)

    def handle_clear(self, _args: list[str], _think_enabled: str | bool) -> HandlerResult:
        return CommandResult(
            True,
            clear=True,
            output=msg("Session context cleared.", "已清空当前会话上下文。"),
        )

    def handle_compact(self, _args: list[str], _think_enabled: str | bool) -> HandlerResult:
        return CommandResult(True, compact=True)

    def handle_selfcheck(self, _args: list[str], _think_enabled: str | bool) -> HandlerResult:
        return CommandResult(True, selfcheck=True)

    def handle_tools(self, _args: list[str], _think_enabled: str | bool) -> HandlerResult:
        names = self.registry.names()
        return "已注册工具：\n" + "\n".join(f"- {name}" for name in names)

    def handle_skills(self, _args: list[str], _think_enabled: str | bool) -> HandlerResult:
        try:
            skills = load_skills()
        except Exception as exc:  # noqa: BLE001 - surface one broken project Skill clearly
            return msg(f"Skills failed to load: {exc}", f"Skills 加载失败：{exc}")
        if not skills:
            return msg("Skills: none discovered.", "Skills：未发现项目 Skill。")
        title = msg("Skills (loaded on demand):", "Skills（按需加载）：")
        return title + "\n" + "\n".join(
            f"- /{skill.name}: {skill.description}" for skill in skills
        )

    def handle_status(self, _args: list[str], think_enabled: str | bool) -> HandlerResult:
        diagnostics = self.finance.provider.diagnostics()
        enabled_sources = [row["name"] for row in diagnostics if row.get("status") == "enabled"]
        try:
            skill_count = len(load_skills())
        except Exception:
            skill_count = 0
        statuses = self.registry.mcp_statuses()
        connected_mcp = sum(row.get("status") == "connected" for row in statuses)
        mcp_summary = f"{connected_mcp}/{len(statuses)}" if statuses else "0/0"
        trace_label = think_label(think_enabled)
        if current_lang() == "en":
            return "\n".join([
                "TickerDossier status:",
                f"- Model: {os.environ.get('DEEPSEEK_MODEL', 'not configured')}",
                f"- Base URL: {safe_base_url(os.environ.get('DEEPSEEK_BASE_URL', ''))}",
                f"- Tools: {len(self.registry)}",
                f"- Skills: {skill_count} (on demand)",
                f"- MCP tools: {', '.join(self._mcp_tool_names()) or 'not connected'}",
                f"- MCP servers: {mcp_summary}",
                f"- Proxy: {self.proxy_label()}",
                f"- WeChat: {wechat_mode_label()}",
                f"- trace: {'on' if trace_label == 'on' else 'off'} (off by default; use /trace on for details)",
                f"- Data sources: {', '.join(enabled_sources) if enabled_sources else 'no real source enabled'}",
                "- License: MIT",
                "- Boundary: research only, no auto trading",
            ])
        return "\n".join([
            "TickerDossier 状态：",
            f"- 模型: {os.environ.get('DEEPSEEK_MODEL', '未配置')}",
            f"- Base URL: {safe_base_url(os.environ.get('DEEPSEEK_BASE_URL', ''))}",
            f"- 工具数: {len(self.registry)}",
            f"- Skills: {skill_count}（按需加载）",
            f"- MCP 工具: {', '.join(self._mcp_tool_names()) or '未接入'}",
            f"- MCP 服务: {mcp_summary}",
            f"- Proxy: {self.proxy_label()}",
            f"- WeChat: {wechat_mode_label()}",
            f"- trace: {'on' if trace_label == 'on' else 'off'}（默认 off；/trace on 展开详情）",
            f"- 数据源: {', '.join(enabled_sources) if enabled_sources else '无可用真实数据源'}",
            "- License: MIT",
            "- 边界: research only, no auto trading",
        ])

    def handle_security(self, _args: list[str], _think_enabled: str | bool) -> HandlerResult:
        return safety_summary()

    def _mcp_tool_names(self) -> list[str]:
        return [name for name in self.registry.names() if name.startswith("mcp__")]

    def handle_think(self, args: list[str], think_enabled: str | bool) -> HandlerResult:
        if not args:
            state = think_label(think_enabled)
            return CommandResult(True, msg(f"thinking state: {state}", f"thinking 当前状态：{state}"))
        value = args[0].lower()
        if value in {"on", "true", "1"}:
            return CommandResult(True, msg(
                "thinking expanded: timestamps, elapsed time, model turns, tool calls and result previews are shown.",
                "thinking 已开启：会显示时间、耗时、模型回合、工具调用和结果摘要。",
            ), think="on")
        if value in {"compact", "summary", "folded"}:
            return CommandResult(True, msg(
                "thinking compact: detailed trace is folded into a one-line summary. Use /trace after a task to expand the last trace.",
                "thinking compact：详细轨迹会收起成一行摘要。任务后输入 /trace 可展开上一轮轨迹。",
            ), think="compact")
        if value in {"off", "false", "0"}:
            return CommandResult(True, msg("thinking disabled.", "thinking 已关闭。"), think="off")
        return CommandResult(True, msg(
            "Usage: /think on | /think compact | /think off",
            "用法：/think on | /think compact | /think off",
        ))

    def handle_trace(self, args: list[str], think_enabled: str | bool) -> HandlerResult:
        if not args:
            state = "on" if think_label(think_enabled) == "on" else "off"
            return CommandResult(True, msg(f"trace state: {state}", f"trace 当前状态：{state}"))
        value = args[0].lower()
        if value in {"on", "true", "1"}:
            return CommandResult(True, msg(
                "trace on: model turns, tool calls, arguments and result previews stay visible.",
                "trace on：模型回合、工具调用、参数和结果摘要会全部保留在终端。",
            ), think="on")
        if value in {"off", "false", "0"}:
            return CommandResult(True, msg(
                "trace off: progress is folded into a live status and one completion summary (default).",
                "trace off：执行过程折叠为动态状态和一行完成摘要（默认）。",
            ), think="compact")
        return CommandResult(True, msg("Usage: /trace on | /trace off", "用法：/trace on | /trace off"))

    def handle_lang(self, args: list[str], _think_enabled: str | bool) -> HandlerResult:
        if not args:
            configured_lang = os.environ.get(
                "TICKER_DOSSIER_LANG",
                os.environ.get("FINANCE_AGENT_LANG", "zh"),
            )
            return CommandResult(True, f"当前语言 / Current language: {configured_lang}")
        value = args[0].lower()
        if value not in {"zh", "cn", "en"}:
            return CommandResult(True, "用法：/lang zh 或 /lang en")
        os.environ["TICKER_DOSSIER_LANG"] = "en" if value == "en" else "zh"
        return CommandResult(
            True,
            "Language set to English." if value == "en" else "语言已切换为中文。",
        )
