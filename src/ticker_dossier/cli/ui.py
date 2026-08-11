"""Compact, terminal-width-aware CLI presentation helpers."""
from __future__ import annotations

import os
import shutil
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime

from ticker_dossier.cli.command_catalog import specs_by_category
from ticker_dossier.config import load_local_env


WELCOME_LOGO = [
    ("╭────────── 招财进宝符 ───────────╮", "red"),
    ("│    ◌    ✦     ◌     ✦    ◌      │", "gold"),
    ("│          /\\_______/\\            │", "white"),
    ("│      ___(  ◠   ◠  )___          │", "white"),
    ("│    .'    \\   ᴗ   /    `.        │", "white"),
    ("│   /   ╭─╮\\_____/╭─╮   \\         │", "gold"),
    ("│  |    │ │  ___  │ │    |        │", "white"),
    ("│  |    │ │ (___) │ │    |        │", "white"),
    ("│   \\   ╰─╯\\___/╰─╯   /           │", "white"),
    ("│    `-.    /   \\    .-'          │", "gold"),
    ("│       (  o ) ( o  )             │", "white"),
    ("│      ◢████████████◣             │", "red"),
    ("│     ═══╧════════╧═══            │", "red"),
    ("╰─────────────────────────────────╯", "gold"),
    ("model", "muted"),
    ("{model}", "muted"),
    ("research only", "gold"),
    ("facts · inference · risk", "muted"),
    ("no auto trading", "red"),
]

WELCOME_PANEL_ROWS = [
    ("Available Tools", ""),
    ("finance", "quote, history, financials, news"),
    ("analysis", "indicators, report, compare"),
    ("agents", "debate, risk, value, macro"),
    ("strategy", "backtest, brief, trace2skill"),
    ("web", "search, fetch, source check"),
    ("wechat", "status, send, report outbox"),
    ("memory", "preference, correction, evolve"),
    ("prediction", "ledger, scorecard, review"),
    ("portfolio", "paper account, allocation, PnL"),
    ("learning", "history patterns, skill update"),
    ("schedule", "wechat brief, due runner"),
    ("", ""),
    ("Market Sources", ""),
    ("quotes", "Yahoo Finance, Alpha Vantage"),
    ("A-share", "Tushare, AKShare"),
    ("fallback", "sample data is clearly marked"),
    ("", ""),
    ("Commands", ""),
    ("/help", "menu and examples"),
    ("/status", "model, sources, tools"),
    ("/dashboard", "runtime and saved positions"),
    ("/trace on", "expand execution trace"),
]


def _sanitize_terminal_data(value: object) -> str:
    """Flatten untrusted labels so persisted data cannot control the terminal."""
    safe = "".join(
        " " if unicodedata.category(char) in {"Cc", "Cf", "Cs"} else char
        for char in str(value)
    )
    return " ".join(safe.split())


@dataclass(frozen=True)
class DashboardHolding:
    """One position rendered from the prices already saved in the paper ledger."""

    symbol: str
    shares: float
    avg_cost: float
    last_price: float
    market_value: float
    weight: float
    pnl_pct: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _sanitize_terminal_data(self.symbol))


@dataclass(frozen=True)
class DashboardPortfolio:
    """Read-only paper-account summary for the terminal dashboard."""

    name: str
    state: str
    net_value: float = 0.0
    initial_cash: float = 0.0
    cash: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    updated_at: str = ""
    holdings: tuple[DashboardHolding, ...] = ()
    conflict: bool = False
    warning: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _sanitize_terminal_data(self.name))
        object.__setattr__(self, "state", _sanitize_terminal_data(self.state))
        object.__setattr__(self, "updated_at", _sanitize_terminal_data(self.updated_at))
        object.__setattr__(self, "warning", _sanitize_terminal_data(self.warning))


