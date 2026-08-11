"""Prediction ledger and ex-post evaluation for finance research."""
from __future__ import annotations

import json
import math
import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ticker_dossier.config import load_local_env


LEGACY_PREDICTION_PATH = Path.cwd() / ".finance_agent" / "predictions.jsonl"
DEFAULT_PREDICTION_PATH = Path.home() / ".finance-agent" / "predictions.jsonl"
PREDICTION_PATH = DEFAULT_PREDICTION_PATH


@dataclass
class PredictionRecord:
    id: str
    symbol: str
    direction: str
    horizon_days: int
    confidence: float
    thesis: str
    baseline_price: float | None
    baseline_as_of: str
    created_at: str
    due_at: str
    source: str = ""
    confidence_kind: str = "legacy_unspecified"
    signal_strength: float | None = None
    calibrated_probability: float | None = None
    calibration_samples: int = 0
    calibration_hits: int = 0
    calibration_interval_low: float | None = None
    calibration_interval_high: float | None = None
    calibration_method: str = ""
    evaluated_at: str = ""
    evaluation_price: float | None = None
    return_pct: float | None = None
    hit: bool | None = None
    score: float | None = None
    notes: str = ""


def record_prediction(
    *,
    symbol: str,
    direction: str,
    horizon_days: int,
    confidence: float,
    thesis: str,
    baseline_price: float | None,
    baseline_as_of: str = "",
    source: str = "",
    confidence_kind: str = "legacy_unspecified",
    signal_strength: float | None = None,
    calibrated_probability: float | None = None,
    calibration_samples: int = 0,
    calibration_hits: int = 0,
    calibration_interval_low: float | None = None,
    calibration_interval_high: float | None = None,
    calibration_method: str = "",
    path: Path | None = None,
) -> PredictionRecord:
    path = _prediction_path(path)
    now = datetime.now(UTC).replace(microsecond=0)
    horizon = max(int(horizon_days), 1)
    record = PredictionRecord(
        id=uuid.uuid4().hex[:12],
        symbol=symbol.strip().upper(),
        direction=_normalize_direction(direction),
        horizon_days=horizon,
        confidence=_normalize_confidence(confidence),
        thesis=thesis.strip(),
        baseline_price=baseline_price,
        baseline_as_of=baseline_as_of,
        created_at=_iso(now),
        due_at=_iso(now + timedelta(days=horizon)),
        source=source,
        confidence_kind=str(confidence_kind or "legacy_unspecified"),
        signal_strength=_optional_confidence(signal_strength),
        calibrated_probability=_optional_confidence(calibrated_probability),
        calibration_samples=max(int(calibration_samples), 0),
        calibration_hits=max(int(calibration_hits), 0),
        calibration_interval_low=_optional_confidence(calibration_interval_low),
        calibration_interval_high=_optional_confidence(calibration_interval_high),
        calibration_method=str(calibration_method or ""),
    )
    _append(record, path)
    return record


def load_predictions(path: Path | None = None) -> list[PredictionRecord]:
    path = _prediction_path(path)
    if not path.exists():
        return []
    rows: list[PredictionRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            rows.append(PredictionRecord(**data))
        except (TypeError, json.JSONDecodeError):
            continue
    return rows


def save_predictions(records: list[PredictionRecord], path: Path | None = None) -> None:
    path = _prediction_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(record.__dict__, ensure_ascii=False, separators=(",", ":")) for record in records)
    path.write_text(text + ("\n" if text else ""), encoding="utf-8")


def evaluate_prediction(
    record: PredictionRecord,
    *,
    evaluation_price: float | None,
    evaluated_at: str = "",
) -> PredictionRecord:
    record.evaluated_at = evaluated_at or _iso(datetime.now(UTC).replace(microsecond=0))
    record.evaluation_price = evaluation_price
    if record.baseline_price in (None, 0) or evaluation_price is None:
        record.notes = "missing baseline or evaluation price"
        record.hit = None
        record.return_pct = None
        record.score = None
        return record
    ret = (float(evaluation_price) - float(record.baseline_price)) / float(record.baseline_price) * 100
    record.return_pct = ret
    record.hit = _hit(record.direction, ret)
    record.score = prediction_score(record.direction, ret, record.confidence)
    return record


