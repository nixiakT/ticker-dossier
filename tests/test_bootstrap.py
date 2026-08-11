from __future__ import annotations

from ticker_dossier.bootstrap import ResearchServices, build_default_registry
from ticker_dossier.integrations.mcp import client as mcp_client
from ticker_dossier.research.agent import FinanceResearchAgent
from ticker_dossier.research.data import ProviderChain
from ticker_dossier.tools import finance_tools as finance_tools_module


def test_registry_binds_finance_tools_to_application_service(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(mcp_client, "connect_project_mcp", lambda registry: None)
    finance = FinanceResearchAgent(provider=ProviderChain(providers=[]))

    registry = build_default_registry(ResearchServices(finance=finance))

    quote = registry.get("finance_get_quote")
    assert quote is not None
    assert getattr(quote.run, "__self__", None) is finance


def test_binding_tools_does_not_create_legacy_fallback_agent() -> None:
    finance_tools_module._fallback_agent = None
    finance = FinanceResearchAgent(provider=ProviderChain(providers=[]))

    bound = finance_tools_module.build_finance_tools(finance)

    assert len(bound) == len(finance_tools_module.finance_tools)
    assert finance_tools_module._fallback_agent is None
