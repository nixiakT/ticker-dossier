"""Suite-wide isolation for persistent portfolio state."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_paper_portfolios(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never let a test discover or mutate a developer's real paper ledger."""
    import ticker_dossier.research.paper_portfolio as portfolio

    root = tmp_path / "paper-portfolio-state"
    user_dir = root / "user"
    workspace_dir = root / "workspace"
    monkeypatch.setenv("FINANCE_PORTFOLIO_DIR", str(user_dir))
    monkeypatch.setattr(portfolio, "DEFAULT_PORTFOLIO_DIR", user_dir)
    monkeypatch.setattr(portfolio, "PORTFOLIO_DIR", user_dir)
    monkeypatch.setattr(portfolio, "LEGACY_PORTFOLIO_DIR", workspace_dir)
