"""JSONL trace writer and replay helper for observability demonstrations."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class Tracer:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")

    def log(self, event: str, payload: dict[str, Any]) -> None:
        row = {"ts": round(time.time(), 3), "event": event, **payload}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def replay(path: str | Path) -> str:
    rows = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line]
    total = sum(int(row.get("total_tokens", 0)) for row in rows if row.get("event") == "model_usage")
    lines = [f"{index}. {row.get('event')}: {row}" for index, row in enumerate(rows, start=1)]
    lines.append(f"total model tokens: {total}")
    return "\n".join(lines)