def evaluate_due_predictions(
    *,
    get_price: Any | None = None,
    get_historical_price: Any | None = None,
    now: datetime | None = None,
    path: Path | None = None,
    include_not_due: bool = False,
) -> tuple[list[PredictionRecord], dict[str, Any]]:
    if get_price is None and get_historical_price is None:
        raise ValueError("evaluation requires a historical or latest-price callback")
    path = _prediction_path(path)
    current = now or datetime.now(UTC).replace(microsecond=0)
    records = load_predictions(path)
    changed = False
    evaluated: list[PredictionRecord] = []
    for record in records:
        if record.evaluated_at:
            continue
        try:
            due_at = _parse_iso(record.due_at)
        except (TypeError, ValueError):
            due_at = datetime.min.replace(tzinfo=UTC)
        if not include_not_due and due_at > current:
            continue
        try:
            if include_not_due and due_at > current and get_price is not None:
                price, as_of = get_price(record.symbol)
            elif get_historical_price is not None:
                price, as_of = get_historical_price(record.symbol, record.due_at)
            else:
                price, as_of = get_price(record.symbol)
            evaluate_prediction(record, evaluation_price=price, evaluated_at=as_of or _iso(current))
        except Exception as exc:  # noqa: BLE001 - one bad quote must not abort the ledger
            record.notes = f"evaluation failed: {exc}"
            record.hit = None
            record.score = None
            record.return_pct = None
        evaluated.append(record)
        changed = True
    if changed:
        save_predictions(records, path)
    return evaluated, scorecard(records)


def evaluation_history_period(due_at: str, now: datetime | None = None) -> str:
    """Choose a history window that still contains a delayed prediction due date."""
    due = _parse_iso(due_at)
    current = now or datetime.now(UTC)
    age_days = max((current - due).days, 0)
    if age_days <= 60:
        return "3mo"
    if age_days <= 150:
        return "6mo"
    if age_days <= 330:
        return "1y"
    if age_days <= 690:
        return "2y"
    if age_days <= 1750:
        return "5y"
    return "max"


def select_due_close(history: list[Any], due_at: str) -> tuple[float, str]:
    """Return the first available market close on or after the due date."""
    due_date = _parse_iso(due_at).date()
    candidates: list[tuple[str, float]] = []
    for candle in history:
        close = getattr(candle, "close", None)
        raw_date = str(getattr(candle, "date", ""))
        if close is None or not raw_date:
            continue
        try:
            market_date = datetime.fromisoformat(raw_date[:10]).date()
        except ValueError:
            continue
        if market_date >= due_date:
            candidates.append((market_date.isoformat(), float(close)))
    if not candidates:
        raise ValueError(f"no market close available on or after prediction due date {due_date.isoformat()}")
    as_of, close = min(candidates, key=lambda item: item[0])
    return close, as_of


def scorecard(records: list[PredictionRecord]) -> dict[str, Any]:
    evaluated = [record for record in records if record.hit is not None]
    if not evaluated:
        return {"evaluated": 0, "accuracy": None, "avg_score": None, "avg_return_pct": None}
    hits = sum(1 for record in evaluated if record.hit)
    scores = [record.score for record in evaluated if record.score is not None]
    returns = [record.return_pct for record in evaluated if record.return_pct is not None]
    return {
        "evaluated": len(evaluated),
        "accuracy": hits / len(evaluated),
        "avg_score": sum(scores) / len(scores) if scores else None,
        "avg_return_pct": sum(returns) / len(returns) if returns else None,
    }


def scorecard_by_direction(records: list[PredictionRecord]) -> dict[str, dict[str, Any]]:
    cards: dict[str, dict[str, Any]] = {}
    for direction in ("up", "down", "neutral"):
        subset = [record for record in records if record.direction == direction and record.hit is not None]
        if subset:
            cards[direction] = scorecard(subset)
    return cards


def render_predictions(records: list[PredictionRecord], limit: int = 20) -> str:
    if not records:
        return "Prediction ledger: empty."
    lines = ["Prediction ledger:"]
    for record in records[-max(limit, 1):]:
        status = "evaluated" if record.evaluated_at else "open"
        hit = "hit" if record.hit else ("miss" if record.hit is False else "pending")
        lines.append(
            f"- {record.id} {record.symbol} {record.direction} {record.horizon_days}d "
            f"{_confidence_summary(record)} base={_fmt(record.baseline_price)} due={record.due_at} "
            f"{status}/{hit} ret={_fmt(record.return_pct)} thesis={record.thesis[:90]}"
        )
    return "\n".join(lines)


