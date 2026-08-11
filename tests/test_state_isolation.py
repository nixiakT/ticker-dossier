from __future__ import annotations

from pathlib import Path


def test_default_persistent_writes_are_confined_to_pytest_tmp_path(tmp_path: Path) -> None:
    """Exercise every default writer without touching developer-owned state."""
    import ticker_dossier.integrations.scheduler as scheduler
    import ticker_dossier.integrations.wechat as wechat
    import ticker_dossier.research.learning.memory as evolution
    import ticker_dossier.research.learning.history as history_learning
    import ticker_dossier.portfolio.service as portfolio
    import ticker_dossier.research.learning.predictions as predictions
    import ticker_dossier.runtime.memory as runtime_memory

    predictions.record_prediction(
        symbol="TEST",
        direction="neutral",
        horizon_days=1,
        confidence=0.5,
        thesis="pytest state-isolation sentinel",
        baseline_price=None,
    )
    memory_path = evolution.add_memory("pytest state-isolation sentinel")
    rule = history_learning.learn_from_history("TEST", [], horizon_days=1)
    learning_path = history_learning.save_learning(rule)
    skill_path = history_learning.update_history_learning_skill(rule)
    scheduler.add_job("research", {"symbols": "TEST"}, interval_minutes=60)
    message = wechat.send_text("pytest state-isolation sentinel")
    portfolio.create_account(name="state-isolation", initial_cash=1_000)
    project_memory_path = runtime_memory.Memory().write("pytest state-isolation sentinel")
    kv_memory_path = runtime_memory.KVMemory().remember("pytest", "state-isolation sentinel")

    paths = (
        predictions.PREDICTION_PATH,
        memory_path,
        learning_path,
        skill_path,
        scheduler.JOBS_PATH,
        message.path,
        portfolio.account_path("state-isolation"),
        project_memory_path,
        kv_memory_path,
        Path.home(),
    )
    sandbox = tmp_path.resolve()
    assert all(path is not None and path.resolve().is_relative_to(sandbox) for path in paths)
    assert message.mode == "dry-run"