@dataclass(frozen=True)
class DashboardSnapshot:
    """Cached runtime and account state; rendering never performs I/O."""

    model: str
    backend: str
    trace: str
    tools: int
    skills: int
    data_sources: int
    mcp_connected: int
    mcp_total: int
    portfolio: DashboardPortfolio
    source_names: tuple[str, ...] = ()
    mcp_configured: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "model", _sanitize_terminal_data(self.model))
        object.__setattr__(self, "backend", _sanitize_terminal_data(self.backend))
        object.__setattr__(self, "trace", _sanitize_terminal_data(self.trace))
        object.__setattr__(
            self,
            "source_names",
            tuple(_sanitize_terminal_data(name) for name in self.source_names),
        )


def render_welcome(width: int | None = None) -> str:
    """Render the lucky-cat workspace on wide terminals, compact elsewhere."""
    frame_width = _terminal_width(width)
    if frame_width < 72:
        return _render_compact_welcome(frame_width)

    inner = frame_width - 2
    load_local_env()
    model = os.environ.get("DEEPSEEK_MODEL", "not configured")
    rows = [_frame_title(" TickerDossier · stock research workspace ", inner)]
    logo_rows = [
        _color(_truncate_display(model, 35), color) if text == "{model}" else _color(text, color)
        for text, color in WELCOME_LOGO
    ]
    body_rows = max(len(logo_rows), len(WELCOME_PANEL_ROWS))
    for index in range(body_rows):
        left = logo_rows[index] if index < len(logo_rows) else ""
        label, value = WELCOME_PANEL_ROWS[index] if index < len(WELCOME_PANEL_ROWS) else ("", "")
        rows.append(_welcome_panel_line(left, _welcome_right_cell(label, value), inner))
    rows.append(_welcome_panel_line("", "", inner))
    if current_lang() == "en":
        ask = "Analyze AAPL over the last 3 months"
        hint = "/help commands  ↑/↓ history"
    else:
        ask = "分析一下 AAPL 最近三个月走势"
        hint = "/help 命令  ↑/↓ 历史"
    rows.append(_welcome_panel_line(_color("Ask", "cyan") + "  " + ask, hint, inner))
    rows.append(_color("╰" + "─" * inner + "╯", "gold"))
    return "\n".join(rows)


def _render_compact_welcome(frame_width: int) -> str:
    inner = frame_width - 2
    load_local_env()
    model = os.environ.get("DEEPSEEK_MODEL", "not configured")
    rows = [
        _frame_title(" TickerDossier ", inner),
        _frame_line(f"ฅ^•ﻀ•^ฅ  model {model}", inner),
        _frame_line("research only · no auto trading", inner),
        _frame_line("/help  /dashboard  /trace on", inner),
        _color("╰" + "─" * inner + "╯", "gold"),
    ]
    return "\n".join(rows)


def render_dashboard(snapshot: DashboardSnapshot, width: int | None = None) -> str:
    """Render a responsive, static dashboard from an already collected snapshot."""
    frame_width = _terminal_width(width)
    lang = current_lang()
    boundary = (
        "只读快照 · 使用纸面账本保存价格 · 不联网刷新 · 不执行交易"
        if lang == "zh"
        else "READ ONLY · saved paper-ledger prices · no refresh · no trading"
    )
    rows = _dashboard_panel(" TickerDossier · Dashboard ", [boundary], frame_width)

    runtime_lines = _dashboard_runtime_lines(snapshot, lang)
    portfolio_lines = _dashboard_portfolio_lines(snapshot.portfolio, lang)
    rows.append("")
    if frame_width >= 88:
        gap = 2
        panel_width = (frame_width - gap) // 2
        line_count = max(len(runtime_lines), len(portfolio_lines))
        runtime_lines.extend([""] * (line_count - len(runtime_lines)))
        portfolio_lines.extend([""] * (line_count - len(portfolio_lines)))
        left = _dashboard_panel(_dashboard_runtime_title(lang), runtime_lines, panel_width)
        right = _dashboard_panel(_dashboard_portfolio_title(lang), portfolio_lines, panel_width)
        rows.extend(_join_dashboard_panels(left, right, panel_width, gap))
    else:
        rows.extend(_dashboard_panel(_dashboard_runtime_title(lang), runtime_lines, frame_width))
        rows.append("")
        rows.extend(
            _dashboard_panel(
                _dashboard_portfolio_title(lang),
                portfolio_lines,
                frame_width,
            )
        )

    rows.append("")
    rows.extend(_dashboard_positions_panel(snapshot.portfolio, frame_width, lang))
    if snapshot.portfolio.conflict or snapshot.portfolio.warning:
        rows.append("")
        rows.extend(_dashboard_warning_panel(snapshot.portfolio, frame_width, lang))
    rows.append("")
    hint = (
        "/portfolio review 只读刷新估值 · /portfolio mark 才会写入账本"
        if lang == "zh"
        else "/portfolio review values in memory · /portfolio mark writes the ledger"
    )
    rows.extend(_dashboard_panel(" Quick actions ", [hint], frame_width))
    return "\n".join(rows)


