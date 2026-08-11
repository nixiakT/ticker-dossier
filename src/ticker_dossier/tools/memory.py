"""Project-level persistent-memory tools."""
from __future__ import annotations

from ticker_dossier.runtime.memory import KVMemory, Memory
from ticker_dossier.runtime.tools import Tool


def _remember(note: str) -> str:
    path = Memory().write(note)
    return f"已记住：{note}\n写入: {path}"


def _memory_set(key: str, value: str) -> str:
    path = KVMemory().remember(key, value)
    return f"已更新结构化记忆：{key}\n写入: {path}"


def _memory_forget(key: str) -> str:
    path = KVMemory().forget(key)
    return f"已遗忘结构化记忆：{key}\n写入: {path}"


remember_tool = Tool(
    "remember",
    "当用户明确要求长期记住跨会话仍成立的项目约定、偏好或关键决策时调用；不得记录密钥、密码、token、cookie 或一次性闲聊。",
    {"type": "object", "properties": {"note": {"type": "string"}}, "required": ["note"]},
    _remember,
)
memory_set_tool = Tool(
    "memory_set",
    "更新一条结构化项目记忆，适合覆盖已有约定。",
    {"type": "object", "properties": {"key": {"type": "string"}, "value": {"type": "string"}}, "required": ["key", "value"]},
    _memory_set,
)
memory_forget_tool = Tool(
    "memory_forget",
    "当用户明确要求忘记某条结构化项目记忆时调用。",
    {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]},
    _memory_forget,
)

memory_tools = [remember_tool, memory_set_tool, memory_forget_tool]
