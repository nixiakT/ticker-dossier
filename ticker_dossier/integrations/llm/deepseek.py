"""DeepSeek model adapter using OpenAI-compatible protocols.

TickerDossier calls a configured model endpoint instead of bundling a local
model runtime.
DeepSeek 的接口与 OpenAI 完全兼容，所以下面用通用的 OpenAI 协议写法，
只要改 base_url / api_key / model 就能换任意 OpenAI 兼容厂商。

接口约定（和 FakeBackend 一致）：
    chat(messages, tools) -> {"role": "assistant", "content": str, "tool_calls": [ {name, arguments}, ... ]}

环境变量：
    DEEPSEEK_API_KEY   你的 key（千万别提交进 git！）
    DEEPSEEK_BASE_URL  默认 https://api.deepseek.com
    DEEPSEEK_MODEL     默认 deepseek-chat
    DEEPSEEK_API_MODE  chat_completions 或 responses
    DEEPSEEK_REASONING_EFFORT  Responses API 推理强度，例如 xhigh
"""
from __future__ import annotations
import os
import json
from typing import Any

import httpx

from ticker_dossier.config import load_local_env
from ticker_dossier.integrations.http import client as http_client
from ticker_dossier.telemetry import normalize_usage


class DeepSeekBackend:
    # Fixed reports remain available to FakeBackend and CLI fallback, not the real model.
    model_tool_exclusions = frozenset({"finance_route_task", "finance_generate_report"})

    def __init__(self,
                 api_key: str | None = None,
                 base_url: str | None = None,
                 model: str | None = None,
                 timeout: float | None = None,
                 read_retries: int | None = None):
        load_local_env()
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        self.base_url = _normalize_base_url(base_url or os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
        self.model = model or os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
        self.api_mode = os.environ.get("DEEPSEEK_API_MODE", "chat_completions").strip().lower()
        self.reasoning_effort = os.environ.get("DEEPSEEK_REASONING_EFFORT", "").strip().lower()
        if self.api_mode not in {"chat_completions", "responses"}:
            raise ValueError("DEEPSEEK_API_MODE must be chat_completions or responses")
        if not self.api_key:
            raise RuntimeError("缺少 DEEPSEEK_API_KEY 环境变量")
        self.timeout = timeout or _positive_float_env("FINANCE_MODEL_TIMEOUT_SECONDS", 120.0)
        configured_retries = (
            _nonnegative_int_env("FINANCE_MODEL_READ_RETRIES", 0)
            if read_retries is None
            else read_retries
        )
        self.read_retries = max(configured_retries, 0)
        self._client = http_client(timeout=self.timeout, follow_redirects=True)

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
             temperature: float = 0.0) -> dict[str, Any]:
        """一次（非流式）对话补全，返回归一化的 assistant 消息。"""
        if self.api_mode == "responses":
            return self._responses_chat(messages, tools)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._to_openai_messages(messages),
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools           # OpenAI tools 格式，base.Tool.schema() 已生成
            payload["tool_choice"] = "auto"

        resp = self._post(
            f"{self.base_url}/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        msg = data["choices"][0]["message"]
        normalized = self._normalize(msg)
        normalized["usage"] = normalize_usage(data.get("usage"))
        return normalized

    def _responses_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "input": self._to_responses_input(messages),
        }
        if self.reasoning_effort:
            payload["reasoning"] = {"effort": self.reasoning_effort}
        if tools:
            payload["tools"] = [self._to_responses_tool(tool) for tool in tools]
            payload["tool_choice"] = "auto"
        resp = self._post(
            f"{self.base_url}/v1/responses",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
        )
        resp.raise_for_status()
        return self._normalize_response(resp.json())

    def _post(self, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> httpx.Response:
        for attempt in range(self.read_retries + 1):
            try:
                return self._client.post(url, headers=headers, json=json)
            except httpx.ReadTimeout:
                if attempt >= self.read_retries:
                    raise
        raise RuntimeError("unreachable model retry state")

    def close(self) -> None:
        """Release the shared HTTP connection pool owned by this adapter."""
        self._client.close()

    # --- 把内部 messages（含 role=tool）转成 OpenAI 标准格式 ---
    def _to_openai_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for m in messages:
            role = m.get("role")
            if role == "tool":
                # OpenAI 要求 tool 消息带 tool_call_id；最小实现可用 name 兜底
                out.append({"role": "tool", "content": str(m.get("content", "")),
                            "tool_call_id": m.get("tool_call_id", m.get("name", "tool"))})
            elif role == "assistant" and m.get("tool_calls"):
                out.append({"role": "assistant", "content": m.get("content") or None,
                            "tool_calls": self._to_openai_tool_calls(m["tool_calls"])})
            else:
                out.append({"role": role, "content": m.get("content", "")})
        return out

    @staticmethod
    def _to_responses_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for message in messages:
            role = message.get("role")
            if role == "tool":
                items.append({
                    "type": "function_call_output",
                    "call_id": message.get("tool_call_id", message.get("name", "tool")),
                    "output": str(message.get("content", "")),
                })
                continue
            content = str(message.get("content") or "")
            if content or not message.get("tool_calls"):
                items.append({"role": role, "content": content})
            if role == "assistant":
                for index, call in enumerate(message.get("tool_calls") or []):
                    items.append({
                        "type": "function_call",
                        "call_id": call.get("id") or f"call_{index}",
                        "name": call.get("name", ""),
                        "arguments": json.dumps(call.get("arguments", {}), ensure_ascii=False),
                    })
        return items

    @staticmethod
    def _to_responses_tool(tool: dict[str, Any]) -> dict[str, Any]:
        function = tool.get("function") or {}
        return {
            "type": "function",
            "name": function.get("name", ""),
            "description": function.get("description", ""),
            "parameters": function.get("parameters") or {"type": "object", "properties": {}},
        }

    @staticmethod
    def _to_openai_tool_calls(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for i, c in enumerate(calls):
            out.append({"id": c.get("id", f"call_{i}"), "type": "function",
                        "function": {"name": c["name"],
                                     "arguments": json.dumps(c.get("arguments", {}), ensure_ascii=False)}})
        return out

    # --- 把 OpenAI 返回归一化成内部格式 ---
    @staticmethod
    def _normalize(msg: dict[str, Any]) -> dict[str, Any]:
        tool_calls = []
        for tc in (msg.get("tool_calls") or []):
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            tool_calls.append({"id": tc.get("id"), "name": fn.get("name"), "arguments": args})
        return {"role": "assistant", "content": msg.get("content") or "", "tool_calls": tool_calls}

    @staticmethod
    def _normalize_response(response: dict[str, Any]) -> dict[str, Any]:
        content: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for item in response.get("output") or []:
            if item.get("type") == "message":
                for part in item.get("content") or []:
                    if part.get("type") == "output_text" and part.get("text"):
                        content.append(str(part["text"]))
            elif item.get("type") == "function_call":
                try:
                    arguments = json.loads(item.get("arguments") or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                tool_calls.append({
                    "id": item.get("call_id") or item.get("id"),
                    "name": item.get("name"),
                    "arguments": arguments,
                })
        return {"role": "assistant", "content": "\n".join(content), "tool_calls": tool_calls}


def _normalize_base_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        return base[:-3]
    return base


def _positive_float_env(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _nonnegative_int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
