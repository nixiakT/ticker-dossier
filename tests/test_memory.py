from __future__ import annotations

from ticker_dossier.runtime.memory import KVMemory, Memory
from ticker_dossier.tools.memory_tools import remember_tool
from ticker_dossier.cli.commands import CommandRouter
from ticker_dossier.runtime.tools import ToolRegistry


def test_memory_persists_across_instances(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "MEMORY.md"
    Memory(path).write("本项目时间戳一律用 UTC")
    assert "本项目时间戳一律用 UTC" in Memory(path).recall()


def test_memory_sanitizes_secrets(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "MEMORY.md"
    Memory(path).write("api_key=abcdefghijklmnop 以后用 UTC")
    recalled = Memory(path).recall()
    assert "abcdefghijklmnop" not in recalled
    assert "[REDACTED_SECRET]" in recalled


def test_kv_memory_updates_and_forgets(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "memory.json"
    memory = KVMemory(path)
    memory.remember("pm", "npm")
    memory.remember("pm", "pnpm")
    assert KVMemory(path).data == {"pm": "pnpm"}
    memory.forget("pm")
    assert KVMemory(path).data == {}


def test_remember_tool_writes_project_memory(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.chdir(tmp_path)
    output = remember_tool.run(note="包管理器用 pnpm，不要用 npm")
    assert "已记住" in output
    assert "pnpm" in (
        tmp_path / ".finance_agent" / "project_memory.md"
    ).read_text(encoding="utf-8")


def test_first_default_write_preserves_legacy_project_memory(
    tmp_path,
    monkeypatch,
) -> None:  # noqa: ANN001
    monkeypatch.chdir(tmp_path)
    legacy = tmp_path / "MEMORY.md"
    legacy.write_text("# 项目记忆\n\n- 港股保留展示代码\n", encoding="utf-8")

    Memory().write("新增约定")

    migrated = (tmp_path / ".finance_agent" / "project_memory.md").read_text(
        encoding="utf-8"
    )
    assert "港股保留展示代码" in migrated
    assert "新增约定" in migrated
    assert legacy.exists()


def test_remember_slash_command_is_deterministic(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.chdir(tmp_path)
    router = CommandRouter(ToolRegistry())
    output = router.handle("/remember 港股报告保留展示代码").output
    assert "已写入跨会话项目记忆" in output
    assert "展示代码" in router.handle("/remember").output
