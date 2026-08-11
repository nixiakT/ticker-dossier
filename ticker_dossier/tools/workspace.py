"""Workspace editing, search, glob, and task-list tools."""
from __future__ import annotations

import ast
import fnmatch
import json
import subprocess
from pathlib import Path

from ticker_dossier.runtime.tools import Tool
from ticker_dossier.security import SecurityError, WORKSPACE_ROOT, guard_read, guard_write, resolve_workspace_path


def _edit(path: str, old: str = "", new: str = "") -> str:
    resolved = guard_read(path)
    content = resolved.read_text(encoding="utf-8")
    count = content.count(old)
    if not old:
        raise ValueError("old 不能为空")
    if count == 0:
        raise ValueError("edit 匹配失败：old 文本不存在")
    if count > 1:
        raise ValueError(f"edit 匹配不唯一：old 出现 {count} 次，请提供更精确上下文")
    updated = content.replace(old, new, 1)
    guard_write(path, updated)
    resolved.write_text(updated, encoding="utf-8")
    return f"编辑成功: {resolved} (替换 1 处)"


# --- grep：基于 ripgrep ---
def _grep(pattern: str, path: str = ".") -> str:
    resolved = guard_read(path) if Path(path).expanduser().is_file() else _guard_directory(path)
    try:
        result = subprocess.run(
            [
                "rg", "--line-number", "--with-filename", "--no-heading", "--color", "never",
                "--iglob", "!**/.env*", "--iglob", "!**/.git/**", "--iglob", "!**/.ssh/**",
                "--iglob", "!**/.aws/**", "--iglob", "!**/.config/**", "--iglob", "!**/.docker/**",
                "--iglob", "!**/.claude/**", "--iglob", "!**/.npmrc", "--iglob", "!**/.pypirc",
                "--iglob", "!**/.netrc", "--iglob", "!**/credentials", "--iglob", "!**/id_rsa",
                "--iglob", "!**/id_ed25519", "-e", pattern, "--", str(resolved),
            ],
            cwd=WORKSPACE_ROOT,
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
    except FileNotFoundError:
        return _grep_python(pattern, resolved)
    if result.returncode == 1:
        return "未找到匹配。"
    if result.returncode != 0:
        return f"grep 失败: {result.stderr.strip()}"
    return _truncate(_workspace_relative_grep_output(result.stdout))


# --- glob：按文件名模式找文件 ---
def _glob(pattern: str, limit: int = 200) -> str:
    matches: list[str] = []
    for path in WORKSPACE_ROOT.rglob("*"):
        if any(part in {"__pycache__", ".pytest_cache"} for part in path.parts):
            continue
        try:
            resolve_workspace_path(str(path), must_exist=True)
        except (FileNotFoundError, SecurityError):
            continue
        rel = path.relative_to(WORKSPACE_ROOT).as_posix()
        if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(path.name, pattern):
            matches.append(rel)
            if len(matches) >= limit:
                break
    if not matches:
        return "未找到匹配文件。"
    return "\n".join(matches)


# --- web_fetch：URL -> markdown，控 token 预算 ---
def _web_fetch(url: str, max_tokens: int = 2000) -> str:
    from ticker_dossier.research.discovery.web import web_fetch

    return web_fetch(url, max_tokens * 4)


# --- task_list（TodoWrite）：自维护待办，提升长任务成功率 ---
def _task_list(action: str, items: list | None = None) -> str:
    normalized = action.lower().strip()
    store = _task_store()
    current = _load_tasks(store)
    if normalized in {"clear", "reset"}:
        store.write_text("", encoding="utf-8")
        return "任务清单已清空。"
    if normalized in {"add", "set"}:
        if not items:
            raise ValueError("items 不能为空")
        current = [_normalize_task(item, index) for index, item in enumerate(items, start=1)]
        current = _active_tasks(current)
        _save_tasks(store, current)
        return _render_tasks("任务清单已建立", current)
    if normalized == "update":
        if not items:
            raise ValueError("items 不能为空")
        by_id = {item["id"]: item for item in current}
        for index, raw in enumerate(items, start=1):
            incoming = _normalize_task(raw, index)
            previous = by_id.get(incoming["id"], {})
            by_id[incoming["id"]] = {**previous, **incoming}
        current = _active_tasks(list(by_id.values()))
        _save_tasks(store, current)
        return _render_tasks("任务清单已更新", current)
    if normalized in {"complete", "done"}:
        if items:
            done = {_task_identity(item) for item in items}
            current = [
                item for item in current
                if item["id"] not in done and item["desc"] not in done
            ]
            _save_tasks(store, current)
        return _render_tasks("剩余任务", current)
    if normalized in {"list", "show"}:
        return _render_tasks("当前任务", current)
    raise ValueError("action 必须是 add/update/complete/list/clear")


edit_tool = Tool("edit", "编辑文件：把 old 文本替换为 new。",
                 {"type": "object", "properties": {"path": {"type": "string"},
                  "old": {"type": "string"}, "new": {"type": "string"}},
                  "required": ["path", "old", "new"]}, _edit)
grep_tool = Tool("grep", "在文件中搜索匹配 pattern 的行（基于 ripgrep）。",
                 {"type": "object", "properties": {"pattern": {"type": "string"},
                  "path": {"type": "string"}}, "required": ["pattern"]}, _grep)
glob_tool = Tool("glob", "按通配模式查找文件路径。",
                 {"type": "object", "properties": {"pattern": {"type": "string"},
                  "limit": {"type": "integer"}},
                  "required": ["pattern"]}, _glob)
web_fetch_tool = Tool("web_fetch", "抓取 URL 并转为 markdown（受 token 预算限制）。",
                      {"type": "object", "properties": {"url": {"type": "string"}},
                       "required": ["url"]}, _web_fetch)
task_list_tool = Tool("task_list", "维护任务待办清单。add/set 建立完整计划；update 按 id 合并状态，不会覆盖其他项；每完成一步用 complete/done 删除对应 id，结束前必须让剩余任务为空。",
                      {"type": "object", "properties": {"action": {"type": "string"},
                       "items": {"type": "array", "items": {
                           "oneOf": [
                               {"type": "string"},
                               {"type": "object", "properties": {
                                   "id": {"type": "string"},
                                   "desc": {"type": "string"},
                                   "status": {"type": "string"},
                               }},
                           ],
                       }}}, "required": ["action"]}, _task_list)


def _guard_directory(path: str) -> Path:
    resolved = resolve_workspace_path(path, must_exist=True)
    if not resolved.is_dir():
        raise FileNotFoundError(f"目录不存在: {path}")
    return resolved


def _grep_python(pattern: str, path: Path) -> str:
    import re

    regex = re.compile(pattern)
    rows: list[str] = []
    files = [path] if path.is_file() else [p for p in path.rglob("*") if p.is_file()]
    for file_path in files:
        if any(part in {"__pycache__", ".pytest_cache"} for part in file_path.parts):
            continue
        try:
            resolve_workspace_path(str(file_path), must_exist=True)
        except (FileNotFoundError, SecurityError):
            continue
        try:
            lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for index, line in enumerate(lines, start=1):
            if regex.search(line):
                rel = file_path.relative_to(WORKSPACE_ROOT)
                rows.append(f"{rel}:{index}:{line}")
                if len(rows) >= 200:
                    return "\n".join(rows)
    return "\n".join(rows) if rows else "未找到匹配。"


def _workspace_relative_grep_output(text: str) -> str:
    """Normalize ripgrep paths across platforms and rg versions."""
    root = str(WORKSPACE_ROOT)
    prefixes = (root + "/", root + "\\")
    rows: list[str] = []
    for line in text.splitlines():
        for prefix in prefixes:
            if line.startswith(prefix):
                line = line[len(prefix):]
                break
        rows.append(line)
    return "\n".join(rows) + ("\n" if text.endswith("\n") else "")


def _task_store() -> Path:
    path = WORKSPACE_ROOT / ".agent_task_list"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load_tasks(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    tasks: list[dict[str, str]] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            try:
                raw = ast.literal_eval(line)
            except (SyntaxError, ValueError):
                raw = line
        tasks.append(_normalize_task(raw, index))
    return tasks


def _save_tasks(path: Path, tasks: list[dict[str, str]]) -> None:
    path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False, sort_keys=True) for item in tasks),
        encoding="utf-8",
    )


