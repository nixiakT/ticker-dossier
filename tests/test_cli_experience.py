from __future__ import annotations

import asyncio
import io

import pytest

from ticker_dossier.cli.command_catalog import CompletionItem, command_completions, command_specs, completion_meta
from ticker_dossier.cli.custom_commands import load_custom_commands
from ticker_dossier.cli.dynamic_commands import DynamicSlashCommands
from ticker_dossier.cli.input import (
    MAX_COMPLETION_ROWS,
    InteractiveInput,
    SlashCompletionItem,
    SlashCompletionPanel,
    _cell_width,
    _sanitize_hint,
    clean_user_input,
)
from ticker_dossier.cli.ui import _display_width, render_help, render_status_bar, render_tool_card, render_welcome


def test_command_catalog_drives_missing_completions() -> None:
    completions = command_completions()

    assert "/trace" in completions
    assert "/trace on" in completions
    assert "/trace off" in completions
    assert "/export-report AAPL 3mo reports/aapl.md" in completions
    assert "/think compact" not in completions
    assert "/skills" in completions
    assert len({spec.name for spec in command_specs()}) == len(command_specs())


def test_input_cleaner_accepts_new_and_legacy_prompt_prefixes() -> None:
    assert clean_user_input("ticker-dossier > /status") == "/status"
    assert clean_user_input("finance-agent > /status") == "/status"