def render_prediction_record(record: PredictionRecord) -> str:
    confidence_kind = getattr(record, "confidence_kind", "legacy_unspecified")
    signal_strength = getattr(record, "signal_strength", None)
    calibrated_probability = getattr(record, "calibrated_probability", None)
    calibration_samples = int(getattr(record, "calibration_samples", 0) or 0)
    calibration_hits = int(getattr(record, "calibration_hits", 0) or 0)
    lines = [
        "Prediction recorded:",
        f"- id: {record.id}",
        f"- symbol: {record.symbol}",
        f"- direction: {record.direction}",
        f"- horizon_days: {record.horizon_days}",
    ]
    if confidence_kind == "historical_calibrated" and calibrated_probability is not None:
        lines.append(f"- historical_calibrated_hit_rate: {calibrated_probability:.1%}")
        interval = _interval(
            getattr(record, "calibration_interval_low", None),
            getattr(record, "calibration_interval_high", None),
        )
        lines.append(
            f"- calibration: {calibration_hits}/{calibration_samples} historical hits"
            f"{interval}; {getattr(record, 'calibration_method', '')}"
        )
        if signal_strength is not None:
            lines.append(f"- raw_signal_strength: {signal_strength * 100:.0f}/100 (not a probability)")
    else:
        strength = signal_strength if signal_strength is not None else record.confidence
        source = {
            "heuristic_signal": "heuristic",
            "user_supplied": "user supplied",
            "model_supplied": "model supplied",
            "legacy_unspecified": "legacy/unspecified",
        }.get(confidence_kind, confidence_kind)
        lines.append(f"- signal_strength: {strength * 100:.0f}/100 ({source}; not a statistical probability)")
        if calibration_samples:
            lines.append(
                f"- calibration: insufficient non-overlapping samples "
                f"(n={calibration_samples}, require n>=30)"
            )
    lines.extend([
        f"- baseline: {record.baseline_price} ({record.baseline_as_of})",
        f"- due_at: {record.due_at}",
    ])
    return "\n".join(lines)


def render_learning_report(records: list[PredictionRecord]) -> str:
    card = scorecard(records)
    if not card.get("evaluated"):
        return "\n".join([
            "Prediction learning report:",
            "- no evaluated predictions yet",
            "- record forecasts with /predict record, then run /predict eval after the horizon",
        ])

    evaluated = [record for record in records if record.hit is not None]
    high_estimate_misses = [
        record for record in evaluated
        if record.hit is False and record.confidence >= 0.7
    ]
    buckets = scorecard_by_direction(records)

    lines = [render_scorecard(card), "", "Direction buckets:"]
    for direction, bucket in buckets.items():
        lines.append(
            f"- {direction}: n={bucket['evaluated']} accuracy={_pct(bucket.get('accuracy'))} "
            f"avg_score={_fmt(bucket.get('avg_score'))} avg_return={_pct_value(bucket.get('avg_return_pct'))}"
        )

    lines.extend(["", "Learning notes:"])
    accuracy = card.get("accuracy") or 0
    avg_score = card.get("avg_score") or 0
    if accuracy < 0.5:
        lines.append("- directional accuracy is below 50%; require stronger base-rate and counterargument checks")
    elif avg_score < 0:
        lines.append("- accuracy is acceptable but confidence calibration is weak; lower confidence on thin evidence")
    else:
        lines.append("- current evaluated set has positive calibration; keep the same evidence checklist and expand sample size")

    weakest = _weakest_bucket(buckets)
    if weakest:
        lines.append(f"- weakest direction bucket: {weakest}; review theses in that bucket before similar calls")

    if high_estimate_misses:
        lines.append("- high-estimate misses to review (check each estimate basis):")
        for record in high_estimate_misses[:3]:
            lines.append(
                f"  - {record.id} {record.symbol} {record.direction} "
                f"{_confidence_summary(record)} ret={_pct_value(record.return_pct)} thesis={record.thesis[:80]}"
            )
    else:
        lines.append("- no high-estimate misses in the evaluated sample")

    return "\n".join(lines)


