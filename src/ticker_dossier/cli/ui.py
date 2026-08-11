"""Compact, terminal-width-aware CLI presentation helpers."""
from __future__ import annotations

import os
import shutil
import sys
import unicodedata
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
    ("/trace on", "expand execution trace"),
]


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
        _frame_line("/help  /status  /trace on", inner),
        _color("╰" + "─" * inner + "╯", "gold"),
    ]
    return "\n".join(rows)


def render_help(width: int | None = None) -> str:
    """Render concise help from the shared command catalog."""
    lang = current_lang()
    budget = _terminal_width(width)
    labels = {
        "session": ("会话与状态", "Session"),
        "research": ("股票研究", "Research"),
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
    usage: dict | None = None,
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