def test_interactive_input_uses_catalog_by_default(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(InteractiveInput, "_build_prompt_session", lambda self: None)
    monkeypatch.setattr(InteractiveInput, "_setup_readline_fallback", lambda self: None)

    reader = InteractiveInput("ticker-dossier > ")

    assert reader.commands == command_completions()


def test_reasonix_style_completion_ranks_prefix_first_and_windows_eight_rows() -> None:
    rows = [
        SlashCompletionItem("/trace", "last trace"),
        SlashCompletionItem("/trace off", "fold trace"),
        SlashCompletionItem("/fetch", "fuzzy subsequence match"),
        *[SlashCompletionItem(f"/command-{index}") for index in range(10)],
    ]
    panel = SlashCompletionPanel(lambda: rows)

    panel.update("/tr")

    assert [item.label for item in panel.items] == ["/trace", "/trace off"]
    panel.update("/")
    assert len(panel.visible_items()) == MAX_COMPLETION_ROWS
    panel.move(-1)
    assert panel.selected == len(rows) - 1
    assert len(panel.visible_items()) == MAX_COMPLETION_ROWS


def test_completion_panel_is_safe_and_bounded_in_a_narrow_cjk_terminal() -> None:
    raw_hint = "[skill] 第一行\n第二行\a 不应响铃\u200b"
    panel = SlashCompletionPanel(
        lambda: [
            SlashCompletionItem(
                "/研究-report AAPL",
                raw_hint,
            )
        ]
    )
    panel.update("/")

    rendered = "".join(text for _, text in panel.render(24))
    lines = rendered.splitlines()

    assert "\a" not in rendered
    assert "\u200b" not in rendered
    assert _sanitize_hint(raw_hint.removeprefix("[skill] ")) == "第一行 第二行 不应响铃"
    assert all(_cell_width(line) == 24 for line in lines)


def test_reasonix_style_completion_is_rendered_and_keyboard_driven(tmp_path) -> None:  # noqa: ANN001
    from prompt_toolkit.data_structures import Size
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import ColorDepth
    from prompt_toolkit.output.vt100 import Vt100_Output

    async def scenario() -> None:
        rendered = io.StringIO()
        with create_pipe_input() as pipe_input:
            output = Vt100_Output(
                rendered,
                get_size=lambda: Size(rows=30, columns=120),
                term="xterm-256color",
                default_color_depth=ColorDepth.DEPTH_8_BIT,
                enable_cpr=False,
            )
            reader = InteractiveInput(
                "ticker-dossier > ",
                input_stream=pipe_input,
                output_stream=output,
                history_path=tmp_path / "history",
                bottom_toolbar=lambda: "compact · deepseek-chat",
            )
            assert reader._session is not None
            reader._session.app.ttimeoutlen = 0.05
            task = asyncio.create_task(reader._session.prompt_async())

            pipe_input.send_text("/tr")
            await asyncio.sleep(0.15)

            transcript = rendered.getvalue()
            assert reader._panel.active
            assert "/trace off" in transcript
            assert "折叠执行轨迹" in transcript
            assert "Tab/Enter 选中" in transcript

            pipe_input.send_text("\x1b")
            await asyncio.sleep(0.1)
            assert not reader._panel.active
            assert reader._session.default_buffer.text == "/tr"

            pipe_input.send_text("\x15/tr")
            await asyncio.sleep(0.15)
            pipe_input.send_text("\x1b[B\x1b[B\t")
            await asyncio.sleep(0.15)
            assert reader._session.default_buffer.text == "/trace off"
            assert not reader._panel.active

            pipe_input.send_text("\r")
            assert await asyncio.wait_for(task, timeout=1) == "/trace off"

    asyncio.run(scenario())


def test_completion_keeps_history_navigation_and_refreshes_once_per_slash_entry(tmp_path) -> None:  # noqa: ANN001
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    refresh_calls = 0

    def refresh() -> tuple[list[str], dict[str, str]]:
        nonlocal refresh_calls
        refresh_calls += 1
        return command_completions(), completion_meta()

    async def scenario() -> None:
        with create_pipe_input() as pipe_input:
            reader = InteractiveInput(
                "ticker-dossier > ",
                completion_refresh=refresh,
                input_stream=pipe_input,
                output_stream=DummyOutput(),
                history_path=tmp_path / "history-navigation",
            )
            assert reader._session is not None
            reader._session.history.append_string("/help")
            reader._session.history.append_string("/status")
            task = asyncio.create_task(reader._session.prompt_async())
            await asyncio.sleep(0.05)

            pipe_input.send_text("\x1b[A")
            await asyncio.sleep(0.05)
            assert reader._session.default_buffer.text == "/status"
            assert reader._panel.active

            pipe_input.send_text("\x1b[A")
            await asyncio.sleep(0.05)
            assert reader._session.default_buffer.text == "/help"
            pipe_input.send_text("\x1b[B")
            await asyncio.sleep(0.05)
            assert reader._session.default_buffer.text == "/status"

            pipe_input.send_text("\x15/exit")
            await asyncio.sleep(0.1)
            pipe_input.send_text("\r")
            assert await asyncio.wait_for(task, timeout=1) == "/exit"

    asyncio.run(scenario())
    assert refresh_calls == 2


def test_welcome_adapts_to_small_and_large_terminals(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("NO_COLOR", "1")

    for width in (20, 60, 100):
        output = render_welcome(width=width)
        assert max(_display_width(line) for line in output.splitlines()) <= width
        assert "TickerDossier" in output

    wide = render_welcome(width=100)
    assert "招财进宝符" in wide
    assert "Available Tools" in wide


def test_help_is_catalog_backed_and_compact(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.delenv("TICKER_DOSSIER_LANG", raising=False)
    monkeypatch.setenv("FINANCE_AGENT_LANG", "zh")

    output = render_help()

    assert "/trace" in output
    assert "/export-report" in output
    assert "/skills" in output
    assert len(output.splitlines()) < 80

    narrow = render_help(width=20)
    assert max(_display_width(line) for line in narrow.splitlines()) <= 20

    monkeypatch.setenv("TICKER_DOSSIER_LANG", "en")
    assert "ticker-dossier command menu" in render_help()


def test_status_bar_surfaces_runtime_capabilities() -> None:
    output = render_status_bar(
        mode="compact",
        model="deepseek-chat",
        data_sources=2,
        skills=4,
        mcp="1/2",
    )

    assert "compact" in output
    assert "deepseek-chat" in output
    assert "data 2" in output
    assert "skills 4" in output
    assert "mcp 1/2" in output

    narrow = render_status_bar(
        mode="compact",
        model="deepseek-reasoner-very-long-name",
        data_sources=2,
        skills=4,
        mcp="1/2",
        width=36,
    )
    assert "deep" in narrow
    assert "d2" in narrow and "s4" in narrow and "m1/2" in narrow
    assert _display_width(narrow) <= 36


def test_completion_menu_merges_builtin_custom_skill_and_mcp_prompt() -> None:
    extra = [
        CompletionItem("/review", "review the current stock", "custom"),
        CompletionItem("/finance-history-learning", "historical learning playbook", "skill"),
        CompletionItem("/mcp:research:daily", "daily research prompt", "mcp"),
    ]

    values = command_completions(extra)
    metadata = completion_meta(extra)

    assert "/help" in values
    assert "/review" in values
    assert "/finance-history-learning" in values
    assert "/mcp:research:daily" in values
    assert "custom" in metadata["/review"]
    assert "skill" in metadata["/finance-history-learning"]
    assert "mcp" in metadata["/mcp:research:daily"]


def test_custom_markdown_command_loads_and_substitutes_arguments(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "commands"
    root.mkdir()
    (root / "review.md").write_text(
        "---\ndescription: Review a stock\nargument-hint: <ticker> <period>\n---\n"
        "Review $1 over $2. Full args: $ARGUMENTS. Literal: $$.",
        encoding="utf-8",
    )

    commands = load_custom_commands([root])

    assert [command.name for command in commands] == ["review"]
    assert commands[0].usage == "/review <ticker> <period>"
    assert commands[0].render(["AAPL", "3mo"]) == (
        "Review AAPL over 3mo. Full args: AAPL 3mo. Literal: $."
    )


def test_tool_card_is_bounded_without_repeated_trace_hint(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("NO_COLOR", "1")

    output = render_tool_card(
        "finance_get_news",
        "done",
        "result " * 100,
        elapsed=0.123,
        width=60,
    )

    assert "finance_get_news" in output
    assert "done" in output
    assert "/trace" not in output
    assert len(output.splitlines()) > 3
    assert len(output.splitlines()) <= 7
    assert max(_display_width(line) for line in output.splitlines()) <= 60


def test_dynamic_slash_expands_custom_skill_and_mcp_prompt(tmp_path) -> None:  # noqa: ANN001
    command_root = tmp_path / "commands"
    command_root.mkdir()
    (command_root / "review.md").write_text(
        "---\ndescription: Review stock\n---\nReview $1 carefully.",
        encoding="utf-8",
    )
    skill_root = tmp_path / "skills"
    skill_path = skill_root / "risk" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(
        "---\nname: risk-check\ndescription: Check risks\n---\n\nList disconfirming evidence.",
        encoding="utf-8",
    )

    class Registry:
        def mcp_prompts(self):  # noqa: ANN201
            return [{
                "server": "research",
                "name": "daily",
                "description": "Daily prompt",
                "arguments": [{"name": "ticker", "required": True}],
            }]

        def get_mcp_prompt(self, server, name, arguments):  # noqa: ANN001, ANN201
            assert (server, name, arguments) == ("research", "daily", {"ticker": "AAPL"})
            return {"messages": [{"role": "user", "content": {"type": "text", "text": "Analyze AAPL"}}]}

    dynamic = DynamicSlashCommands(
        Registry(),
        command_roots=[command_root],
        skill_root=skill_root,
    )

    assert dynamic.expand("/review AAPL") == "Review AAPL carefully."
    assert "List disconfirming evidence." in dynamic.expand("/risk-check AAPL")
    assert "Analyze AAPL" in dynamic.expand("/mcp:research:daily AAPL")
    values = [item.text for item in dynamic.completion_items()]
    assert "/review" in values
    assert "/risk-check" in values
    assert "/mcp:research:daily" in values


def test_dynamic_mcp_prompt_failure_is_a_command_error(tmp_path) -> None:  # noqa: ANN001
    class Registry:
        def mcp_prompts(self):  # noqa: ANN201
            return [{"server": "broken", "name": "daily", "arguments": []}]

        def get_mcp_prompt(self, server, name, arguments):  # noqa: ANN001, ANN201
            raise RuntimeError("server died")

    dynamic = DynamicSlashCommands(Registry(), command_roots=[], skill_root=tmp_path)

    with pytest.raises(ValueError, match="server died"):
        dynamic.expand("/mcp:broken:daily")


def test_dynamic_completion_refreshes_new_skills(tmp_path) -> None:  # noqa: ANN001
    skill_root = tmp_path / "skills"
    dynamic = DynamicSlashCommands(type("Registry", (), {"mcp_prompts": lambda self: []})(), command_roots=[], skill_root=skill_root)
    assert dynamic.completion_items() == []

    path = skill_root / "new-skill" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "---\nname: new-skill\ndescription: newly generated\n---\n\nFollow the new workflow.",
        encoding="utf-8",
    )
    dynamic.refresh()

    assert "/new-skill" in [item.text for item in dynamic.completion_items()]
    assert "Follow the new workflow" in dynamic.expand("/new-skill AAPL")
