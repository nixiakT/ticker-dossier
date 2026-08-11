"""Workspace-scoped file read and write tools."""
from __future__ import annotations

from ticker_dossier.runtime.tools import Tool
from ticker_dossier.security import guard_read, guard_write, untrusted_block


def _read(
    path: str,
    max_bytes: int = 100_000,
    start_line: int = 1,
    line_count: int | None = None,
) -> str:
    resolved = guard_read(path)
    if start_line < 1:
        raise ValueError("start_line must be at least 1")
    if line_count is not None and line_count < 1:
        raise ValueError("line_count must be at least 1")
    if max_bytes < 1:
        raise ValueError("max_bytes must be at least 1")
    data = resolved.read_bytes()
    all_lines = data.decode("utf-8", errors="replace").splitlines()
    start_index = start_line - 1
    requested = all_lines[start_index:]
    if line_count is not None:
        requested = requested[:line_count]
    requested_text = "\n".join(requested)
    requested_bytes = requested_text.encode("utf-8")
    byte_truncated = len(requested_bytes) > max_bytes
    visible_text = requested_bytes[:max_bytes].decode("utf-8", errors="replace")
    visible_lines = visible_text.splitlines()
    numbered = "\n".join(
        f"{idx:>4} {line}"
        for idx, line in enumerate(visible_lines, start=start_line)
    )
    consumed_lines = len(visible_lines)
    has_more_lines = start_index + consumed_lines < len(all_lines)
    result = f"路径: {resolved}\n"
    result += f"范围: 第 {start_line} 行起，返回 {consumed_lines} 行；文件共 {len(all_lines)} 行。\n"
    if byte_truncated:
        result += f"备注: 本段超过 {max_bytes} bytes，已按字节截断。\n"
    if has_more_lines:
        result += f"下一段: start_line={start_line + consumed_lines}\n"
    result += numbered
    return untrusted_block("FILE", str(resolved), result)


def _write(path: str, content: str) -> str:
    resolved = guard_write(path, content)
    resolved.write_text(content, encoding="utf-8")
    return f"写入成功: {resolved} ({len(content)} chars)"


read_tool = Tool(
    name="read",
    description=(
        "读取指定路径的文本文件内容，可用 start_line 和 line_count 按行分段读取。"
        "先依据 grep 返回的行号读取相关区间；长文件不要反复读取同一前缀。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "max_bytes": {"type": "integer", "description": "最多读取字节数，默认 100000"},
            "start_line": {"type": "integer", "description": "起始行号，从 1 开始，默认 1"},
            "line_count": {"type": "integer", "description": "最多返回多少行；省略时读到字节上限"},
        },
        "required": ["path"],
    },
    run=_read,
)

write_tool = Tool(
    name="write",
    description="把内容写入指定路径（覆盖）。",
    parameters={"type": "object",
                "properties": {"path": {"type": "string"},
                               "content": {"type": "string"}},
                "required": ["path", "content"]},
    run=_write,
)
