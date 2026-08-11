"""Catalog-driven interactive slash-command router."""
from __future__ import annotations

import shlex
from typing import Callable

from ticker_dossier.cli.command_catalog import command_specs, resolve_command
from ticker_dossier.cli.command_types import CommandResult
from ticker_dossier.cli.handlers import (
    HANDLER_METHODS,
    IntegrationCommandHandlers,
    PortfolioCommandHandlers,
    ResearchCommandHandlers,
    SessionCommandHandlers,
    WorkflowCommandHandlers,
)
from ticker_dossier.cli.handlers._shared import json_preview, msg, preview
from ticker_dossier.integrations.http import proxy_label, test_connectivity
from ticker_dossier.integrations.scheduler import add_job, list_jobs, render_jobs, run_due_jobs
from ticker_dossier.integrations.wechat import connector_status, send_markdown, send_text
from ticker_dossier.research import web as web_services
from ticker_dossier.research.agent import FinanceResearchAgent
from ticker_dossier.research.data import ProviderError
from ticker_dossier.research.evolution import add_memory, extract_learning, render_memories
from ticker_dossier.research.paper_portfolio import PortfolioError
from ticker_dossier.research.predictions import (
    evaluation_history_period,
    evaluate_due_predictions,
    load_predictions,
    record_prediction,
    render_learning_report,
    render_prediction_record,
    render_predictions,
    render_scorecard,
    select_due_close,
)
from ticker_dossier.runtime.memory import Memory
from ticker_dossier.runtime.tools import ToolRegistry
from ticker_dossier.security import SecurityError, guard_write


class CommandRouter(
    SessionCommandHandlers,
    ResearchCommandHandlers,
    PortfolioCommandHandlers,
    IntegrationCommandHandlers,
    WorkflowCommandHandlers,
):
    """Resolve one slash command through the executable command catalog."""

    def __init__(
        self,
        registry: ToolRegistry,
        finance_agent: FinanceResearchAgent | None = None,
        trace: Callable[[str, str], None] | None = None,
        *,
        web_search_service: Callable[[str, int], str] | None = None,
        web_fetch_service: Callable[[str, int], str] | None = None,
    ):
        self.registry = registry
        registered_services = registry.get_service("research")
        registered_finance = getattr(registered_services, "finance", None)
        self.finance = finance_agent or registered_finance or FinanceResearchAgent()
        self.trace = trace

        # Composition dependencies are captured at construction time.  Keeping
        # these here preserves the public router API and gives tests/app shells
        # a narrow injection seam without handler modules importing adapters.
        self.web_search_service = web_search_service or web_services.web_search
        self.web_fetch_service = web_fetch_service or web_services.web_fetch
        self.write_guard = guard_write
        self.proxy_label = proxy_label
        self.test_connectivity = test_connectivity
        self.connector_status = connector_status
        self.send_markdown = send_markdown
        self.send_text = send_text
        self.memory_factory = Memory
        self.add_memory = add_memory
        self.extract_learning = extract_learning
        self.render_memories = render_memories
        self.evaluation_history_period = evaluation_history_period
        self.evaluate_due_predictions = evaluate_due_predictions
        self.load_predictions = load_predictions
        self.record_prediction = record_prediction
        self.render_learning_report = render_learning_report
        self.render_prediction_record = render_prediction_record
        self.render_predictions = render_predictions
        self.render_scorecard = render_scorecard
        self.select_due_close = select_due_close
        self.add_job = add_job
        self.list_jobs = list_jobs
        self.render_jobs = render_jobs
        self.run_due_jobs = run_due_jobs
        self._handlers = {
            key: getattr(self, method_name)
            for key, method_name in HANDLER_METHODS.items()
        }
        self._validate_handler_catalog()

    def handle(self, raw: str, think_enabled: str | bool = False) -> CommandResult:
        text = raw.strip()
        if not text.startswith("/"):
            return CommandResult(False)
        try:
            parts = shlex.split(text)
        except ValueError as exc:
            return CommandResult(True, f"命令解析失败：{exc}")
        if not parts:
            return CommandResult(False)

        spec = resolve_command(parts[0])
        if spec is None:
            return CommandResult(True, msg(
                f"Unknown command: {parts[0].lower()}\nType /help for available commands.",
                f"未知命令：{parts[0].lower()}\n输入 /help 查看可用命令。",
            ))
        handler = self._handlers[spec.handler_key]
        try:
            result = handler(parts[1:], think_enabled)
        except ProviderError as exc:
            return CommandResult(True, f"数据获取失败：{preview(exc, 360)}")
        except (PortfolioError, SecurityError, ValueError) as exc:
            return CommandResult(True, str(exc))
        if isinstance(result, CommandResult):
            return result
        return CommandResult(True, str(result))

    def handler_keys(self) -> tuple[str, ...]:
        """Expose the bound handler surface for diagnostics and consistency tests."""
        return tuple(self._handlers)

    def _validate_handler_catalog(self) -> None:
        catalog_keys = {spec.handler_key for spec in command_specs()}
        handler_keys = set(self._handlers)
        if catalog_keys != handler_keys:
            missing = sorted(catalog_keys - handler_keys)
            orphaned = sorted(handler_keys - catalog_keys)
            raise RuntimeError(
                f"command handler/catalog mismatch: missing={missing}, orphaned={orphaned}"
            )

    def _trace_tool(self, name: str, arguments: dict) -> None:
        if self.trace:
            self.trace("tool", f"{name} {json_preview(arguments)}")

    def _with_result_trace(self, name: str, output: str) -> str:
        if self.trace:
            self.trace("tool result", f"{name} -> {preview(output)}")
        return output


__all__ = ["CommandResult", "CommandRouter"]
