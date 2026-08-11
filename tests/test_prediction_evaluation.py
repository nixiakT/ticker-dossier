from __future__ import annotations

from datetime import UTC, datetime

from ticker_dossier.cli.commands import CommandRouter
from ticker_dossier.research.models import Candle
from ticker_dossier.research.predictions import (
    evaluate_due_predictions,
    evaluation_history_period,
    load_predictions,
    record_prediction,
    save_predictions,
    select_due_close,
)
from ticker_dossier.runtime.tools import ToolRegistry


def test_select_due_close_uses_due_session_instead_of_latest_price() -> None:
    history = [
        Candle("2026-07-08", None, None, None, 105),
        Candle("2026-07-09", None, None, None, 110),
        Candle("2026-07-12", None, None, None, 95),
    ]

    price, as_of = select_due_close(history, "2026-07-08T00:00:00Z")

    assert price == 105
    assert as_of == "2026-07-08"


def test_select_due_close_uses_next_session_for_weekend_due_date() -> None:
    history = [
        Candle("2026-07-10", None, None, None, 100),
        Candle("2026-07-13", None, None, None, 103),
        Candle("2026-07-14", None, None, None, 108),
    ]

    price, as_of = select_due_close(history, "2026-07-11T00:00:00Z")

    assert price == 103
    assert as_of == "2026-07-13"


def test_due_evaluation_passes_record_due_time_to_historical_lookup(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "predictions.jsonl"
    record = record_prediction(
        symbol="AAPL",
        direction="up",
        horizon_days=7,
        confidence=0.7,
        thesis="unit",
        baseline_price=100,
        path=path,
    )
    record.due_at = "2026-07-08T00:00:00Z"
    save_predictions([record], path)
    requested: list[tuple[str, str]] = []

    def get_historical_price(symbol: str, due_at: str) -> tuple[float, str]:
        requested.append((symbol, due_at))
        return 105, "2026-07-08"

    evaluated, card = evaluate_due_predictions(
        get_historical_price=get_historical_price,
        now=datetime(2026, 7, 14, tzinfo=UTC),
        path=path,
    )

    assert requested == [("AAPL", "2026-07-08T00:00:00Z")]
    assert evaluated[0].evaluation_price == 105
    assert evaluated[0].evaluated_at == "2026-07-08"
    assert evaluated[0].hit is True
    assert card["accuracy"] == 1.0


def test_demo_evaluation_uses_latest_quote_only_for_not_due_record(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "predictions.jsonl"
    record = record_prediction(
        symbol="AAPL",
        direction="up",
        horizon_days=30,
        confidence=0.5,
        thesis="demo",
        baseline_price=100,
        path=path,
    )
    record.due_at = "2026-08-01T00:00:00Z"
    save_predictions([record], path)
    calls: list[str] = []

    evaluated, _ = evaluate_due_predictions(
        get_price=lambda symbol: (calls.append(f"latest:{symbol}") or 101, "2026-07-14"),
        get_historical_price=lambda symbol, due_at: (_ for _ in ()).throw(
            AssertionError(f"future due date must not be queried: {symbol} {due_at}")
        ),
        now=datetime(2026, 7, 14, tzinfo=UTC),
        path=path,
        include_not_due=True,
    )

    assert calls == ["latest:AAPL"]
    assert evaluated[0].evaluation_price == 101


def test_predict_eval_command_uses_history_and_never_latest_quote(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    import ticker_dossier.research.predictions as predictions

    path = tmp_path / "predictions.jsonl"
    monkeypatch.setattr(predictions, "PREDICTION_PATH", path)
    record = record_prediction(
        symbol="AAPL",
        direction="up",
        horizon_days=7,
        confidence=0.7,
        thesis="command integration",
        baseline_price=100,
        path=path,
    )
    record.due_at = "2026-07-08T00:00:00Z"
    save_predictions([record], path)

    class Provider:
        history_calls: list[tuple[str, str, str]] = []

        def get_quote(self, symbol: str):  # noqa: ANN201
            raise AssertionError(f"latest quote must not be used for {symbol}")

        def get_history(self, symbol: str, period: str, interval: str) -> list[Candle]:
            self.history_calls.append((symbol, period, interval))
            return [
                Candle("2026-07-08", None, None, None, 105),
                Candle("2026-07-14", None, None, None, 95),
            ]

    class Finance:
        provider = Provider()

    output = CommandRouter(ToolRegistry(), finance_agent=Finance()).handle("/predict eval").output  # type: ignore[arg-type]
    evaluated = load_predictions(path)[0]

    assert Finance.provider.history_calls == [("AAPL", "3mo", "1d")]
    assert evaluated.evaluation_price == 105
    assert evaluated.evaluated_at == "2026-07-08"
    assert "accuracy: 100.00%" in output


def test_default_ledger_migrates_from_project_directory(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    import ticker_dossier.research.predictions as predictions

    legacy = tmp_path / "project" / ".finance_agent" / "predictions.jsonl"
    target = tmp_path / "home" / ".finance-agent" / "predictions.jsonl"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        '{"id":"old","symbol":"AAPL","direction":"up","horizon_days":7,'
        '"confidence":0.7,"thesis":"legacy","baseline_price":100,'
        '"baseline_as_of":"2026-07-01","created_at":"2026-07-01T00:00:00Z",'
        '"due_at":"2026-07-08T00:00:00Z"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(predictions, "LEGACY_PREDICTION_PATH", legacy)
    monkeypatch.setattr(predictions, "DEFAULT_PREDICTION_PATH", target)
    monkeypatch.setattr(predictions, "PREDICTION_PATH", target)
    monkeypatch.delenv("FINANCE_PREDICTION_PATH", raising=False)

    records = load_predictions()

    assert [record.id for record in records] == ["old"]
    assert target.exists()
    assert legacy.exists()


def test_prediction_path_can_point_at_persistent_volume(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    import ticker_dossier.research.predictions as predictions

    target = tmp_path / "mounted-volume" / "predictions.jsonl"
    monkeypatch.setattr(predictions, "PREDICTION_PATH", predictions.DEFAULT_PREDICTION_PATH)
    monkeypatch.setenv("FINANCE_PREDICTION_PATH", str(target))
    monkeypatch.setattr(predictions, "LEGACY_PREDICTION_PATH", tmp_path / "missing.jsonl")

    record_prediction(
        symbol="MSFT",
        direction="up",
        horizon_days=30,
        confidence=0.6,
        thesis="persistent",
        baseline_price=200,
    )

    assert target.exists()
    assert load_predictions()[0].symbol == "MSFT"


def test_history_period_expands_when_evaluation_is_delayed() -> None:
    now = datetime(2026, 7, 14, tzinfo=UTC)

    assert evaluation_history_period("2026-07-01T00:00:00Z", now) == "3mo"
    assert evaluation_history_period("2025-07-01T00:00:00Z", now) == "2y"
