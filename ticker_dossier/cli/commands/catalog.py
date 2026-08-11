"""Single source of truth for slash-command discovery, help, and completion."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class CommandCompletion:
    usage: str
    description_zh: str
    description_en: str


@dataclass(frozen=True)
class CommandSpec:
    name: str
    usage: str
    description_zh: str
    description_en: str
    category: str
    handler_key: str
    aliases: tuple[str, ...] = ()
    completion_variants: tuple[CommandCompletion, ...] = ()
    include_in_completion: bool = True

    @property
    def command(self) -> str:
        return f"/{self.name}"

    @property
    def all_names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


@dataclass(frozen=True)
class CompletionItem:
    """One row in the shared slash-command completion menu."""

    text: str
    description: str = ""
    kind: str = "builtin"


_COMMANDS = (
    CommandSpec("help", "/help", "查看命令菜单", "show command menu", "session", "session.help"),
    CommandSpec("status", "/status", "查看模型、数据、Skill 和 MCP 状态", "show runtime status", "session", "session.status"),
    CommandSpec(
        "dashboard",
        "/dashboard",
        "查看只读运行状态与纸面持仓仪表盘（可用 --account name）",
        "show a read-only dashboard (optional: --account name)",
        "session",
        "session.dashboard",
    ),
    CommandSpec(
        "trace",
        "/trace",
        "展开或切换执行轨迹",
        "show or toggle execution trace",
        "session",
        "session.trace",
        completion_variants=(
            CommandCompletion("/trace on", "实时显示全部执行轨迹", "show full execution trace"),
            CommandCompletion("/trace off", "折叠执行轨迹（默认）", "fold execution trace (default)"),
        ),
    ),
    CommandSpec("think", "/think on|compact|off", "切换思考过程显示", "toggle reasoning display", "session", "session.think", include_in_completion=False),
    CommandSpec("lang", "/lang zh|en", "切换界面语言", "switch interface language", "session", "session.lang"),
    CommandSpec("clear", "/clear", "清空会话上下文", "clear conversation context", "session", "session.clear"),
    CommandSpec("compact", "/compact", "压缩较早的会话上下文", "compact older context", "session", "session.compact"),
    CommandSpec("selfcheck", "/selfcheck", "运行项目自检", "run self-check", "session", "session.selfcheck"),
    CommandSpec("tools", "/tools", "列出已注册工具", "list registered tools", "session", "session.tools"),
    CommandSpec("skills", "/skills", "查看可用 Skill", "list available skills", "session", "session.skills"),
    CommandSpec("security", "/security", "查看安全策略", "show safety policy", "session", "session.security"),
    CommandSpec("exit", "/exit", "退出交互会话", "exit interactive session", "session", "session.exit", ("quit",)),
    CommandSpec("resolve", "/resolve Apple", "解析公司名和股票代码", "resolve company or ticker", "research", "research.resolve"),
    CommandSpec("quote", "/quote AAPL", "查询行情快照", "get quote snapshot", "research", "research.quote"),
    CommandSpec("quality", "/quality AAPL 1y", "运行研究质量门禁", "run research quality gate", "research", "research.quality"),
    CommandSpec("history", "/history AAPL 1y", "查看历史行情和指标", "show history and indicators", "research", "research.history"),
    CommandSpec("financials", "/financials AAPL", "查看基本面", "show fundamentals", "research", "research.financials"),
    CommandSpec("news", "/news AAPL 5", "查看相关新闻", "show related news", "research", "research.news"),
    CommandSpec("indicators", "/indicators AAPL 1y", "计算技术指标", "calculate indicators", "research", "research.indicators"),
    CommandSpec("report", "/report AAPL 1y", "生成股票研究报告", "generate research report", "research", "research.report"),
    CommandSpec("export-report", "/export-report AAPL 3mo reports/aapl.md", "导出 Markdown 报告", "export Markdown report", "research", "research.export_report"),
    CommandSpec("compare", "/compare NVDA AMD 1y", "比较多只股票", "compare stocks", "research", "research.compare"),
    CommandSpec("debate", "/debate NVDA AMD 1y", "运行多视角审查", "run multi-perspective review", "research", "research.debate"),
    CommandSpec("backtest", "/backtest TSLA 20 60 2y", "回测均线策略", "backtest moving-average strategy", "research", "research.backtest"),
    CommandSpec("brief", "/brief AAPL MSFT NVDA", "生成自选股简报", "generate watchlist brief", "research", "research.brief"),
    CommandSpec("sources", "/sources", "查看数据源状态", "show data-source status", "integrations", "integrations.sources"),
    CommandSpec("mcp", "/mcp", "查看 MCP 服务器和工具", "show MCP servers and tools", "integrations", "integrations.mcp"),
    CommandSpec("search", "/search Apple AAPL stock", "搜索公开来源", "search public sources", "integrations", "integrations.search"),
    CommandSpec("fetch", "/fetch https://example.com", "抓取并核验网页", "fetch and inspect a page", "integrations", "integrations.fetch"),
    CommandSpec("proxy", "/proxy status|test|set|off", "管理查询代理", "manage query proxy", "integrations", "integrations.proxy"),
    CommandSpec("wechat", "/wechat status|send|send-md", "管理微信连接", "manage WeChat connector", "integrations", "integrations.wechat"),
    CommandSpec("portfolio", "/portfolio init|status|review|mark|sell|trades|pnl|rebalance|locate|migrate [--account name]", "管理纸面组合", "manage paper portfolio", "portfolio", "portfolio.manage"),
    CommandSpec("remember", "/remember <长期项目约定>", "保存或查看跨会话项目记忆", "save or show persistent project memory", "workflow", "workflow.remember"),
    CommandSpec("memory", "/memory list|add", "查看或新增研究记忆", "list or add research memory", "workflow", "workflow.memory"),
    CommandSpec("evolve", "/evolve <复盘>", "沉淀研究经验", "save research learning", "workflow", "workflow.evolve"),
    CommandSpec("predict", "/predict record|list|eval|learn", "记录和评估预测", "record and score predictions", "workflow", "workflow.predict"),
    CommandSpec("learn-history", "/learn-history AAPL 2y 20", "从历史行情学习规则", "learn rules from history", "workflow", "workflow.learn_history", ("learn",)),
    CommandSpec("schedule", "/schedule list|brief|portfolio|run", "管理本地定时任务", "manage local schedules", "workflow", "workflow.schedule"),
)


def _normalize_name(value: str) -> str:
    return value.strip().lower().removeprefix("/")


def _build_index() -> dict[str, CommandSpec]:
    index: dict[str, CommandSpec] = {}
    for spec in _COMMANDS:
        if not spec.handler_key.strip():
            raise ValueError(f"command '{spec.name}' has no handler_key")
        if _normalize_name(spec.name) != spec.name:
            raise ValueError(f"command name must be normalized: {spec.name}")
        for name in spec.all_names:
            normalized = _normalize_name(name)
            if not normalized or normalized in index:
                raise ValueError(f"duplicate or empty command name/alias: {name}")
            index[normalized] = spec
    return index


_COMMAND_INDEX = _build_index()


def command_specs() -> list[CommandSpec]:
    return list(_COMMANDS)


def command_names(*, include_aliases: bool = True) -> tuple[str, ...]:
    if include_aliases:
        return tuple(_COMMAND_INDEX)
    return tuple(spec.name for spec in _COMMANDS)


def resolve_command(value: str) -> CommandSpec | None:
    return _COMMAND_INDEX.get(_normalize_name(value))


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
        if spec.include_in_completion
    ]
    rows.extend(
        CompletionItem(
            variant.usage,
            variant.description_en if lang == "en" else variant.description_zh,
            "builtin",
        )
        for spec in _COMMANDS
        for variant in spec.completion_variants
    )
    rows.extend(
        CompletionItem(
            f"/{alias}",
            f"alias for /{spec.name}",
            "alias",
        )
        for spec in _COMMANDS
        for alias in spec.aliases
    )
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
    grouped: dict[str, list[CommandSpec]] = {
        "session": [],
        "research": [],
        "integrations": [],
        "portfolio": [],
        "workflow": [],
    }
    for spec in _COMMANDS:
        grouped.setdefault(spec.category, []).append(spec)
    return grouped
