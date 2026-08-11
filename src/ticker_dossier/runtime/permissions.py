"""Tool permission policy for the agent runtime."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ticker_dossier.security import SecurityError, guard_shell


READONLY = {"read", "grep", "glob", "read_skill", "web_search"}
WRITE = {"write", "edit", "trace2skill_generate"}
FIXED_SAFE_WRITES = {"remember", "memory_set", "memory_forget", "task_list"}
SAFE_MCP_TOOLS = {"mcp__finance__risk_budget"}
EXEC = {"bash", "web_fetch"}
AUTO_FINANCE_PREFIXES = (
    "finance_",
    "prediction_",
    "schedule_list",
    "wechat_status",
)


def check(tool: str, args: dict[str, Any], workdir: Path) -> str:
    """Return ``allow``, ``confirm`` or ``deny`` for one tool call."""
    if tool == "wechat_send":
        return "allow" if _wechat_delivery_is_local() else "confirm"
    if tool in FIXED_SAFE_WRITES:
        return "allow"
    if tool in SAFE_MCP_TOOLS:
        return "allow"
    if tool in READONLY or tool.startswith(AUTO_FINANCE_PREFIXES):
        return "allow"
    if tool in WRITE:
        path = _write_path_for(tool, args)
        if path is None:
            return "confirm"
        return "allow" if _inside(path, workdir) else "deny"
    if tool == "bash":
        try:
            parts = guard_shell(str(args.get("command") or ""))
        except SecurityError:
            return "deny"
        if _runs_python_code(parts):
            return "confirm"
        return "allow"
    if tool in EXEC or tool.startswith("mcp__"):
        return "confirm"
    return "confirm"


def denial_message(tool: str, args: dict[str, Any], workdir: Path) -> str:
    target = _write_path_for(tool, args)
    if target is not None and not _inside(target, workdir):
        return f"[权限层] 拒绝：{tool} 试图写入工作目录外路径 {target}"
    return f"[权限层] 拒绝：{tool}({args}) 不符合当前安全策略。"


def confirmation_message(tool: str, args: dict[str, Any]) -> str:
    return f"[权限层] 需确认：{tool}({args}) —— 已拦截（演示默认不放行）。"


def can_auto_approve(tool: str, args: dict[str, Any]) -> bool:
    """Limit the CLI convenience switch to reviewed local Python entry points."""
    if tool != "bash":
        return False
    try:
        parts = guard_shell(str(args.get("command") or ""))
    except SecurityError:
        return False
    return _runs_python_code(parts)


def _write_path_for(tool: str, args: dict[str, Any]) -> Path | None:
    raw = args.get("path")
    if raw is None:
        raw = args.get("output_path") or args.get("file")
    if raw is None:
        return None
    return Path(str(raw)).expanduser().resolve()


def _inside(path: Path, workdir: Path) -> bool:
    try:
        path.relative_to(workdir.resolve())
        return True
    except ValueError:
        return False


def _wechat_delivery_is_local() -> bool:
    mode = os.environ.get("FINANCE_WECHAT_MODE", "").strip().lower()
    if mode in {"dry-run", "dryrun", "file"}:
        return True
    if mode in {"webhook", "relay"}:
        return False
    return not (
        os.environ.get("FINANCE_WECHAT_WEBHOOK", "").strip()
        or os.environ.get("FINANCE_WECHAT_RELAY_URL", "").strip()
    )


def _runs_python_code(parts: list[str]) -> bool:
    if len(parts) < 2 or Path(parts[0]).name not in {"python", "python3"}:
        return False
    return parts[1] not in {"--version", "-V"}
