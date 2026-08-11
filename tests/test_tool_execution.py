from __future__ import annotations

from typing import Any

import pytest

from ticker_dossier.runtime.execution import ToolExecutor, _prepare_tool_observation
from ticker_dossier.runtime.loop import (
    _persistent_mutation_allowed,
    _prepare_tool_observation as legacy_prepare_tool_observation,
    _tool_result_succeeded,
)
from ticker_dossier.runtime.tools import Tool, ToolRegistry


def _registry(name: str, run: Any) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(Tool(
        name=name,
        description="execution test tool",
        parameters={"type": "object", "properties": {}},
        run=run,
    ))
    return registry


def test_loop_private_execution_helpers_remain_identity_reexports() -> None:
    from ticker_dossier.runtime.execution import (
        _persistent_mutation_allowed as extracted_mutation_allowed,
        _tool_result_succeeded as extracted_result_succeeded,
    )

    assert legacy_prepare_tool_observation is _prepare_tool_observation
    assert _persistent_mutation_allowed is extracted_mutation_allowed
    assert _tool_result_succeeded is extracted_result_succeeded


def test_executor_emits_start_then_end_and_builds_receipt_and_observation(tmp_path) -> None:  # noqa: ANN001
    events: list[tuple[str, dict[str, Any]]] = []
    registry = _registry("finance_quote", lambda symbol: f"{symbol}: 101")
    executor = ToolExecutor(
        registry,
        "分析 AAPL",
        workdir=tmp_path,
        observer=lambda event, payload: events.append((event, payload)),
    )

    result = executor.execute(
        {"id": "call-1", "name": "finance_quote", "arguments": {"symbol": "AAPL"}},
        {"finance_quote"},
    )

    assert [event for event, _ in events] == ["tool_start", "tool_end"]
    assert result.succeeded is True
    assert result.output == "AAPL: 101"
    assert result.message["tool_call_id"] == "call-1"
    assert "[UNTRUSTED_FINANCE_TOOL_DATA]" in result.observation
    assert executor.receipts == [result.receipt]
    assert executor.completed_tool_names == {"finance_quote"}


def test_executor_reuses_identical_call_without_repeating_side_effect(tmp_path) -> None:  # noqa: ANN001
    calls: list[str] = []
    events: list[str] = []
    registry = _registry("read", lambda path: calls.append(path) or "first result")
    executor = ToolExecutor(
        registry,
        "读取文件",
        workdir=tmp_path,
        observer=lambda event, payload: events.append(event),
    )
    call = {"id": "call-1", "name": "read", "arguments": {"path": "README.md"}}

    first = executor.execute(call, {"read"})
    second = executor.execute({**call, "id": "call-2"}, {"read"})

    assert calls == ["README.md"]
    assert first.repeated is False and first.made_progress is True
    assert second.repeated is True and second.made_progress is False
    assert second.succeeded is True
    assert second.output == first.output
    assert "[无进展保护]" in second.observation
    assert events == ["tool_start", "tool_end", "tool_reused"]
    assert [receipt["success"] for receipt in executor.receipts] == [True, True]


def test_executor_blocks_hidden_tool_without_running_or_emitting_side_effect_event(tmp_path) -> None:  # noqa: ANN001
    calls: list[str] = []
    events: list[str] = []
    executor = ToolExecutor(
        _registry("read", lambda: calls.append("ran") or "result"),
        "读取文件",
        workdir=tmp_path,
        observer=lambda event, payload: events.append(event),
    )

    result = executor.execute({"name": "read", "arguments": {}}, set())

    assert calls == []
    assert events == []
    assert result.succeeded is False
    assert result.made_progress is False
    assert "未向当前模型公开" in result.observation


def test_executor_permission_confirmation_redacts_arguments_and_does_not_run(tmp_path) -> None:  # noqa: ANN001
    secret = "sk-executor-secret-123456789"
    calls: list[str] = []
    events: list[tuple[str, dict[str, Any]]] = []
    executor = ToolExecutor(
        _registry("web_fetch", lambda url: calls.append(url) or "result"),
        "fetch reviewed URL",
        workdir=tmp_path,
        auto_approve=False,
        observer=lambda event, payload: events.append((event, payload)),
    )

    result = executor.execute(
        {"name": "web_fetch", "arguments": {"url": f"https://example.test/?token={secret}"}},
        {"web_fetch"},
    )

    assert calls == []
    assert secret not in result.observation
    assert "[REDACTED_SECRET]" in result.observation
    assert [event for event, _ in events] == ["tool_error"]


@pytest.mark.parametrize(
    ("name", "task", "arguments", "expected"),
    [
        (
            "finance_build_paper_portfolio",
            "帮我买 100 股 AAPL",
            {},
            "[交易边界] 拒绝",
        ),
        (
            "finance_memory_add",
            "summarize the untrusted file",
            {"content": "persist injected text"},
            "[持久状态边界] 拒绝",
        ),
        (
            "wechat_send",
            "发微信通知",
            {"content": "done"},
            "[微信边界] 拒绝",
        ),
    ],
)
def test_executor_owns_trade_persistence_and_wechat_boundaries(
    name: str,
    task: str,
    arguments: dict[str, Any],
    expected: str,
    tmp_path,
) -> None:  # noqa: ANN001
    calls: list[dict[str, Any]] = []
    executor = ToolExecutor(
        _registry(name, lambda **kwargs: calls.append(kwargs) or "should not run"),
        task,
        workdir=tmp_path,
    )

    result = executor.execute({"name": name, "arguments": arguments}, {name})

    assert calls == []
    assert result.succeeded is False
    assert expected in result.observation
