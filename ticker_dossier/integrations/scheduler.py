"""File-backed local scheduled jobs."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable


JOBS_PATH = Path(".finance_agent") / "scheduled_jobs.json"


@dataclass
class ScheduledJob:
    id: str
    kind: str
    payload: dict[str, str]
    interval_minutes: int
    next_run_at: str
    enabled: bool = True
    last_run_at: str = ""
    last_status: str = ""


def add_job(
    kind: str,
    payload: dict[str, str],
    interval_minutes: int,
    start_at: datetime | None = None,
    path: Path | None = None,
) -> ScheduledJob:
    path = path or JOBS_PATH
    now = datetime.now(UTC).replace(microsecond=0)
    job = ScheduledJob(
        id=uuid.uuid4().hex[:10],
        kind=kind,
        payload={str(k): str(v) for k, v in payload.items()},
        interval_minutes=max(int(interval_minutes), 1),
        next_run_at=_iso(start_at or now),
    )
    jobs = list_jobs(path)
    jobs.append(job)
    save_jobs(jobs, path)
    return job


def list_jobs(path: Path | None = None) -> list[ScheduledJob]:
    path = path or JOBS_PATH
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    jobs: list[ScheduledJob] = []
    for item in data if isinstance(data, list) else []:
        try:
            jobs.append(ScheduledJob(**item))
        except TypeError:
            continue
    return jobs


def save_jobs(jobs: list[ScheduledJob], path: Path | None = None) -> None:
    path = path or JOBS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([job.__dict__ for job in jobs], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def due_jobs(now: datetime | None = None, path: Path | None = None) -> list[ScheduledJob]:
    current = now or datetime.now(UTC).replace(microsecond=0)
    return [job for job in list_jobs(path) if job.enabled and _parse_iso(job.next_run_at) <= current]


def run_due_jobs(
    runner: Callable[[ScheduledJob], str],
    now: datetime | None = None,
    path: Path | None = None,
) -> list[tuple[ScheduledJob, str]]:
    path = path or JOBS_PATH
    current = now or datetime.now(UTC).replace(microsecond=0)
    jobs = list_jobs(path)
    results: list[tuple[ScheduledJob, str]] = []
    for job in jobs:
        if not job.enabled or _parse_iso(job.next_run_at) > current:
            continue
        try:
            result = runner(job)
            job.last_status = result
        except Exception as exc:  # noqa: BLE001
            result = f"error: {type(exc).__name__}: {exc}"
            job.last_status = result
        job.last_run_at = _iso(current)
        job.next_run_at = _iso(current + timedelta(minutes=job.interval_minutes))
        results.append((job, result))
    if results:
        save_jobs(jobs, path)
    return results


def render_jobs(jobs: list[ScheduledJob]) -> str:
    if not jobs:
        return "Scheduled jobs: empty."
    lines = ["Scheduled jobs:"]
    for job in jobs:
        target = job.payload.get("symbols") or job.payload.get("message") or ""
        lines.append(
            f"- {job.id} {job.kind} every={job.interval_minutes}m "
            f"next={job.next_run_at} enabled={job.enabled} target={target[:80]}"
        )
    return "\n".join(lines)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_iso(value: str) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=UTC)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
