"""Small domain MCP server for deterministic position risk budgeting."""
from __future__ import annotations

import json
import math
import sys
from typing import Any


TOOLS = [{
    "name": "risk_budget",
    "description": "根据资金、单笔风险比例、入场价和止损价计算最大股数与仓位；只做研究计算，不执行交易。",
    "inputSchema": {
        "type": "object",
        "properties": {
            "capital": {"type": "number"},
            "risk_pct": {"type": "number"},
            "entry_price": {"type": "number"},
            "stop_price": {"type": "number"},
        },
        "required": ["capital", "risk_pct", "entry_price", "stop_price"],
    },
}]


def calculate_risk_budget(arguments: dict[str, Any]) -> dict[str, Any]:
    capital = _positive(arguments, "capital")
    risk_pct = _positive(arguments, "risk_pct")
    entry_price = _positive(arguments, "entry_price")
    stop_price = _positive(arguments, "stop_price")
    if risk_pct > 100:
        raise ValueError("risk_pct must be <= 100")
    risk_per_share = abs(entry_price - stop_price)
    if risk_per_share == 0:
        raise ValueError("entry_price and stop_price must differ")
    risk_budget = capital * risk_pct / 100
    max_shares = math.floor(risk_budget / risk_per_share)
    position_value = max_shares * entry_price
    return {
        "capital": round(capital, 4),
        "risk_pct": round(risk_pct, 4),
        "risk_budget": round(risk_budget, 4),
        "risk_per_share": round(risk_per_share, 4),
        "max_shares": max_shares,
        "position_value": round(position_value, 4),
        "position_pct": round(position_value / capital * 100, 4),
        "boundary": "research-only calculation; no order was sent",
    }


def handle(request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    if request_id is None:
        return None
    if method == "initialize":
        return _result(request_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "finance-risk", "version": "1.0"},
        })
    if method == "tools/list":
        return _result(request_id, {"tools": TOOLS})
    if method == "tools/call":
        params = request.get("params") or {}
        if params.get("name") != "risk_budget":
            return _error(request_id, -32601, f"unknown tool: {params.get('name')}")
        try:
            result = calculate_risk_budget(params.get("arguments") or {})
        except (TypeError, ValueError) as exc:
            return _error(request_id, -32602, str(exc))
        return _result(request_id, {
            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
        })
    return _error(request_id, -32601, f"unknown method: {method}")


def main() -> None:
    for line in sys.stdin:
        try:
            request = json.loads(line)
            response = handle(request)
        except json.JSONDecodeError:
            continue
        except Exception as exc:  # noqa: BLE001 - server errors must stay off stdout JSON stream
            sys.stderr.write(f"[finance_mcp] {exc}\n")
            sys.stderr.flush()
            continue
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


def _positive(arguments: dict[str, Any], name: str) -> float:
    value = float(arguments.get(name, 0))
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _result(request_id: Any, value: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": value}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


if __name__ == "__main__":
    main()