def _active_tasks(tasks: list[dict[str, str]]) -> list[dict[str, str]]:
    return [item for item in tasks if item.get("status") not in {"completed", "complete", "done"}]


def _normalize_task(raw: object, index: int) -> dict[str, str]:
    if isinstance(raw, dict):
        task_id = str(raw.get("id") or index).strip()
        desc = str(raw.get("desc") or raw.get("task") or raw.get("title") or task_id).strip()
        status = str(raw.get("status") or "pending").strip().lower()
    else:
        task_id = str(index)
        desc = str(raw).strip()
        status = "pending"
    return {"id": task_id, "desc": desc, "status": status}


def _task_identity(raw: object) -> str:
    if isinstance(raw, dict):
        return str(raw.get("id") or raw.get("desc") or raw).strip()
    return str(raw).strip()


def _render_tasks(title: str, tasks: list[dict[str, str]]) -> str:
    if not tasks:
        return f"{title}：\n无（计划已全部完成）"
    rows = [
        f"- [{item['status']}] {item['id']}: {item['desc']}"
        for item in tasks
    ]
    return f"{title}：\n" + "\n".join(rows) + "\n完成后请用 action=complete 按 id 删除对应项。"


def _truncate(text: str, limit: int = 12_000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[已截断，共 {len(text)} 字符]"