def _dashboard_runtime_lines(snapshot: DashboardSnapshot, lang: str) -> list[str]:
    source_detail = ", ".join(snapshot.source_names) or ("无真实源" if lang == "zh" else "none")
    mcp = f"{snapshot.mcp_connected}/{snapshot.mcp_total}"
    if snapshot.mcp_configured:
        mcp += " · config only" if lang == "en" else " · 仅检查配置"
    if lang == "en":
        return [
            f"Backend   {snapshot.backend}",
            f"Model     {snapshot.model}",
            f"Trace     {snapshot.trace}",
            f"Tools     {snapshot.tools} · Skills {snapshot.skills}",
            f"Data      {snapshot.data_sources} · {source_detail}",
            f"MCP       {mcp}",
        ]
    return [
        f"后端      {snapshot.backend}",
        f"模型      {snapshot.model}",
        f"轨迹      {snapshot.trace}",
        f"工具      {snapshot.tools} · Skills {snapshot.skills}",
        f"数据源    {snapshot.data_sources} · {source_detail}",
        f"MCP       {mcp}",
    ]


def _dashboard_portfolio_lines(portfolio: DashboardPortfolio, lang: str) -> list[str]:
    if portfolio.state == "missing":
        if lang == "en":
            return [
                f"Account   {portfolio.name}",
                "State     not created",
                "Create    /portfolio init --account " + portfolio.name,
            ]
        return [
            f"账户      {portfolio.name}",
            "状态      尚未创建",
            "创建      /portfolio init --account " + portfolio.name,
        ]
    if portfolio.state not in {"ready", "empty"}:
        if lang == "en":
            return [
                f"Account   {portfolio.name}",
                "State     unavailable",
                "Next      /portfolio locate " + portfolio.name,
            ]
        return [
            f"账户      {portfolio.name}",
            "状态      读取失败",
            "下一步    /portfolio locate " + portfolio.name,
        ]

    pnl = _color(_dashboard_signed_money(portfolio.pnl), _dashboard_change_color(portfolio.pnl))
    pnl_pct = _color(
        _dashboard_signed_percent(portfolio.pnl_pct),
        _dashboard_change_color(portfolio.pnl_pct),
    )
    storage = "CONFLICT" if portfolio.conflict else ("warning" if portfolio.warning else "ok")
    if lang == "en":
        return [
            f"Account   {portfolio.name}",
            f"Net value {_dashboard_money(portfolio.net_value)}",
            f"Cash      {_dashboard_money(portfolio.cash)}",
            f"Return    {pnl} · {pnl_pct}",
            f"Updated   {portfolio.updated_at or 'unknown'}",
            f"Storage   {storage}",
        ]
    return [
        f"账户      {portfolio.name}",
        f"净值      {_dashboard_money(portfolio.net_value)}",
        f"现金      {_dashboard_money(portfolio.cash)}",
        f"累计收益  {pnl} · {pnl_pct}",
        f"账本时间  {portfolio.updated_at or '未知'}",
        f"存储状态  {'冲突' if portfolio.conflict else ('警告' if portfolio.warning else '正常')}",
    ]


