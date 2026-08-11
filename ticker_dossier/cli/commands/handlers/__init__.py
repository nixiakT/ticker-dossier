"""Built-in slash-command handlers grouped by capability."""

from .integrations import INTEGRATION_HANDLER_METHODS, IntegrationCommandHandlers
from .portfolio import PORTFOLIO_HANDLER_METHODS, PortfolioCommandHandlers
from .research import RESEARCH_HANDLER_METHODS, ResearchCommandHandlers
from .session import SESSION_HANDLER_METHODS, SessionCommandHandlers
from .workflow import WORKFLOW_HANDLER_METHODS, WorkflowCommandHandlers


HANDLER_METHODS = {
    **SESSION_HANDLER_METHODS,
    **RESEARCH_HANDLER_METHODS,
    **PORTFOLIO_HANDLER_METHODS,
    **INTEGRATION_HANDLER_METHODS,
    **WORKFLOW_HANDLER_METHODS,
}


__all__ = [
    "HANDLER_METHODS",
    "IntegrationCommandHandlers",
    "PortfolioCommandHandlers",
    "ResearchCommandHandlers",
    "SessionCommandHandlers",
    "WorkflowCommandHandlers",
]
