"""Finance-specific learning and memory helpers."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


MEMORY_DIR = Path(".finance_agent")
MEMORY_PATH = MEMORY_DIR / "finance_memory.jsonl"


SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"(?i)(api[_-]?key|token|secret|password|cookie)\s*[:=]\s*['\"]?[^'\"\s]+"),
]


@dataclass
class FinanceMemory:
    category: str
    content: str
    source: str = "user"
    symbol: str = ""
    confidence: str = "medium"


def add_memory(
    content: str,
    category: str = "preference",
    source: str = "user",
    symbol: str = "",
    confidence: str = "medium",
    path: Path | None = None,
) -> Path:
    path = path or MEMORY_PATH
    clean_content = _sanitize(content.strip())
    if not clean_content:
        raise ValueError("memory content is required")
    item = FinanceMemory(
        category=_normalize_category(category),
        content=clean_content,
        source=_sanitize(source.strip() or "user"),
        symbol=_sanitize(symbol.strip().upper()),
        confidence=_normalize_confidence(confidence),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "category": item.category,
        "content": item.content,
        "source": item.source,
        "symbol": item.symbol,
        "confidence": item.confidence,
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    return path


def list_memories(limit: int = 20, path: Path | None = None) -> list[dict[str, str]]:
    path = path or MEMORY_PATH
    if not path.exists():
        return []
    rows: list[dict[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        rows.append({str(key): str(value) for key, value in data.items()})
    return rows[-max(limit, 1):]


def render_memories(limit: int = 20) -> str:
    rows = list_memories(limit)
    if not rows:
        return "Finance memory: empty."
    lines = ["Finance memory:"]
    for index, row in enumerate(rows, start=1):
        symbol = f" [{row.get('symbol')}]" if row.get("symbol") else ""
        lines.append(
            f"{index}. {row.get('category', '')}{symbol} "
            f"({row.get('confidence', 'medium')}) - {row.get('content', '')}"
        )
    return "\n".join(lines)


def extract_learning(task: str, answer: str = "", trace: str = "") -> str:
    """Convert a finance task result into concise reusable learning notes."""
    text = "\n".join(part for part in (task, answer, trace) if part)
    lines = []
    if _contains_any(text, ("SAMPLE_FALLBACK", "样例", "fallback")):
        lines.append("真实研究中必须区分真实数据和 SAMPLE_FALLBACK，样例数据只能用于演示。")
    if _contains_any(text, ("No route to host", "403", "WAF", "Cloudflare", "连接失败")):
        lines.append("网页/行情失败时先检查代理和替代数据源，不应直接用模型记忆回答。")
    if _contains_any(text.lower(), ("spcx", "spacex")):
        lines.append("SpaceX 相关任务先解析为 SPCX，再核验公开网页、行情时间和新闻来源。")
    if _contains_any(text, ("智谱", "02513")):
        lines.append("智谱相关任务保留 02513.HK 展示代码，并说明数据源查询代码差异。")
    if _contains_any(text, ("买入", "卖出", "收益", "回测")):
        lines.append("策略和结论必须标注风险边界，不能输出确定性买卖指令或承诺收益。")
    if not lines:
        clean = " ".join(_sanitize(text).split())
        if clean:
            lines.append(clean[:240])
    return "\n".join(f"- {line}" for line in _dedupe(lines))


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(token.lower() in lowered for token in tokens)


def _sanitize(text: str) -> str:
    clean = text
    for pattern in SECRET_PATTERNS:
        clean = pattern.sub("[REDACTED_SECRET]", clean)
    return clean


def _normalize_category(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-") or "preference"
    allowed = {"preference", "correction", "source", "risk", "workflow", "symbol", "strategy"}
    return normalized if normalized in allowed else "workflow"


def _normalize_confidence(value: str) -> str:
    normalized = value.strip().lower() or "medium"
    return normalized if normalized in {"low", "medium", "high"} else "medium"


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            out.append(item)
            seen.add(item)
    return out[:8]