def _dashboard_positions_panel(
    portfolio: DashboardPortfolio,
    frame_width: int,
    lang: str,
) -> list[str]:
    title = " Saved positions " if lang == "en" else " 持仓 · 账本保存价格 "
    if portfolio.state not in {"ready", "empty"}:
        empty = "No paper account available." if lang == "en" else "没有可展示的纸面账户。"
        return _dashboard_panel(title, [empty], frame_width)
    if not portfolio.holdings:
        empty = "No positions." if lang == "en" else "暂无持仓。"
        return _dashboard_panel(title, [empty], frame_width)

    content_width = max(frame_width - 4, 1)
    lines = _dashboard_position_lines(portfolio.holdings, content_width, lang)
    return _dashboard_panel(title, lines, frame_width)


def _dashboard_position_lines(
    holdings: tuple[DashboardHolding, ...],
    width: int,
    lang: str,
) -> list[str]:
    visible = holdings[:8]
    lines: list[str] = []
    if width >= 83:
        wide_labels = (
            ("Symbol", "Shares", "Avg cost", "Saved", "Value", "Weight", "P/L")
            if lang == "en"
            else ("标的", "股数", "成本", "账本价", "市值", "权重", "浮盈亏")
        )
        wide_widths = (8, 10, 11, 11, 14, 8, 9)
        lines.append(_dashboard_table_row(wide_labels, wide_widths))
        lines.append("─" * width)
        for holding in visible:
            lines.append(_dashboard_holding_row(holding, wide_widths))
    elif width >= 50:
        compact_labels = (
            ("Symbol", "Shares", "Value", "Weight", "P/L")
            if lang == "en"
            else ("标的", "股数", "市值", "权重", "浮盈亏")
        )
        compact_widths = (7, 8, 12, 7, 8)
        lines.append(_dashboard_table_row(compact_labels, compact_widths))
        lines.append("─" * width)
        for holding in visible:
            values = (
                holding.symbol,
                _dashboard_quantity(holding.shares),
                _dashboard_money(holding.market_value),
                f"{holding.weight * 100:.1f}%",
                _color(
                    _dashboard_signed_percent(holding.pnl_pct),
                    _dashboard_change_color(holding.pnl_pct),
                ),
            )
            lines.append(_dashboard_table_row(values, compact_widths))
    else:
        for holding in visible:
            lines.append(
                f"{holding.symbol}  {holding.weight * 100:.1f}%  "
                + _color(
                    _dashboard_signed_percent(holding.pnl_pct),
                    _dashboard_change_color(holding.pnl_pct),
                )
            )
            lines.append(
                f"{_dashboard_quantity(holding.shares)} × "
                f"{_dashboard_money(holding.last_price)} = "
                f"{_dashboard_money(holding.market_value)}"
            )
    if len(holdings) > len(visible):
        remaining = len(holdings) - len(visible)
        lines.append(
            f"… and {remaining} more; use /portfolio status"
            if lang == "en"
            else f"… 另有 {remaining} 个持仓；用 /portfolio status 查看全部"
        )
    return lines


def _dashboard_holding_row(
    holding: DashboardHolding,
    widths: tuple[int, ...],
) -> str:
    values = (
        holding.symbol,
        _dashboard_quantity(holding.shares),
        _dashboard_money(holding.avg_cost),
        _dashboard_money(holding.last_price),
        _dashboard_money(holding.market_value),
        f"{holding.weight * 100:.1f}%",
        _color(
            _dashboard_signed_percent(holding.pnl_pct),
            _dashboard_change_color(holding.pnl_pct),
        ),
    )
    return _dashboard_table_row(values, widths)


def _dashboard_table_row(values: tuple[str, ...], widths: tuple[int, ...]) -> str:
    cells = [
        _pad_display(_truncate_display(value, cell_width), cell_width)
        for value, cell_width in zip(values, widths)
    ]
    return "  ".join(cells).rstrip()


