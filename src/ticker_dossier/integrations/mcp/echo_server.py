"""Minimal stdio JSON-RPC MCP server exposing ``echo(text)``."""
from __future__ import annotations
import json
import sys
from typing import Any

TOOLS = [{
    "name": "echo",
    "description": "原样返回输入的 text。",
    "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
}]


def handle(req: dict[str, Any]) -> dict[str, Any] | None:
    method = req.get("method")
    rid = req.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid,
                "result": {"protocolVersion": "2024-11-05",
                           "serverInfo": {"name": "echo", "version": "0.1"},
                           "capabilities": {"tools": {}}}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        text = req["params"]["arguments"].get("text", "")
        return {"jsonrpc": "2.0", "id": rid,
                "result": {"content": [{"type": "text", "text": text}]}}
    if rid is None:           # 通知类（如 notifications/initialized）无需回应
        return None
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "method not found"}}


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        resp = handle(json.loads(line))
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
