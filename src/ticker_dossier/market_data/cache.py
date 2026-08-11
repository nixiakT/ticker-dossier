"""Thread-safe TTL caching for provider-chain operations."""

from __future__ import annotations

from copy import deepcopy
import threading
import time
from typing import Any

from .coverage import source_coverage


CacheEntry = tuple[float, Any, dict[str, Any]]
CacheStore = dict[tuple[Any, ...], CacheEntry]


def get_cached(
    method: str,
    args: tuple[Any, ...],
    *,
    ttls: dict[str, float],
    cache: CacheStore,
    cache_lock: threading.Lock,
    coverage_by_method: dict[str, dict[str, Any]],
    now: float | None = None,
) -> Any | None:
    """Read a detached value and restore its matching coverage snapshot."""
    ttl = ttls.get(method, 0.0)
    if ttl <= 0:
        return None
    key = (method, *args)
    current = time.monotonic() if now is None else now
    with cache_lock:
        entry = cache.get(key)
        if entry is None:
            return None
        stored_at, value, coverage = entry
        age = current - stored_at
        if age >= ttl:
            cache.pop(key, None)
            return None
        cached_value = deepcopy(value)
        cached_coverage = deepcopy(coverage)
    cached_coverage["cache_hit"] = True
    cached_coverage["cache_age_seconds"] = age
    coverage_by_method[method] = cached_coverage
    return cached_value


def set_cached(
    method: str,
    args: tuple[Any, ...],
    value: Any,
    *,
    ttls: dict[str, float],
    cache: CacheStore,
    cache_lock: threading.Lock,
    coverage_by_method: dict[str, dict[str, Any]],
    now: float | None = None,
) -> None:
    """Store detached data beside the exact diagnostics that produced it."""
    if ttls.get(method, 0.0) <= 0:
        return
    coverage = source_coverage(coverage_by_method, method)
    coverage["cache_hit"] = False
    coverage["cache_age_seconds"] = 0.0
    key = (method, *args)
    with cache_lock:
        current = time.monotonic() if now is None else now
        cache[key] = (current, deepcopy(value), coverage)
