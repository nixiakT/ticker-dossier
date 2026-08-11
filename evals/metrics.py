"""Reusable trajectory metrics for agent evaluation and ablation."""
from __future__ import annotations

import json
import re
from typing import Any, Callable


TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)


def success_rate(records: list[dict[str, Any]], check: Callable[[dict[str, Any]], bool] | None = None) -> float:
    predicate = check or (lambda record: bool(record.get("success")))
    return sum(1 for record in records if predicate(record)) / max(len(records), 1)


def average_steps(records: list[dict[str, Any]]) -> float:
    return sum(len(record.get("steps", [])) for record in records) / max(len(records), 1)


def token_count(record: dict[str, Any]) -> int:
    return sum(
        int(step.get("prompt_tokens", 0)) + int(step.get("completion_tokens", 0))
        for step in record.get("steps", [])
    )


def average_tokens(records: list[dict[str, Any]]) -> float:
    return sum(token_count(record) for record in records) / max(len(records), 1)


def json_valid_rate(records_or_outputs: list[dict[str, Any]] | list[str]) -> float:
    snippets = _tool_call_snippets(records_or_outputs)
    valid = 0
    for snippet in snippets:
        try:
            parsed = json.loads(snippet)
            valid += isinstance(parsed, dict)
        except (TypeError, json.JSONDecodeError):
            pass
    return valid / max(len(snippets), 1)


def tool_choice_accuracy(preds: list[dict], expected_tools: list[str]) -> float:
    correct = sum(1 for pred, expected in zip(preds, expected_tools) if pred.get("name") == expected)
    return correct / max(len(expected_tools), 1)


def arg_accuracy(preds: list[dict], expected_args: list[dict]) -> float:
    correct = 0
    for pred, expected in zip(preds, expected_args):
        actual = pred.get("arguments", {})
        if all(str(actual.get(key)) == str(value) for key, value in expected.items()):
            correct += 1
    return correct / max(len(expected_args), 1)


def _tool_call_snippets(values: list[dict[str, Any]] | list[str]) -> list[str]:
    snippets: list[str] = []
    for value in values:
        if isinstance(value, dict):
            raws = [str(step.get("raw", "")) for step in value.get("steps", [])]
        else:
            raws = [str(value)]
        for raw in raws:
            match = TOOL_CALL_RE.search(raw)
            if match:
                snippets.append(match.group(1))
            elif "<tool_call>" in raw:
                start, end = raw.find("{"), raw.rfind("}")
                snippets.append(raw[start:end + 1] if start >= 0 and end >= start else raw)
    return snippets
