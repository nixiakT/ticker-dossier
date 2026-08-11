"""Suite-wide isolation for every persistent developer-owned state store."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_persistent_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never let a test discover, migrate, or mutate real local application state."""
    import ticker_dossier.integrations.scheduler as scheduler
    import ticker_dossier.integrations.wechat as wechat
    import ticker_dossier.research.learning.memory as evolution
    import ticker_dossier.research.learning.history as history_learning
    import ticker_dossier.portfolio.service as portfolio
    import ticker_dossier.research.learning.predictions as predictions
    import ticker_dossier.runtime.memory as runtime_memory

    root = tmp_path / "persistent-state"
    fake_home = root / "home"
    workspace = root / "workspace" / ".finance_agent"
    user_state = fake_home / ".finance-agent"
    portfolio_dir = user_state / "portfolios"
    prediction_path = user_state / "predictions.jsonl"

    # Path.home() is evaluated lazily by CLI history and custom-command paths.
    monkeypatch.setenv("HOME", str(fake_home))

    monkeypatch.setenv("FINANCE_PORTFOLIO_DIR", str(portfolio_dir))
    monkeypatch.setattr(portfolio, "DEFAULT_PORTFOLIO_DIR", portfolio_dir)
    monkeypatch.setattr(portfolio, "PORTFOLIO_DIR", portfolio_dir)
    monkeypatch.setattr(portfolio, "LEGACY_PORTFOLIO_DIR", workspace)

    monkeypatch.setenv("FINANCE_PREDICTION_PATH", str(prediction_path))
    monkeypatch.setattr(predictions, "DEFAULT_PREDICTION_PATH", prediction_path)
    monkeypatch.setattr(predictions, "PREDICTION_PATH", prediction_path)
    monkeypatch.setattr(predictions, "LEGACY_PREDICTION_PATH", workspace / "predictions.jsonl")

    monkeypatch.setattr(evolution, "MEMORY_DIR", workspace)
    monkeypatch.setattr(evolution, "MEMORY_PATH", workspace / "finance_memory.jsonl")
    monkeypatch.setattr(history_learning, "LEARNING_DIR", workspace)
    monkeypatch.setattr(history_learning, "LEARNING_PATH", workspace / "history_learning.jsonl")
    monkeypatch.setattr(
        history_learning,
        "SKILL_PATH",
        root / "skills" / "finance-history-learning" / "SKILL.md",
    )
    monkeypatch.setattr(scheduler, "JOBS_PATH", workspace / "scheduled_jobs.json")
    monkeypatch.setattr(wechat, "OUTBOX_DIR", workspace / "wechat_outbox")
    monkeypatch.setattr(
        runtime_memory,
        "DEFAULT_MEMORY_PATH",
        workspace / "project_memory.md",
    )
    monkeypatch.setattr(
        runtime_memory,
        "DEFAULT_KV_MEMORY_PATH",
        workspace / "project_memory.json",
    )
    monkeypatch.setattr(runtime_memory, "LEGACY_MEMORY_PATH", root / "legacy" / "MEMORY.md")

    # Tests must never discover a configured external delivery endpoint.
    monkeypatch.setenv("FINANCE_WECHAT_MODE", "dry-run")
    monkeypatch.delenv("FINANCE_WECHAT_WEBHOOK", raising=False)
    monkeypatch.delenv("FINANCE_WECHAT_RELAY_URL", raising=False)