def _dashboard_warning_panel(
    portfolio: DashboardPortfolio,
    frame_width: int,
    lang: str,
) -> list[str]:
    lines: list[str] = []
    if portfolio.conflict:
        lines.append(
            "CONFLICT: user and workspace ledgers both exist; writes remain locked."
            if lang == "en"
            else "冲突：用户级与 workspace 同名账本同时存在；写操作仍保持锁定。"
        )
    if portfolio.warning:
        lines.extend(_wrap_display(portfolio.warning, max(frame_width - 4, 1), max_lines=3))
    lines.append(
        f"Inspect with /portfolio locate {portfolio.name}; dashboard never migrates files."
        if lang == "en"
        else f"用 /portfolio locate {portfolio.name} 核对；dashboard 不会迁移文件。"
    )
    return _dashboard_panel(" Account warning ", lines, frame_width)


def _dashboard_panel(title: str, lines: list[str], width: int) -> list[str]:
    inner = max(width - 2, 1)
    return [
        _frame_title(title, inner),
        *(_frame_line(line, inner) for line in lines),
        _color("╰" + "─" * inner + "╯", "gold"),
    ]


def _join_dashboard_panels(
    left: list[str],
    right: list[str],
    panel_width: int,
    gap: int,
) -> list[str]:
    spacer = " " * gap
    return [
        _pad_display(left_line, panel_width) + spacer + right_line
        for left_line, right_line in zip(left, right)
    ]


def _dashboard_runtime_title(lang: str) -> str:
    return " Runtime " if lang == "en" else " 运行状态 "


def _dashboard_portfolio_title(lang: str) -> str:
    return " Paper portfolio " if lang == "en" else " 纸面组合 "


def _dashboard_money(value: float) -> str:
    return f"{value:,.2f}"


def _dashboard_signed_money(value: float) -> str:
    return f"{value:+,.2f}"


def _dashboard_signed_percent(value: float) -> str:
    return f"{value:+.2f}%"


def _dashboard_quantity(value: float) -> str:
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"


def _dashboard_change_color(value: float) -> str:
    return "green" if value >= 0 else "red"


