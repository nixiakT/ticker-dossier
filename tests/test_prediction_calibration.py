from __future__ import annotations

from datetime import date, timedelta

import pytest

from ticker_dossier.research.agent import FinanceResearchAgent
from ticker_dossier.research.history_learning import calibrate_momentum_signal
from ticker_dossier.research.models import Candle
from ticker_dossier.research.predictions import render_prediction_record


def _rising_candles(count: int) -> list[Candle]:
    start = date(2020, 1, 1)
    return [
        Candle(
            date=(start + timedelta(days=index)).isoformat(),
            open=None,
            high=None,
            low=None,
            close=100 + index,
        )
        for index in range(count)
    ]


def test_walk_forward_calibration_uses_disjoint_historical_windows() -> None:
    calibration = calibrate_momentum_signal(_rising_candles(900), horizon_days=30)

    assert calibration.direction == "up"
    assert calibration.sample_count >= 30
    assert calibration.hits == calibration.sample_count
    assert calibration.calibrated_probability is not None
    assert calibration.calibrated_probability > 0.95
    assert calibration.interval_low is not None
    assert calibration.interval_high is not None
    assert calibration.horizon_sessions == 21


def test_walk_forward_calibration_withholds_probability_for_small_sample() -> None:
    calibration = calibrate_momentum_signal(_rising_candles(180), horizon_days=30)

    assert calibration.sample_count < 30
    assert calibration.calibrated_probability is None
    assert 0 <= calibration.signal_strength <= 0.85


def test_prediction_record_distinguishes_calibrated_rate_from_signal_strength(
    tmp_path, monkeypatch: pytest.MonkeyPatch  # noqa: ANN001
) -> None:
    import ticker_dossier.research.predictions as predictions

    class Provider:
        name = "HISTORY"

        def get_history(self, symbol: str, period: str, interval: str) -> list[Candle]:
            assert period == "5y"
            return _rising_candles(900)

    monkeypatch.setattr(predictions, "PREDICTION_PATH", tmp_path / "predictions.jsonl")
    agent = FinanceResearchAgent(provider=Provider())  # type: ignore[arg-type]

    calibrated = agent.create_prediction_record(symbol="AAPL", horizon_days=30, thesis="calibrated")
    subjective = agent.create_prediction_record(
        symbol="AAPL",
        direction="up",
        horizon_days=30,
        signal_strength=0.79,
        signal_source="user_supplied",
        use_calibration=False,
        thesis="subjective",
    )

    calibrated_output = render_prediction_record(calibrated)
    subjective_output = render_prediction_record(subjective)
    assert calibrated.confidence_kind == "historical_calibrated"
    assert "historical_calibrated_hit_rate" in calibrated_output
    assert "historical hits" in calibrated_output
    assert subjective.confidence_kind == "user_supplied"
    assert "signal_strength: 79/100" in subjective_output
    assert "not a statistical probability" in subjective_output
    assert "historical_calibrated_hit_rate" not in subjective_output
