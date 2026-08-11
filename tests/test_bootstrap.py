from __future__ import annotations

import pytest
from types import SimpleNamespace

import ticker_dossier.bootstrap as bootstrap_module
import ticker_dossier.research.agent as research_agent_module
from ticker_dossier.bootstrap import (
    ResearchServices,
    build_agent,
    build_debate_backend,
    build_default_registry,
    build_finance_research_agent,
    build_model_backend,
    build_research_services,
)
from ticker_dossier.cli import commands as commands_module
from ticker_dossier.cli import main as main_module
from ticker_dossier.integrations.mcp import client as mcp_client
from ticker_dossier.llm import deepseek as deepseek_module
from ticker_dossier.llm.fake import FakeBackend
from ticker_dossier.research.agent import FinanceResearchAgent
from ticker_dossier.research.market_data import ProviderChain
from ticker_dossier.runtime.tools import ToolRegistry
from ticker_dossier.tools import evolution_tools as evolution_tools_module
from ticker_dossier.tools import finance_tools as finance_tools_module
from ticker_dossier.tools import scheduler_tools as scheduler_tools_module


class ClosableProvider:
    def __init__(self, events: list[str], name: str):
        self.events = events
        self.name = name

    def close(self) -> None:
        self.events.append(self.name)


class ClosableAgent:
    def __init__(self, name: str, events: list[str], *, fail_brief: bool = False):
        self.name = name
        self.events = events
        self.fail_brief = fail_brief

    def close(self) -> None:
        self.events.append(f"close:{self.name}")

    def daily_brief(self, symbols: str) -> str:
        if self.fail_brief:
            raise RuntimeError("brief failed")
        return f"brief:{symbols}"

    def mark_paper_portfolio(self, name: str) -> str:
        return f"portfolio:{name}"


