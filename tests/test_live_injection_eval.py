from __future__ import annotations

import os

import pytest

from ticker_dossier.config import load_local_env
from evals.injection import run_injection_eval


load_local_env()


@pytest.mark.skipif(
    os.environ.get("FINANCE_RUN_LIVE_EVAL") != "1",
    reason="set FINANCE_RUN_LIVE_EVAL=1 to spend three configured model calls",
)
def test_configured_model_resists_repeated_financial_pressure() -> None:
    from ticker_dossier.llm.deepseek import DeepSeekBackend

    result = run_injection_eval(DeepSeekBackend())

    assert result.passed, result.checks
