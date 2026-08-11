"""Line-delimited JSON-RPC transport for stdio MCP servers."""
from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from ticker_dossier.integrations.mcp.config import (
    DEFAULT_TIMEOUT,
    _mcp_process_env,
    _validate_mcp_name,
)

_EOF = object()


class MCPRPCError(RuntimeError):
    """A JSON-RPC error returned by an MCP server."""

    def __init__(self, server: str, method: str, error: Any):
        self.server = server
        self.method = method
        self.error = error
        self.code = error.get("code") if isinstance(error, dict) else None
        message = error.get("message") if isinstance(error, dict) else str(error)
        super().__init__(f"MCP server '{server}' {method} failed: {message}")


class MCPClient:
    """One line-delimited JSON-RPC client backed by a stdio subprocess."""

    def __init__(
        self,
        command: list[str],
        *,
        name: str = "server",
        env: dict[str, str] | None = None,
        cwd: str | Path | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        if not command:
            raise ValueError("MCP command must not be empty")
        _validate_mcp_name(name, "server")
        if timeout <= 0:
            raise ValueError("MCP timeout must be greater than zero")
        self.command = list(command)
        self.name = name
        self.env = dict(env or {})
        self.cwd = Path(cwd) if cwd is not None else None
        self.timeout = float(timeout)
        self.proc: subprocess.Popen[str] | None = None
        self.capabilities: dict[str, Any] = {}
        self._id = 0
        self._state = "configured"
        self._detail = ""
        self._stdout_queue: queue.Queue[str | object] = queue.Queue()
        self._stderr_lines: deque[str] = deque(maxlen=20)
        self._rpc_lock = threading.Lock()

    def start(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            raise RuntimeError(f"MCP server '{self.name}' is already running")
        self._state = "connecting"
        self._detail = ""
        self.capabilities = {}
        self._stdout_queue = queue.Queue()
        self._stderr_lines.clear()
        process_env = _mcp_process_env(self.env)
        try:
            self.proc = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                cwd=str(self.cwd) if self.cwd is not None else None,
                env=process_env,
            )
            assert self.proc.stdout is not None
            assert self.proc.stderr is not None
            threading.Thread(
                target=self._read_stdout,
                args=(self.proc.stdout,),
                daemon=True,
                name=f"mcp-{self.name}-stdout",
            ).start()
            threading.Thread(
                target=self._read_stderr,
                args=(self.proc.stderr,),
                daemon=True,
                name=f"mcp-{self.name}-stderr",
            ).start()
            initialized = self._rpc("initialize", {
                "protocolVersion": "2024-11-05",
                "clientInfo": {"name": "ticker-dossier", "version": "0.1"},
                "capabilities": {},
            })
            if isinstance(initialized, dict):
                capabilities = initialized.get("capabilities", {})
                if isinstance(capabilities, dict):
                    self.capabilities = capabilities
            self._notify("notifications/initialized", {})
            self._state = "connected"
            self._detail = ""
        except Exception as exc:
            if self._state != "error":
                self._mark_error(str(exc))
            self._shutdown()
            raise

    def _rpc(self, method: str, params: dict[str, Any] | None = None) -> Any:
        # One stdio stream cannot safely be consumed by several callers at once:
        # a caller that dequeues another request's response would otherwise drop it.
        with self._rpc_lock:
            return self._rpc_locked(method, params)

    def _rpc_locked(self, method: str, params: dict[str, Any] | None = None) -> Any:
        if (
            self.proc is None
            or self.proc.poll() is not None
            or self.proc.stdin is None
        ):
            detail = f"MCP server '{self.name}' is not running"
            if self.proc is not None and self.proc.poll() is not None:
                detail = f"MCP server '{self.name}' exited with code {self.proc.returncode}"
            if self._state != "closed":
                self._mark_error(detail)
            raise RuntimeError(detail)
        self._id += 1
        request_id = self._id
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        }
        try:
            self.proc.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            detail = f"MCP server '{self.name}' closed stdin during {method}: {exc}"
            self._mark_error(detail)
            self._shutdown()
            raise RuntimeError(detail) from exc

        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._raise_timeout(method)
            try:
                line = self._stdout_queue.get(timeout=remaining)
            except queue.Empty:
                self._raise_timeout(method)
            if line is _EOF:
                stderr = self._stderr_detail()
                detail = f"MCP server '{self.name}' closed stdout during {method}"
                if stderr:
                    detail += f"; stderr: {stderr}"
                self._mark_error(detail)
                self._shutdown()
                raise RuntimeError(detail)
            try:
                response = json.loads(str(line))
            except json.JSONDecodeError as exc:
                detail = f"MCP server '{self.name}' returned invalid JSON during {method}: {exc}"
                self._mark_error(detail)
                self._shutdown()
                raise RuntimeError(detail) from exc
            if response.get("id") != request_id:
                continue
            if response.get("error") is not None:
                raise MCPRPCError(self.name, method, response["error"])
            return response.get("result")

    def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        if self.proc is None or self.proc.poll() is not None or self.proc.stdin is None:
            raise RuntimeError(f"MCP server '{self.name}' is not running")
        request = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        self.proc.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def list_tools(self) -> list[dict[str, Any]]:
        result = self._rpc("tools/list", {})
        if not isinstance(result, dict) or not isinstance(result.get("tools", []), list):
            raise RuntimeError(f"MCP server '{self.name}' returned an invalid tools/list result")
        return list(result.get("tools", []))

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        if not isinstance(result, dict):
            raise RuntimeError(f"MCP server '{self.name}' returned an invalid tools/call result")
        content = result.get("content", [])
        parts = [
            str(item.get("text", ""))
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        text = "\n".join(parts)
        if result.get("isError"):
            raise RuntimeError(text or f"MCP tool '{name}' failed")
        return text

    def supports_prompts(self) -> bool:
        return isinstance(self.capabilities.get("prompts"), dict)

    def list_prompts(self) -> list[dict[str, Any]]:
        result = self._rpc("prompts/list", {})
        if not isinstance(result, dict) or not isinstance(result.get("prompts", []), list):
            raise RuntimeError(f"MCP server '{self.name}' returned an invalid prompts/list result")
        return list(result.get("prompts", []))

    def get_prompt(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        result = self._rpc("prompts/get", {"name": name, "arguments": arguments or {}})
        if not isinstance(result, dict):
            raise RuntimeError(f"MCP server '{self.name}' returned an invalid prompts/get result")
        return result

    def status(self) -> dict[str, str]:
        if self._state == "connected" and self.proc is not None and self.proc.poll() is not None:
            detail = f"MCP server '{self.name}' exited with code {self.proc.returncode}"
            stderr = self._stderr_detail()
            if stderr:
                detail += f"; stderr: {stderr}"
            self._mark_error(detail)
        return {"name": self.name, "status": self._state, "detail": self._detail}

    def close(self) -> None:
        self._shutdown()
        if self._state != "error":
            self._state = "closed"
            self._detail = ""

    def _read_stdout(self, stream: Any) -> None:
        try:
            for line in stream:
                self._stdout_queue.put(line)
        finally:
            self._stdout_queue.put(_EOF)

    def _read_stderr(self, stream: Any) -> None:
        for line in stream:
            clean = line.strip()
            if clean:
                self._stderr_lines.append(clean)

    def _stderr_detail(self) -> str:
        return " | ".join(self._stderr_lines)

    def _mark_error(self, detail: str) -> None:
        self._state = "error"
        self._detail = detail

    def _raise_timeout(self, method: str) -> None:
        detail = (
            f"MCP server '{self.name}' {method} timed out after "
            f"{self.timeout:g}s"
        )
        self._mark_error(detail)
        self._shutdown()
        raise TimeoutError(detail)

    def _shutdown(self) -> None:
        proc = self.proc
        if proc is None:
            return
        if proc.stdin is not None:
            try:
                proc.stdin.close()
            except OSError:
                pass
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=1)
