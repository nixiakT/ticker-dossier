from __future__ import annotations

from ticker_dossier.cli.main import TracePrinter
from ticker_dossier.telemetry import add_usage, format_usage, normalize_usage


def test_normalize_usage_and_optional_cost(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("DEEPSEEK_INPUT_PRICE_PER_MILLION", "1")
    monkeypatch.setenv("DEEPSEEK_OUTPUT_PRICE_PER_MILLION", "2")
    usage = normalize_usage({
        "prompt_tokens": 1000,
        "completion_tokens": 500,
        "total_tokens": 1500,
        "prompt_tokens_details": {"cached_tokens": 200},
    })
    assert usage["cache_hit_tokens"] == 200
    assert usage["estimated_cost_usd"] == 0.002
    assert "tokens 1,500" in format_usage(usage)


def test_add_usage_accumulates_model_turns() -> None:
    total: dict[str, int | float] = {}
    add_usage(total, {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12})
    add_usage(total, {"prompt_tokens": 20, "completion_tokens": 4, "total_tokens": 24})
    assert total == {"prompt_tokens": 30, "completion_tokens": 6, "total_tokens": 36}


def test_trace_summary_and_details_include_usage(capsys) -> None:  # noqa: ANN001
    printer = TracePrinter(lambda: "compact")
    printer.observe("model_start", {"turn": 1})
    printer.observe("model_end", {
        "turn": 1,
        "tool_calls": [],
        "content_preview": "done",
        "usage": {"prompt_tokens": 80, "completion_tokens": 20, "total_tokens": 100},
    })
    printer.flush()
    output = capsys.readouterr().out
    assert "tokens 100 (in 80 / out 20)" in output
    assert "model usage" in printer.render_details()
