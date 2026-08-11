from __future__ import annotations

import json

import pytest

from ticker_dossier.runtime.permissions import check
from ticker_dossier.integrations.mcp.client import MCPClient
from ticker_dossier.integrations.mcp.finance_server import calculate_risk_budget, handle
from ticker_dossier.bootstrap import build_default_registry


def test_risk_budget_is_deterministic_and_research_only() -> None:
    result = calculate_risk_budget({
        "capital": 100_000,
        "risk_pct": 1,
        "entry_price": 50,
        "stop_price": 45,
    })
    assert result["risk_budget"] == 1000
    assert result["max_shares"] == 200
    assert result["position_value"] == 10_000
    assert "no order" in result["boundary"]


def test_risk_budget_rejects_invalid_prices() -> None:
    with pytest.raises(ValueError, match="must differ"):
        calculate_risk_budget({"capital": 1000, "risk_pct": 1, "entry_price": 10, "stop_price": 10})


def test_finance_mcp_handle_returns_json_rpc_content() -> None:
    response = handle({
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "risk_budget",
            "arguments": {"capital": 100_000, "risk_pct": 1, "entry_price": 50, "stop_price": 45},
        },
    })
    assert response is not None
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["max_shares"] == 200


def test_project_registry_connects_domain_mcp() -> None:
    registry = build_default_registry()
    try:
        tool = registry.get("mcp__finance__risk_budget")
        assert tool is not None, registry.mcp_statuses()
        output = tool.run(capital=100_000, risk_pct=1, entry_price=50, stop_price=45)
        assert '"max_shares": 200' in output
        assert registry.get("mcp__echo") is not None
    finally:
        registry.close()


def test_domain_mcp_calculator_is_readonly_allowed(tmp_path) -> None:  # noqa: ANN001
    assert check("mcp__finance__risk_budget", {}, tmp_path) == "allow"