def render_scorecard(card: dict[str, Any]) -> str:
    if not card.get("evaluated"):
        return "Prediction scorecard: no evaluated predictions yet."
    accuracy = card.get("accuracy")
    avg_score = card.get("avg_score")
    avg_return = card.get("avg_return_pct")
    return "\n".join([
        "Prediction scorecard:",
        f"- evaluated: {card['evaluated']}",
        f"- directional accuracy: {_pct(accuracy)}",
        f"- avg estimate-weighted score: {_fmt(avg_score)}",
        f"- avg realized return: {_pct_value(avg_return)}",
    ])


def prediction_score(direction: str, return_pct: float, confidence: float) -> float:
    """Confidence-weighted score in [-1, 1]."""
    hit = _hit(_normalize_direction(direction), return_pct)
    edge = min(abs(return_pct) / 10.0, 1.0)
    confidence = _normalize_confidence(confidence)
    signed = 1.0 if hit else -1.0
    return signed * (0.4 + 0.6 * edge) * confidence


def _prediction_path(path: Path | None) -> Path:
    if path is not None:
        return Path(path).expanduser()
    uses_default_location = PREDICTION_PATH == DEFAULT_PREDICTION_PATH
    if not uses_default_location:
        target = Path(PREDICTION_PATH).expanduser()
    else:
        load_local_env()
        configured = os.environ.get("FINANCE_PREDICTION_PATH", "").strip()
        target = Path(configured).expanduser() if configured else DEFAULT_PREDICTION_PATH
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            if configured:
                raise
            return LEGACY_PREDICTION_PATH
    if uses_default_location and not target.exists() and LEGACY_PREDICTION_PATH.exists():
        shutil.copy2(LEGACY_PREDICTION_PATH, target)
    return target


def _append(record: PredictionRecord, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record.__dict__, ensure_ascii=False, separators=(",", ":")) + "\n")


def _hit(direction: str, return_pct: float) -> bool:
    if direction == "up":
        return return_pct > 0
    if direction == "down":
        return return_pct < 0
    return abs(return_pct) <= 2.0


def _normalize_direction(value: str) -> str:
    lowered = value.strip().lower()
    if lowered in {"up", "bull", "bullish", "涨", "看涨", "上涨"}:
        return "up"
    if lowered in {"down", "bear", "bearish", "跌", "看跌", "下跌"}:
        return "down"
    return "neutral"


def _normalize_confidence(value: float) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        confidence = 0.5
    if confidence > 1:
        confidence /= 100.0
    if math.isnan(confidence):
        confidence = 0.5
    return min(max(confidence, 0.0), 1.0)


def _optional_confidence(value: float | None) -> float | None:
    return None if value is None else _normalize_confidence(value)


def _confidence_summary(record: PredictionRecord) -> str:
    confidence_kind = getattr(record, "confidence_kind", "legacy_unspecified")
    calibrated_probability = getattr(record, "calibrated_probability", None)
    if confidence_kind == "historical_calibrated" and calibrated_probability is not None:
        samples = int(getattr(record, "calibration_samples", 0) or 0)
        return f"calibrated_hit_rate={calibrated_probability:.1%}(n={samples})"
    signal_strength = getattr(record, "signal_strength", None)
    strength = signal_strength if signal_strength is not None else record.confidence
    return f"signal={strength:.2f}({confidence_kind};not_probability)"


def _interval(low: float | None, high: float | None) -> str:
    if low is None or high is None:
        return ""
    return f", 95% interval {low:.1%}-{high:.1%}"


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_iso(value: str) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=UTC)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _fmt(value: Any) -> str:
    if value is None:
        return "NA"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def _pct(value: Any) -> str:
    if value is None:
        return "NA"
    return f"{float(value) * 100:.2f}%"


def _pct_value(value: Any) -> str:
    if value is None:
        return "NA"
    return f"{float(value):.2f}%"


def _weakest_bucket(cards: dict[str, dict[str, Any]]) -> str:
    if not cards:
        return ""
    ranked = sorted(
        cards.items(),
        key=lambda item: (
            item[1].get("avg_score") if item[1].get("avg_score") is not None else -999,
            item[1].get("accuracy") if item[1].get("accuracy") is not None else -999,
        ),
    )
    direction, card = ranked[0]
    return f"{direction} accuracy={_pct(card.get('accuracy'))} avg_score={_fmt(card.get('avg_score'))}"
