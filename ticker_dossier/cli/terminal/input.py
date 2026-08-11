"""Interactive terminal input helpers."""
from __future__ import annotations

import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from ticker_dossier.cli.commands.catalog import command_completions, completion_meta


MAX_COMPLETION_ROWS = 8
COMPLETION_FOOTER = "↑/↓ 移动 · Tab/Enter 选中 · Esc 关闭"


@dataclass(frozen=True)
class SlashCompletionItem:
    label: str
    hint: str = ""


class SlashCompletionPanel:
    """Reasonix-style slash completion state, independent of terminal rendering."""

    def __init__(self, items: Callable[[], list[SlashCompletionItem]]):
        self._items = items
        self.active = False
        self.items: list[SlashCompletionItem] = []
        self.selected = 0
        self._dismissed_text: str | None = None

    def update(self, text: str, cursor_position: int | None = None) -> None:
        cursor_position = len(text) if cursor_position is None else cursor_position
        if self._dismissed_text is not None:
            if text == self._dismissed_text:
                self._clear()
                return
            self._dismissed_text = None
        if cursor_position != len(text) or not text.startswith("/") or any(char.isspace() for char in text):
            self._clear()
            return

        ranked = _rank_slash_items(self._items(), text)
        if not ranked:
            self._clear()
            return
        previous = self.selected
        self.active = True
        self.items = ranked
        self.selected = previous if previous < len(ranked) else 0

    def move(self, delta: int) -> None:
        if self.active and self.items:
            self.selected = (self.selected + delta) % len(self.items)

    def accept(self) -> str | None:
        current = self.current
        if current is None:
            return None
        self._dismissed_text = current.label
        self._clear()
        return current.label

    def dismiss(self, text: str) -> None:
        self._dismissed_text = text
        self._clear()

    @property
    def current(self) -> SlashCompletionItem | None:
        if not self.active or not self.items:
            return None
        return self.items[self.selected]

    def visible_items(self) -> list[tuple[int, SlashCompletionItem]]:
        if not self.active:
            return []
        start = max(self.selected - MAX_COMPLETION_ROWS // 2, 0)
        start = min(start, max(len(self.items) - MAX_COMPLETION_ROWS, 0))
        end = min(start + MAX_COMPLETION_ROWS, len(self.items))
        return list(enumerate(self.items[start:end], start=start))

    def render(self, width: int) -> list[tuple[str, str]]:
        """Return prompt_toolkit fragments for a pinned, full-width menu."""
        visible = self.visible_items()
        if not visible:
            return []
        width = max(width, 12)
        label_width = min(
            max((_cell_width(item.label) for _, item in visible), default=8),
            max(width // 2, 8),
            48,
        )
        fragments: list[tuple[str, str]] = []
        for row_index, item in visible:
            prefix = "› " if row_index == self.selected else "  "
            label = _fit_cells(item.label, label_width)
            label += " " * max(label_width - _cell_width(label), 0)
            hint = _sanitize_hint(re.sub(r"^\[[^]]+\]\s*", "", item.hint))
            line = prefix + label
            remaining = width - _cell_width(line)
            if hint and remaining > 3:
                rendered_hint = _fit_cells(hint, remaining - 2)
                line += "  " + rendered_hint
            line = _pad_cells(_fit_cells(line, width), width)
            style = (
                "bg:#334155 #ffffff bold"
                if row_index == self.selected
                else "bg:#111827 #e5e7eb"
            )
            fragments.extend([(style, line), ("", "\n")])

        footer = f"{len(self.items)} 个匹配 · {COMPLETION_FOOTER}"
        fragments.append(("bg:#111827 #94a3b8 italic", _pad_cells(_fit_cells(footer, width), width)))
        return fragments

    def _clear(self) -> None:
        self.active = False
        self.items = []
        self.selected = 0


class InteractiveInput:
    def __init__(
        self,
        prompt: str,
        commands: Iterable[str] | None = None,
        command_metadata: dict[str, str] | None = None,
        completion_refresh: Callable[[], tuple[list[str], dict[str, str]]] | None = None,
        bottom_toolbar: Callable[[], str] | None = None,
        *,
        input_stream: Any | None = None,
        output_stream: Any | None = None,
        history_path: str | Path | None = None,
    ):
        self.prompt = prompt
        self.commands = list(commands) if commands is not None else command_completions()
        self.command_metadata = command_metadata if command_metadata is not None else completion_meta()
        self.completion_refresh = completion_refresh
        self.bottom_toolbar = bottom_toolbar
        self.input_stream = input_stream
        self.output_stream = output_stream
        self.history_path = Path(history_path) if history_path is not None else Path.home() / ".finance_agent_history"
        self._completion_cache = self._items_from_current_commands()
        self._panel = SlashCompletionPanel(self._completion_items)
        self._last_panel_input: tuple[str, int] | None = None
        self._session = self._build_prompt_session()
        self._setup_readline_fallback()

    def read(self) -> str:
        if self._session is not None:
            return str(self._session.prompt())
        return input(self.prompt)

    def _build_prompt_session(self):
        if self.input_stream is None and not sys.stdin.isatty():
            return None
        try:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.document import Document
            from prompt_toolkit.filters import Condition, is_done
            from prompt_toolkit.formatted_text import ANSI
            from prompt_toolkit.history import FileHistory
            from prompt_toolkit.key_binding import KeyBindings
            from prompt_toolkit.keys import Keys
            from prompt_toolkit.layout import ConditionalContainer, Dimension, HSplit, Layout, Window
            from prompt_toolkit.layout.controls import FormattedTextControl
        except Exception:
            return None

        panel = self._panel
        bindings = KeyBindings()
        panel_active = Condition(lambda: panel.active)

        def set_buffer_text(event, text: str) -> None:  # noqa: ANN001
            event.current_buffer.document = Document(text=text, cursor_position=len(text))
            event.app.invalidate()

        def accept_selected(event) -> None:  # noqa: ANN001
            selected = panel.accept()
            if selected is not None:
                set_buffer_text(event, selected)

        @bindings.add(Keys.ControlJ)
        def _(event):  # noqa: ANN001
            event.current_buffer.insert_text("\n")

        @bindings.add(Keys.Up, filter=panel_active, eager=True)
        def _(event):  # noqa: ANN001
            if _is_history_line(event.current_buffer):
                panel.dismiss(event.current_buffer.text)
                event.current_buffer.history_backward()
            else:
                panel.move(-1)
            event.app.invalidate()

        @bindings.add(Keys.Down, filter=panel_active, eager=True)
        def _(event):  # noqa: ANN001
            if _is_history_line(event.current_buffer):
                panel.dismiss(event.current_buffer.text)
                event.current_buffer.history_forward()
            else:
                panel.move(1)
            event.app.invalidate()

        @bindings.add(Keys.ControlI, filter=panel_active, eager=True)
        def _(event):  # noqa: ANN001
            accept_selected(event)

        @bindings.add(Keys.ControlM, filter=panel_active, eager=True)
        def _(event):  # noqa: ANN001
            selected = panel.current
            if selected is not None and event.current_buffer.text.strip() == selected.label.strip():
                panel.dismiss(event.current_buffer.text)
                event.current_buffer.validate_and_handle()
            else:
                accept_selected(event)

        @bindings.add(Keys.Escape, filter=panel_active, eager=True)
        def _(event):  # noqa: ANN001
            panel.dismiss(event.current_buffer.text)
            event.app.invalidate()

        session = PromptSession(
            message=ANSI(self.prompt),
            history=FileHistory(str(self.history_path)),
            complete_while_typing=False,
            multiline=False,
            key_bindings=bindings,
            enable_history_search=True,
            bottom_toolbar=self.bottom_toolbar,
            input=self.input_stream,
            output=self.output_stream,
        )

        def refresh_panel(buffer) -> None:  # noqa: ANN001
            current_input = (buffer.text, buffer.cursor_position)
            if current_input == self._last_panel_input:
                return
            previous_text = self._last_panel_input[0] if self._last_panel_input is not None else ""
            self._last_panel_input = current_input
            if buffer.text.startswith("/") and not previous_text.startswith("/"):
                self._refresh_completion_cache()
            panel.update(buffer.text, buffer.cursor_position)
            session.app.invalidate()

        session.default_buffer.on_text_changed += refresh_panel
        session.default_buffer.on_cursor_position_changed += refresh_panel

        panel_window = ConditionalContainer(
            Window(
                FormattedTextControl(
                    lambda: panel.render(session.app.output.get_size().columns),
                    focusable=False,
                ),
                height=Dimension(max=MAX_COMPLETION_ROWS + 1),
                dont_extend_height=True,
                wrap_lines=False,
            ),
            filter=panel_active & ~is_done,
        )
        pinned_layout = Layout(
            HSplit([panel_window, session.layout.container]),
            focused_element=session.default_buffer,
        )
        session.layout = pinned_layout
        session.app.layout = pinned_layout
        return session

    def _completion_words(self) -> list[str]:
        if self.completion_refresh is not None:
            commands, metadata = self.completion_refresh()
            self.commands = list(commands)
            self.command_metadata.clear()
            self.command_metadata.update(metadata)
        return self.commands

    def _completion_items(self) -> list[SlashCompletionItem]:
        return list(self._completion_cache)

    def _refresh_completion_cache(self) -> None:
        self._completion_words()
        self._completion_cache = self._items_from_current_commands()

    def _items_from_current_commands(self) -> list[SlashCompletionItem]:
        return [
            SlashCompletionItem(command, self.command_metadata.get(command, ""))
            for command in self.commands
        ]

    def _setup_readline_fallback(self) -> None:
        if self._session is not None or not sys.stdin.isatty():
            return
        try:
            import readline
        except Exception:
            return
        readline.parse_and_bind("tab: complete")
        readline.parse_and_bind("set editing-mode emacs")
        try:
            self.history_path.touch(exist_ok=True)
            readline.read_history_file(str(self.history_path))
        except OSError:
            return
        import atexit

        atexit.register(readline.write_history_file, str(self.history_path))


def _rank_slash_items(items: list[SlashCompletionItem], query: str) -> list[SlashCompletionItem]:
    lowered = query.lower()
    prefix: list[SlashCompletionItem] = []
    fuzzy: list[SlashCompletionItem] = []
    for item in items:
        label = item.label.lower()
        if label.startswith(lowered):
            prefix.append(item)
        elif _subsequence(lowered, label):
            fuzzy.append(item)
    return [*prefix, *fuzzy]


def _is_history_line(buffer: Any) -> bool:
    working_lines = getattr(buffer, "_working_lines", ())
    return bool(working_lines) and buffer.working_index < len(working_lines) - 1


def _subsequence(query: str, value: str) -> bool:
    iterator = iter(value)
    return all(any(candidate == char for candidate in iterator) for char in query)


def _cell_width(text: str) -> int:
    width = 0
    for char in text:
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
    return width


def _sanitize_hint(text: str) -> str:
    """Keep external Skill/MCP descriptions from controlling the terminal."""
    safe = "".join(
        " " if unicodedata.category(char) in {"Cc", "Cf"} else char
        for char in text
    )
    return " ".join(safe.split())


def _fit_cells(text: str, width: int) -> str:
    if width <= 0:
        return ""
    result: list[str] = []
    used = 0
    for char in text:
        char_width = _cell_width(char)
        if used + char_width > width:
            break
        result.append(char)
        used += char_width
    if len(result) < len(text) and width >= 1:
        while result and used + 1 > width:
            removed = result.pop()
            used -= _cell_width(removed)
        result.append("…")
    return "".join(result)


def _pad_cells(text: str, width: int) -> str:
    return text + "\u00a0" * max(width - _cell_width(text), 0)


def clean_user_input(raw: str) -> str:
    """Remove accidentally pasted CLI prompt prefixes from user input."""
    text = raw.strip()
    pattern = re.compile(
        r"^(?:(?:ticker-dossier|finance-agent)\s*>\s*)+",
        flags=re.IGNORECASE,
    )
    previous = None
    while previous != text:
        previous = text
        text = pattern.sub("", text).strip()
    return text
