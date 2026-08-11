"""Single source of truth for CLI command discovery, help, and completion."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class CommandSpec:
    name: str
    usage: str
    description_zh: str
    description_en: str
    category: str


@dataclass(frozen=True)
class CompletionItem:
    """One row in the shared slash-command completion menu."""

    text: str
    description: str = ""
    kind: str = "builtin"


_COMMANDS = (
    CommandSpec("help", "/help", "查看命令菜单", "show command menu", "session"),
    CommandSpec("status", "/status", "查看模型、数据、Skill 和 MCP 状态", "show runtime status", "session"),
    CommandSpec("trace", "/trace", "展开上一轮执行轨迹", "show last execution trace", "session"),
    CommandSpec("trace-on", "/trace on", "实时显示全部执行轨迹", "show full execution trace", "session"),
    CommandSpec("trace-off", "/trace off", "折叠执行轨迹（默认）", "fold execution trace (default)", "session"),
    CommandSpec("lang", "/lang zh|en", "切换界面语言", "switch interface language", "session"),
    CommandSpec("clear", "/clear", "清空会话上下文", "clear conversation context", "session"),
    CommandSpec("compact", "/compact", "压缩较早的会话上下文", "compact older context", "session"),
    CommandSpec("selfcheck", "/selfcheck", "运行项目自检", "run self-check", "session"),
    CommandSpec("tools", "/tools", "列出已注册工具", "list registered tools", "session"),
    CommandSpec("sources", "/sources", "查看数据源状态", "show data-source status", "session"),
    CommandSpec("skills", "/skills", "查看可用 Skill", "list available skills", "session"),
    CommandSpec("mcp", "/mcp", "查看 MCP 服务器和工具", "show MCP servers and tools", "session"),
    CommandSpec("security", "/security", "查看安全策略", "show safety policy", "session"),
    CommandSpec("exit", "/exit", "退出交互会话", "exit interactive session", "session"),
    CommandSpec("resolve", "/resolve Apple", "解析公司名和股票代码", "resolve company or ticker", "research"),
    CommandSpec("quote", "/quote AAPL", "查询行情快照", "get quote snapshot", "research"),
    CommandSpec("quality", "/quality AAPL 1y", "运行研究质量门禁", "run research quality gate", "research"),
    CommandSpec("history", "/history AAPL 1y", "查看历史行情和指标", "show history and indicators", "research"),
    CommandSpec("financials", "/financials AAPL", "查看基本面", "show fundamentals", "research"),
    CommandSpec("news", "/news AAPL 5", "查看相关新闻", "show related news", "research"),
    CommandSpec("indicators", "/indicators AAPL 1y", "计算技术指标", "calculate indicators", "research"),
    CommandSpec("report", "/report AAPL 1y", "生成股票研究报告", "generate research report", "research"),
    CommandSpec("export-report", "/export-report AAPL 3mo reports/aapl.md", "导出 Markdown 报告", "export Markdown report", "research"),
    CommandSpec("compare", "/compare NVDA AMD 1y", "比较多只股票", "compare stocks", "research"),
    CommandSpec("debate", "/debate NVDA AMD 1y", "运行多视角审查", "run multi-perspective review", "research"),
    CommandSpec("backtest", "/backtest TSLA 20 60 2y", "回测均线策略", "backtest moving-average strategy", "research"),
    CommandSpec("brief", "/brief AAPL MSFT NVDA", "生成自选股简报", "generate watchlist brief", "research"),
    CommandSpec("search", "/search Apple AAPL stock", "搜索公开来源", "search public sources", "research"),
    CommandSpec("fetch", "/fetch https://example.com", "抓取并核验网页", "fetch and inspect a page", "research"),
    CommandSpec("proxy", "/proxy status|test|set|off", "管理查询代理", "manage query proxy", "workflow"),
    CommandSpec("wechat", "/wechat status|send|send-md", "管理微信连接", "manage WeChat connector", "workflow"),
    CommandSpec("remember", "/remember <长期项目约定>", "保存或查看跨会话项目记忆", "save or show persistent project memory", "workflow"),
    CommandSpec("memory", "/memory list|add", "查看或新增研究记忆", "list or add research memory", "workflow"),
    CommandSpec("evolve", "/evolve <复盘>", "沉淀研究经验", "save research learning", "workflow"),
    CommandSpec("predict", "/predict record|list|eval|learn", "记录和评估预测", "record and score predictions", "workflow"),
    CommandSpec("portfolio", "/portfolio init|status|review|mark|sell|trades|pnl|rebalance", "管理纸面组合", "manage paper portfolio", "workflow"),
    CommandSpec("learn-history", "/learn-history AAPL 2y 20", "从历史行情学习规则", "learn rules from history", "workflow"),
    CommandSpec("schedule", "/schedule list|brief|portfolio|run", "管理本地定时任务", "manage local schedules", "workflow"),
)


def command_specs() -> list[CommandSpec]:
    return list(_COMMANDS)


def completion_items(extra: Iterable[CompletionItem | str] = ()) -> list[CompletionItem]:
    configured_lang = os.environ.get(
        "TICKER_DOSSIER_LANG",
        os.environ.get("FINANCE_AGENT_LANG", "zh"),
    )
    lang = "en" if configured_lang.lower().startswith("en") else "zh"
    rows = [
        CompletionItem(
            spec.usage,
            spec.description_en if lang == "en" else spec.description_zh,
            "builtin",
        )
        for spec in _COMMANDS
    ]
    for item in extra:
        if isinstance(item, CompletionItem):
            rows.append(item)
        elif str(item).strip():
            rows.append(CompletionItem(str(item).strip(), kind="dynamic"))
    by_text: dict[str, CompletionItem] = {}
    for row in rows:
        by_text.setdefault(row.text, row)
    return list(by_text.values())


def command_completions(extra: Iterable[CompletionItem | str] = ()) -> list[str]:
    """Return stable, de-duplicated completions from the shared catalog."""
    return [item.text for item in completion_items(extra)]


def completion_meta(extra: Iterable[CompletionItem | str] = ()) -> dict[str, str]:
    return {
        item.text: f"[{item.kind}] {item.description}".rstrip()
        for item in completion_items(extra)
    }


def specs_by_category() -> dict[str, list[CommandSpec]]:
    grouped: dict[str, list[CommandSpec]] = {"session": [], "research": [], "workflow": []}
    for spec in _COMMANDS:
        grouped.setdefault(spec.category, []).append(spec)
    return grouped
