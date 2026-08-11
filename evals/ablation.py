"""Reproducible replay ablation for system policy + planning/error recovery."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evals.metrics import average_steps, average_tokens, json_valid_rate, success_rate


def _step(name: str, args: dict[str, Any], prompt: int, completion: int, *, valid: bool = True) -> dict[str, Any]:
    payload = json.dumps({"name": name, "arguments": args}, ensure_ascii=False)
    raw = f"<tool_call>{payload}</tool_call>" if valid else f"<tool_call>{payload[:-2]}"
    return {
        "tool_calls": [{"name": name, "arguments": args}] if valid else [],
        "raw": raw,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
    }


WITH_POLICY = [
    {"task": "read-config", "success": True, "steps": [_step("read", {"path": "config.json"}, 310, 22)]},
    {"task": "list-dir", "success": True, "steps": [_step("bash", {"command": "ls"}, 300, 18)]},
    {"task": "finance-report", "success": True, "steps": [
        _step("finance_resolve_symbol", {"query": "苹果"}, 360, 28),
        _step("finance_get_quote", {"symbol": "AAPL"}, 410, 32),
        _step("finance_get_financials", {"symbol": "AAPL"}, 430, 35),
    ]},
    {"task": "write-report", "success": True, "steps": [
        _step("read", {"path": "source.md"}, 300, 20),
        _step("write", {"path": "report.md"}, 340, 24),
    ]},
    {"task": "long-plan", "success": True, "steps": [
        _step("task_list", {"action": "add", "items": ["定位", "分析", "输出"]}, 320, 28),
        _step("glob", {"pattern": "**/*.py"}, 350, 30),
        _step("grep", {"pattern": "TODO", "path": "."}, 390, 31),
        _step("task_list", {"action": "complete", "items": ["定位", "分析", "输出"]}, 410, 30),
    ]},
    {"task": "recover-path", "success": True, "steps": [
        _step("read", {"path": "data/missing.csv"}, 300, 22),
        _step("glob", {"pattern": "**/*.csv"}, 330, 25),
        _step("read", {"path": "demo/data.csv"}, 350, 24),
    ]},
]


WITHOUT_POLICY = [
    {"task": "read-config", "success": False, "steps": [_step("read", {"path": "config.json"}, 120, 14, valid=False)]},
    {"task": "list-dir", "success": True, "steps": [_step("bash", {"command": "ls"}, 125, 14)]},
    {"task": "finance-report", "success": False, "steps": [_step("finance_get_quote", {"symbol": "APPLE"}, 150, 16, valid=False)]},
    {"task": "write-report", "success": True, "steps": [_step("write", {"path": "report.md"}, 180, 18)]},
    {"task": "long-plan", "success": False, "steps": [_step("glob", {"pattern": "*.py"}, 145, 15, valid=False)]},
    {"task": "recover-path", "success": False, "steps": [_step("read", {"path": "data/missing.csv"}, 135, 14)]},
]


def summarize(records: list[dict[str, Any]]) -> dict[str, float]:
    return {
        "success_rate": success_rate(records),
        "average_steps": average_steps(records),
        "average_tokens": average_tokens(records),
        "json_valid_rate": json_valid_rate(records),
    }


def render() -> str:
    with_policy = summarize(WITH_POLICY)
    without_policy = summarize(WITHOUT_POLICY)
    lines = [
        "=== Agent replay ablation (n=6 per group) ===",
        _row("完整 system policy", with_policy),
        _row("移除 policy", without_policy),
        f"成功率变化: {without_policy['success_rate']:.2f} -> {with_policy['success_rate']:.2f}",
        "说明: 固定轨迹回放用于验证指标管线，不代表真实模型投资准确率。",
    ]
    return "\n".join(lines)


def write_json(path: str | Path = "artifacts/evals/ablation-results.json") -> Path:
    target = Path(path)
    payload = {
        "method": "deterministic trajectory replay",
        "sample_size_per_group": len(WITH_POLICY),
        "with_policy": summarize(WITH_POLICY),
        "without_policy": summarize(WITHOUT_POLICY),
        "limitations": "Constructed fixed traces validate the metric/replay pipeline; run live eval separately for model claims.",
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def _row(name: str, metrics: dict[str, float]) -> str:
    return (
        f"{name:20s} 成功率={metrics['success_rate']:.2f} "
        f"平均步数={metrics['average_steps']:.2f} "
        f"平均token={metrics['average_tokens']:.0f} "
        f"JSON合法率={metrics['json_valid_rate']:.2f}"
    )


if __name__ == "__main__":
    print(render())
    print(f"结果已写入: {write_json()}")
