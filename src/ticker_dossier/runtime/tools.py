"""Tool contracts shared by the runtime and capability adapters.

Application assembly deliberately lives in :mod:`ticker_dossier.bootstrap` so
this low-level module never imports finance, MCP, WeChat, or scheduler code.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable, cast


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    run: Callable[..., str]

    def schema(self) -> dict[str, Any]:
        """转成 OpenAI tools 字段的一项。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class ToolRegistry:
    _tools: dict[str, Tool] = field(default_factory=dict)
    _managed_resources: list[Any] = field(default_factory=list, repr=False)
    _services: dict[str, Any] = field(default_factory=dict, repr=False)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"工具重名：{tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def schemas(self) -> list[dict[str, Any]]:
        return [t.schema() for t in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools)

    def manage(self, resource: Any) -> None:
        """Attach a runtime resource so callers can inspect and close it centrally."""
        self._managed_resources.append(resource)

    def provide_service(self, name: str, service: Any) -> None:
        """Expose one application-owned service without importing its concrete type."""
        clean = str(name).strip()
        if not clean:
            raise ValueError("service name must not be empty")
        if clean in self._services:
            raise ValueError(f"service already registered: {clean}")
        self._services[clean] = service

    def get_service(self, name: str) -> Any | None:
        return self._services.get(name)

    def mcp_statuses(self) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for resource in self._managed_resources:
            statuses = getattr(resource, "statuses", None)
            if callable(statuses):
                rows.extend(statuses())
        return sorted(rows, key=lambda row: row.get("name", ""))

    def mcp_prompts(self) -> list[dict[str, Any]]:
        prompts: list[dict[str, Any]] = []
        for resource in self._managed_resources:
            catalog = getattr(resource, "prompt_catalog", None)
            if callable(catalog):
                prompts.extend(catalog())
        return sorted(prompts, key=lambda item: (item.get("server", ""), item.get("name", "")))

    def get_mcp_prompt(
        self,
        server: str,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        for resource in self._managed_resources:
            getter = getattr(resource, "get_prompt", None)
            if not callable(getter):
                continue
            try:
                return cast(dict[str, Any], getter(server, name, arguments))
            except KeyError:
                continue
        raise KeyError(f"unknown MCP prompt '{server}/{name}'")

    def close(self) -> None:
        """Close every managed runtime, even if one close operation fails."""
        errors: list[str] = []
        for resource in reversed(self._managed_resources):
            close = getattr(resource, "close", None)
            if not callable(close):
                continue
            try:
                close()
            except Exception as exc:  # noqa: BLE001 - finish closing the remaining resources
                errors.append(str(exc))
        if errors:
            raise RuntimeError("failed to close managed resources: " + "; ".join(errors))

    def __len__(self) -> int:
        return len(self._tools)
