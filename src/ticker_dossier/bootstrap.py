"""Application composition root.

Only this module knows about every built-in capability and integration.  The
runtime keeps depending on the small ``Tool`` and ``ToolRegistry`` contracts,
while concrete finance, MCP, messaging, and scheduler adapters are injected
here.
"""
from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Any

from ticker_dossier.runtime.loop import AgentLoop
from ticker_dossier.runtime.tools import ToolRegistry

if TYPE_CHECKING:
    from ticker_dossier.research.agent import FinanceResearchAgent


@dataclass(frozen=True)
class ResearchServices:
    """Application-owned research services shared by CLI and tool adapters."""

    finance: FinanceResearchAgent


def build_research_services() -> ResearchServices:
    """Create one finance facade and its provider lifecycle for an application."""
    from ticker_dossier.research.agent import FinanceResearchAgent

    return ResearchServices(finance=FinanceResearchAgent())


def _compat_env(primary: str, legacy: str, default: str = "") -> str:
    """Read the renamed setting while preserving existing local setups."""
    value = os.environ.get(primary)
    return os.environ.get(legacy, default) if value is None else value


def build_default_registry(services: ResearchServices | None = None) -> ToolRegistry:
    """Create the complete built-in tool registry and connect project MCP."""
    from ticker_dossier.integrations.mcp.client import connect_project_mcp
    from ticker_dossier.tools.evolution_tools import build_evolution_tools
    from ticker_dossier.tools.finance_tools import build_finance_tools
    from ticker_dossier.tools.fs import read_tool, write_tool
    from ticker_dossier.tools.memory_tools import memory_tools
    from ticker_dossier.tools.more_tools import edit_tool, glob_tool, grep_tool, task_list_tool
    from ticker_dossier.tools.scheduler_tools import build_scheduler_tools
    from ticker_dossier.tools.shell import bash_tool
    from ticker_dossier.tools.skill_tools import read_skill_tool
    from ticker_dossier.tools.trace2skill_tools import trace2skill_tools
    from ticker_dossier.tools.web_tools import web_tools
    from ticker_dossier.tools.wechat_tools import wechat_tools

    active_services = services or build_research_services()
    registry = ToolRegistry()
    registry.provide_service("research", active_services)
    for tool in (
        read_tool,
        write_tool,
        bash_tool,
        edit_tool,
        grep_tool,
        glob_tool,
        task_list_tool,
        *memory_tools,
        read_skill_tool,
        *build_finance_tools(active_services.finance),
        *build_evolution_tools(active_services.finance),
        *trace2skill_tools,
        *web_tools,
        *build_scheduler_tools(active_services.finance),
        *wechat_tools,
    ):
        registry.register(tool)
    connect_project_mcp(registry)
    return registry


def build_agent(
    system_prompt: str,
    *,
    observer: Callable[[str, dict[str, Any]], None] | None = None,
    registry: ToolRegistry | None = None,
    notify: Callable[[str], None] | None = None,
    approved_tools: Iterable[str] | None = None,
) -> AgentLoop:
    """Assemble an ``AgentLoop`` with the configured or offline backend."""
    from ticker_dossier.research.symbols import extract_symbols, normalize_symbol, to_yahoo_symbol

    active_registry = registry if registry is not None else build_default_registry()
    try:
        from ticker_dossier.llm.deepseek import DeepSeekBackend

        backend: Any = DeepSeekBackend()
    except Exception as exc:  # noqa: BLE001 - an offline backend is an intentional fallback
        from ticker_dossier.llm.fake import FakeBackend

        if notify is not None:
            notify(
                f"[提示] 未启用真后端（{exc}），回退 FakeBackend。"
                "配置 DEEPSEEK_API_KEY 后即用真模型。"
            )
        backend = FakeBackend()

    configured_tools_raw = _compat_env(
        "TICKER_DOSSIER_APPROVED_TOOLS",
        "MINI_OPENCLAW_APPROVED_TOOLS",
    )
    configured_tools = {
        name.strip()
        for name in configured_tools_raw.split(",")
        if name.strip()
    }
    if approved_tools is not None:
        configured_tools.update(name.strip() for name in approved_tools if name.strip())
    auto_approve = _compat_env(
        "TICKER_DOSSIER_AUTO_APPROVE",
        "MINI_OPENCLAW_AUTO_APPROVE",
    ).lower() in {
        "1",
        "true",
        "yes",
    }
    return AgentLoop(
        backend,
        active_registry,
        system_prompt,
        auto_approve=auto_approve,
        observer=observer,
        approved_tools=configured_tools,
        symbol_extractor=extract_symbols,
        symbol_key=lambda value: to_yahoo_symbol(normalize_symbol(value)),
    )