def render_help(width: int | None = None) -> str:
    """Render concise help from the shared command catalog."""
    lang = current_lang()
    budget = _terminal_width(width)
    labels = {
        "session": ("会话与状态", "Session"),
        "research": ("股票研究", "Research"),
        "integrations": ("数据与集成", "Integrations"),
        "portfolio": ("纸面组合", "Portfolio"),
        "workflow": ("工作流", "Workflow"),
    }
    lines = ["ticker-dossier 功能菜单" if lang == "zh" else "ticker-dossier command menu", ""]
    usage_width = min(42, max(20, budget // 2))
    desc_width = max(budget - usage_width - 4, 12)
    for category, specs in specs_by_category().items():
        zh, en = labels.get(category, (category, category))
        lines.append(f"[{zh if lang == 'zh' else en}]")
        for spec in specs:
            description = spec.description_zh if lang == "zh" else spec.description_en
            if spec.aliases:
                alias_text = ", ".join(f"/{alias}" for alias in spec.aliases)
                description += f" ({'别名' if lang == 'zh' else 'aliases'}: {alias_text})"
            if budget < 44:
                lines.append("  " + _truncate_display(spec.usage, max(budget - 2, 1)))
                lines.append("    " + _truncate_display(description, max(budget - 4, 1)))
            else:
                usage = _truncate_display(spec.usage, usage_width)
                description = _truncate_display(description, desc_width)
                lines.append(f"  {_pad_display(usage, usage_width)}  {description}".rstrip())
        lines.append("")
    lines.append(
        "输入 / 后可模糊补全；↑/↓ 查看历史；/trace 展开上一轮轨迹。"
        if lang == "zh"
        else "Type / for fuzzy completion; use ↑/↓ for history and /trace for the last trace."
    )
    return "\n".join(_truncate_display(line, budget) for line in lines)


def render_status_bar(
    *,
    mode: str,
    model: str,
    data_sources: int,
    skills: int,
    mcp: str,
    width: int | None = None,
) -> str:
    budget = _terminal_width(width)
    if budget < 32:
        fixed = f" {str(mode)[:1]} d{data_sources} s{skills} m{mcp} "
        model_width = max(budget - _display_width(fixed) - 1, 1)
        return _truncate_display(fixed + _truncate_display(str(model), model_width), budget)
    compact = budget < 64
    prefix = f" {mode} · "
    suffix = (
        f" · d{data_sources} · s{skills} · m{mcp} "
        if compact
        else f" · data {data_sources} · skills {skills} · mcp {mcp} "
    )
    model_width = max(budget - _display_width(prefix) - _display_width(suffix), 1)
    return _truncate_display(prefix + _truncate_display(str(model), model_width) + suffix, budget)


def render_prompt() -> str:
    if not sys.stdin.isatty():
        return ""
    return _color("ticker-dossier", "green") + _color(" > ", "muted")


def render_trace(
    event: str,
    detail: str = "",
    *,
    elapsed: float | None = None,
    timestamp: str | None = None,
) -> str:
    clock = timestamp or datetime.now().strftime("%H:%M:%S")
    elapsed_part = "" if elapsed is None else " " + _color("+" + _format_elapsed(elapsed), "gold")
    prefix = f"{_color('thinking', 'muted')} {_color(clock, 'muted')}{elapsed_part}"
    return f"{prefix} · {event}: {_trace_detail(detail)}" if detail else f"{prefix} · {event}"


def render_trace_summary(
    steps: int,
    tools: list[str],
    *,
    elapsed: float | None = None,
    usage: dict[str, int | float] | None = None,
) -> str:
    from ticker_dossier.telemetry import format_usage

    elapsed_part = f" · {_format_elapsed(elapsed)}" if elapsed is not None else ""
    tool_count = len(tools)
    tool_label = "tool" if tool_count == 1 else "tools"
    tool_part = ", ".join(tools[:4])
    if len(tools) > 4:
        tool_part += f", +{len(tools) - 4}"
    if tool_part:
        tool_part = f" · {tool_part}"
    expand = " · /trace"
    usage_part = f" · {format_usage(usage)}" if usage else ""
    return f"{_color('thinking', 'muted')} · completed{elapsed_part} · {steps} steps · {tool_count} {tool_label}{tool_part}{usage_part}{expand}"


def render_thinking_status(
    detail: str,
    *,
    elapsed: float = 0.0,
    frame: str = "·",
    width: int | None = None,
) -> str:
    """Render one transient, width-aware progress line."""
    budget = _terminal_width(width)
    prefix = f"{_color(frame, 'gold')} {_color('thinking', 'muted')} · "
    suffix = f" · {_format_elapsed(elapsed)}"
    detail_width = max(budget - _display_width(prefix) - _display_width(suffix), 1)
    return _truncate_display(prefix + _truncate_display(detail, detail_width) + suffix, budget)


def render_tool_card(
    name: str,
    state: str,
    detail: str = "",
    *,
    elapsed: float | None = None,
    width: int | None = None,
) -> str:
    """Render a bounded tool event without repeating global usage hints."""
    frame_width = _terminal_width(width)
    inner = frame_width - 2
    timing = f" · +{_format_elapsed(elapsed)}" if elapsed is not None else ""
    content_width = max(inner - 2, 1)
    detail_lines = _wrap_display(detail, content_width, max_lines=5)
    return "\n".join([
        _frame_title(f" tool {name} · {state}{timing} ", inner),
        *(_frame_line(line, inner) for line in detail_lines),
        _color("╰" + "─" * inner + "╯", "gold"),
    ])


def current_lang() -> str:
    load_local_env()
    value = os.environ.get(
        "TICKER_DOSSIER_LANG",
        os.environ.get("FINANCE_AGENT_LANG", "zh"),
    ).strip().lower()
    return "en" if value.startswith("en") else "zh"


def _terminal_width(width: int | None = None) -> int:
    detected = shutil.get_terminal_size((82, 24)).columns if width is None else int(width)
    return max(20, min(detected, 120))


def _frame_title(title: str, inner: int) -> str:
    label = _truncate_display(title, inner)
    remaining = max(inner - _display_width(label), 0)
    left = remaining // 2
    right = remaining - left
    return _color("╭" + "─" * left + label + "─" * right + "╮", "gold")


def _frame_line(text: str, inner: int) -> str:
    content_width = max(inner - 2, 0)
    content = _pad_display(_truncate_display(text, content_width), content_width)
    return _color("│", "gold") + " " + content + " " + _color("│", "gold")


def _welcome_panel_line(left: str, right: str, inner: int) -> str:
    left_width = min(35, max(inner // 2 - 1, 1))
    right_width = max(inner - left_width - 2, 1)
    left_text = _pad_display(_truncate_display(left, left_width), left_width)
    right_text = _pad_display(_truncate_display(right, right_width), right_width)
    return _color("│", "gold") + " " + left_text + " " + right_text + _color("│", "gold")


def _welcome_right_cell(label: str, value: str) -> str:
    if not label and not value:
        return ""
    if value:
        return _color(label + ": ", "cyan") + value
    return _color(label, "cyan")


def _color(text: str, name: str) -> str:
    if not _should_color():
        return text
    colors = {
        "gold": "\033[38;5;220m",
        "green": "\033[38;5;82m",
        "cyan": "\033[38;5;80m",
        "muted": "\033[38;5;245m",
        "red": "\033[38;5;203m",
        "white": "\033[38;5;255m",
    }
    return f"{colors.get(name, '')}{text}\033[0m"


def _should_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return sys.stdout.isatty()


def _strip_ansi(text: str) -> str:
    output: list[str] = []
    index = 0
    while index < len(text):
        if text[index:index + 2] == "\033[":
            index += 2
            while index < len(text) and text[index] != "m":
                index += 1
            index += 1
            continue
        output.append(text[index])
        index += 1
    return "".join(output)


def _display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1 for char in _strip_ansi(text))


def _pad_display(text: str, width: int) -> str:
    return text + " " * max(width - _display_width(text), 0)


def _truncate_display(text: str, width: int) -> str:
    clean = _strip_ansi(text)
    if _display_width(clean) <= width:
        return text
    visible = ""
    used = 0
    for char in clean:
        char_width = 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
        if used + char_width > max(width - 1, 0):
            break
        visible += char
        used += char_width
    return visible + "…"


def _wrap_display(text: str, width: int, max_lines: int = 5) -> list[str]:
    """Wrap text by terminal display width and cap noisy tool output."""
    clean = " ".join(str(text).split())
    if not clean:
        return [""]

    lines: list[str] = []
    remaining = clean
    while remaining and len(lines) < max_lines:
        line, remaining = _take_display_line(remaining, width)
        lines.append(line)
    if remaining and lines:
        lines[-1] = _truncate_display(lines[-1].rstrip() + "…", width)
    return lines or [""]


def _take_display_line(text: str, width: int) -> tuple[str, str]:
    used = 0
    split_at = 0
    last_space = -1
    for index, char in enumerate(text):
        char_width = 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
        if used + char_width > width:
            break
        used += char_width
        split_at = index + 1
        if char.isspace():
            last_space = index
    else:
        return text, ""

    if last_space > 0:
        split_at = last_space
    split_at = max(split_at, 1)
    return text[:split_at].rstrip(), text[split_at:].lstrip()


def _format_elapsed(seconds: float) -> str:
    if seconds < 10:
        return f"{seconds:.2f}s"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    return f"{minutes}m{seconds - minutes * 60:.0f}s"


def _trace_detail(detail: str, width: int = 220) -> str:
    return _truncate_display(" ".join(str(detail).split()), width)