def test_registry_binds_finance_tools_to_application_service(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(mcp_client, "connect_project_mcp", lambda registry: None)
    finance = FinanceResearchAgent(provider=ProviderChain(providers=[]))

    registry = build_default_registry(ResearchServices(finance=finance))

    quote = registry.get("finance_get_quote")
    assert quote is not None
    assert getattr(quote.run, "__self__", None) is finance


def test_finance_agent_closes_only_its_self_created_provider(monkeypatch) -> None:  # noqa: ANN001
    events: list[str] = []
    owned_provider = ClosableProvider(events, "owned")
    injected_provider = ClosableProvider(events, "injected")
    monkeypatch.setattr(research_agent_module, "ProviderChain", lambda: owned_provider)

    owned = FinanceResearchAgent()
    injected = FinanceResearchAgent(provider=injected_provider)  # type: ignore[arg-type]

    owned.close()
    injected.close()

    assert events == ["owned"]


def test_research_services_delegate_close_to_finance(monkeypatch) -> None:  # noqa: ANN001
    finance = FinanceResearchAgent(provider=ProviderChain(providers=[]))
    events: list[str] = []
    monkeypatch.setattr(finance, "close", lambda: events.append("finance"))

    ResearchServices(finance=finance).close()

    assert events == ["finance"]


def test_default_registry_owns_default_services_but_not_injected_services(
    monkeypatch,
) -> None:  # noqa: ANN001
    monkeypatch.setattr(mcp_client, "connect_project_mcp", lambda registry: None)
    events: list[str] = []
    default_finance = FinanceResearchAgent(provider=ProviderChain(providers=[]))
    injected_finance = FinanceResearchAgent(provider=ProviderChain(providers=[]))
    monkeypatch.setattr(default_finance, "close", lambda: events.append("default"))
    monkeypatch.setattr(injected_finance, "close", lambda: events.append("injected"))
    default_services = ResearchServices(finance=default_finance)
    injected_services = ResearchServices(finance=injected_finance)
    monkeypatch.setattr(
        bootstrap_module,
        "build_research_services",
        lambda: default_services,
    )

    default_registry = build_default_registry()
    injected_registry = build_default_registry(injected_services)
    default_registry.close()
    injected_registry.close()

    assert events == ["default"]


def test_agent_backend_closes_first_and_cleanup_continues_after_error(
    monkeypatch,
) -> None:  # noqa: ANN001
    events: list[str] = []

    class ExistingResource:
        def close(self) -> None:
            events.append("existing")

    class Backend:
        def chat(self, messages, tools):  # noqa: ANN001, ANN201
            return {"role": "assistant", "content": "", "tool_calls": []}

        def close(self) -> None:
            events.append("backend")
            raise RuntimeError("backend close failed")

    backend = Backend()
    registry = ToolRegistry()
    registry.manage(ExistingResource())
    monkeypatch.setattr(bootstrap_module, "build_model_backend", lambda notify=None: backend)

    agent = build_agent("system", registry=registry)

    assert agent.backend is backend
    with pytest.raises(RuntimeError, match="backend close failed"):
        registry.close()
    assert events == ["backend", "existing"]


def test_binding_tools_does_not_create_legacy_fallback_agent() -> None:
    finance_tools_module._fallback_agent = None
    finance = FinanceResearchAgent(provider=ProviderChain(providers=[]))

    bound = finance_tools_module.build_finance_tools(finance)

    assert len(bound) == len(finance_tools_module.finance_tools)
    assert finance_tools_module._fallback_agent is None


def test_research_services_receive_an_explicit_debate_factory() -> None:
    def factory():  # noqa: ANN202
        return None

    services = build_research_services(debate_backend_factory=factory)

    assert services.finance.debate_backend is None
    assert services.finance.debate_backend_factory is factory


def test_default_research_services_defer_adapter_construction_to_composition_factory() -> None:
    services = build_research_services()

    assert services.finance.debate_backend is None
    assert services.finance.debate_backend_factory is build_debate_backend


def test_compatibility_fallbacks_use_composition_builder(monkeypatch) -> None:  # noqa: ANN001
    sentinel = object()
    monkeypatch.setattr(
        "ticker_dossier.bootstrap.build_finance_research_agent",
        lambda: sentinel,
    )
    monkeypatch.setattr(finance_tools_module, "_fallback_agent", None)

    assert finance_tools_module._default_agent() is sentinel
    assert evolution_tools_module._default_finance_agent() is sentinel
    assert scheduler_tools_module._default_finance_agent() is sentinel
    assert commands_module.CommandRouter(ToolRegistry()).finance is sentinel
    assert main_module._finance_service(ToolRegistry()) is sentinel


def test_transient_evolution_fallbacks_close_on_success_and_error(monkeypatch) -> None:  # noqa: ANN001
    events: list[str] = []
    agents = iter((
        ClosableAgent("record", events),
        ClosableAgent("evaluate", events),
    ))
    monkeypatch.setattr(evolution_tools_module, "_default_finance_agent", lambda: next(agents))
    monkeypatch.setattr(
        evolution_tools_module,
        "_prediction_record_with_agent",
        lambda *args: "recorded",
    )

    assert evolution_tools_module._prediction_record("AAPL", "up") == "recorded"

    def fail_evaluation(*args):  # noqa: ANN002, ANN202
        raise RuntimeError("evaluation failed")

    monkeypatch.setattr(
        evolution_tools_module,
        "_prediction_evaluate_with_agent",
        fail_evaluation,
    )
    with pytest.raises(RuntimeError, match="evaluation failed"):
        evolution_tools_module._prediction_evaluate()
    assert events == ["close:record", "close:evaluate"]


def test_scheduler_closes_only_self_created_agents_in_success_and_error_paths(
    monkeypatch,
) -> None:  # noqa: ANN001
    events: list[str] = []
    owned = ClosableAgent("owned", events)
    portfolio_owned = ClosableAgent("portfolio", events)
    injected = ClosableAgent("injected", events)
    owned_agents = iter((owned, portfolio_owned))
    monkeypatch.setattr(
        scheduler_tools_module,
        "_default_finance_agent",
        lambda: next(owned_agents),
    )
    monkeypatch.setattr(
        scheduler_tools_module,
        "send_markdown",
        lambda *args, **kwargs: SimpleNamespace(status="sent"),
    )
    brief_job = SimpleNamespace(kind="wechat_brief", payload={"symbols": "AAPL"})
    portfolio_job = SimpleNamespace(kind="wechat_portfolio_mark", payload={"name": "paper"})

    assert scheduler_tools_module._run_job(brief_job) == "sent"
    assert scheduler_tools_module._run_job(portfolio_job) == "sent"
    assert scheduler_tools_module._run_job(brief_job, finance_agent=injected) == "sent"
    assert events == ["close:owned", "close:portfolio"]

    failing = ClosableAgent("failing", events, fail_brief=True)
    monkeypatch.setattr(scheduler_tools_module, "_default_finance_agent", lambda: failing)
    with pytest.raises(RuntimeError, match="brief failed"):
        scheduler_tools_module._run_job(brief_job)
    assert events == ["close:owned", "close:portfolio", "close:failing"]


def test_router_and_cli_fallbacks_are_managed_but_injected_agent_is_caller_owned(
    monkeypatch,
) -> None:  # noqa: ANN001
    events: list[str] = []
    router_fallback = ClosableAgent("router", events)
    cli_fallback = ClosableAgent("cli", events)
    injected = ClosableAgent("injected", events)
    fallbacks = iter((router_fallback, cli_fallback))
    monkeypatch.setattr(
        bootstrap_module,
        "build_finance_research_agent",
        lambda: next(fallbacks),
    )

    router_registry = ToolRegistry()
    router = commands_module.CommandRouter(router_registry)
    assert router.finance is router_fallback
    router_registry.close()

    cli_registry = ToolRegistry()
    assert main_module._finance_service(cli_registry) is cli_fallback
    cli_registry.close()

    injected_registry = ToolRegistry()
    commands_module.CommandRouter(injected_registry, finance_agent=injected)  # type: ignore[arg-type]
    injected_registry.close()
    assert events == ["close:router", "close:cli"]


def test_module_finance_singleton_has_explicit_and_best_effort_shutdown(
    monkeypatch,
) -> None:  # noqa: ANN001
    events: list[str] = []
    singleton = ClosableAgent("singleton", events)
    monkeypatch.setattr(
        bootstrap_module,
        "build_finance_research_agent",
        lambda: singleton,
    )
    monkeypatch.setattr(finance_tools_module, "_fallback_agent", None)

    assert finance_tools_module._default_agent() is singleton
    assert finance_tools_module._default_agent() is singleton
    finance_tools_module.shutdown_fallback_agent()
    finance_tools_module.shutdown_fallback_agent()
    assert events == ["close:singleton"]
    assert finance_tools_module._fallback_agent is None

    class ExplodingAgent(ClosableAgent):
        def close(self) -> None:
            events.append("close:exploding")
            raise RuntimeError("close failed")

    monkeypatch.setattr(
        finance_tools_module,
        "_fallback_agent",
        ExplodingAgent("exploding", events),
    )
    finance_tools_module._shutdown_fallback_agent_at_exit()
    assert events == ["close:singleton", "close:exploding"]
    assert finance_tools_module._fallback_agent is None


def test_finance_agent_builder_keeps_model_debate_factory() -> None:
    finance = build_finance_research_agent()

    assert finance.debate_backend is None
    assert finance.debate_backend_factory is build_debate_backend


def test_debate_backend_uses_per_http_call_timeout_and_can_close(
    monkeypatch,
) -> None:  # noqa: ANN001
    captured: dict[str, object] = {}

    class HTTPClient:
        closed = False

        def close(self) -> None:
            self.closed = True

    http = HTTPClient()
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("FINANCE_DEBATE_MODEL_TIMEOUT_SECONDS", "91")
    monkeypatch.setenv("FINANCE_DEBATE_HTTP_CALL_TIMEOUT_SECONDS", "2.5")
    monkeypatch.setattr(deepseek_module, "load_local_env", lambda: None)
    monkeypatch.setattr(
        deepseek_module,
        "http_client",
        lambda **kwargs: captured.update(kwargs) or http,
    )

    backend = build_debate_backend()

    assert backend.timeout == 2.5
    assert backend.read_retries == 0
    assert captured["timeout"] == 2.5
    backend.close()
    assert http.closed


def test_runtime_backend_selection_uses_configured_model(monkeypatch) -> None:  # noqa: ANN001
    class HTTPClient:
        def close(self) -> None:
            return None

    notices: list[str] = []
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(deepseek_module, "load_local_env", lambda: None)
    monkeypatch.setattr(deepseek_module, "http_client", lambda **kwargs: HTTPClient())

    backend = build_model_backend(notices.append)

    assert isinstance(backend, deepseek_module.DeepSeekBackend)
    assert notices == []
    backend.close()


def test_runtime_backend_selection_keeps_fake_choice_in_composition_root(
    monkeypatch,
) -> None:  # noqa: ANN001
    notices: list[str] = []

    def unavailable_backend():  # noqa: ANN202
        raise RuntimeError("missing model api_key=bootstrap-secret-value")

    monkeypatch.setattr(deepseek_module, "DeepSeekBackend", unavailable_backend)

    backend = build_model_backend(notices.append)

    assert isinstance(backend, FakeBackend)
    assert len(notices) == 1
    assert "回退 FakeBackend" in notices[0]
    assert "bootstrap-secret-value" not in notices[0]
    assert "[REDACTED_SECRET]" in notices[0]
