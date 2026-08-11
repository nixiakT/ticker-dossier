"""Concurrent provider execution and timeout isolation."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import threading
import time
from collections.abc import Callable, Hashable
from typing import Any, cast

from ticker_dossier.market_data.providers import (
    MarketDataProvider,
    ProviderTimeoutError,
    SampleDataProvider,
)
from ticker_dossier.market_data.providers.normalization import _compact_provider_error


ProviderCallKey = tuple[int, str, tuple[Hashable, ...]]


@dataclass
class ProviderFlight:
    """One shared provider call retained until all waiters have consumed it."""

    event: threading.Event = field(default_factory=threading.Event)
    waiters: int = 0
    ok: bool = False
    value: Any = None
    completed_at: float | None = None


ProviderFlights = dict[ProviderCallKey, ProviderFlight]


class _OperationDeadlineTimeout(ProviderTimeoutError):
    """The caller's operation budget expired while a provider worker was running."""


def _provider_supports(provider: MarketDataProvider, method: str, *args: Any) -> bool:
    supports = getattr(provider, "supports", None)
    if callable(supports):
        return bool(supports(method, *args))
    capabilities = getattr(provider, "capabilities", None)
    return capabilities is None or method in capabilities


def _is_sample_provider(provider: MarketDataProvider) -> bool:
    return isinstance(provider, SampleDataProvider) or provider.name == "SAMPLE_FALLBACK"


def partition_providers(
    providers: list[MarketDataProvider],
    method: str,
    args: tuple[Any, ...],
    circuit_status: Callable[[MarketDataProvider], str],
) -> tuple[list[MarketDataProvider], list[MarketDataProvider], list[dict[str, str]]]:
    """Split supported providers into runnable real and deferred sample sources."""
    real_providers: list[MarketDataProvider] = []
    sample_providers: list[MarketDataProvider] = []
    failures: list[dict[str, str]] = []
    for provider in providers:
        try:
            supported = _provider_supports(provider, method, *args)
        except Exception as exc:  # noqa: BLE001 - one adapter must not abort selection
            failures.append({"name": provider.name, "error": _compact_provider_error(exc)})
            continue
        if not supported:
            continue
        if _is_sample_provider(provider):
            sample_providers.append(provider)
            continue
        if blocked := circuit_status(provider):
            failures.append({"name": provider.name, "error": blocked})
            continue
        real_providers.append(provider)
    return real_providers, sample_providers, failures


def _collect_provider_calls(
    providers: list[MarketDataProvider],
    method: str,
    args: tuple[Any, ...],
    timeout: float,
    inflight: ProviderFlights,
    inflight_lock: threading.Lock,
) -> list[tuple[MarketDataProvider, bool, Any]]:
    """Run providers concurrently and share identical in-flight calls.

    The single-flight key contains provider identity, method, and frozen arguments.
    Each caller retains its own operation deadline; joining a healthy call is not
    itself a timeout. Daemon workers are intentional because a stuck third-party
    SDK must not block request teardown or interpreter shutdown.
    """
    if not providers:
        return []
    call_args = tuple(_freeze_call_arg(arg) for arg in args)
    registrations: list[tuple[MarketDataProvider, ProviderCallKey, ProviderFlight]] = []
    for provider in providers:
        key = (id(provider), method, call_args)
        start_worker = False
        with inflight_lock:
            flight = inflight.get(key)
            if flight is None:
                flight = ProviderFlight()
                inflight[key] = flight
                start_worker = True
            flight.waiters += 1
        registrations.append((provider, key, flight))
        if start_worker:
            threading.Thread(
                target=_run_provider_call,
                args=(provider, method, args, key, flight, inflight, inflight_lock),
                name=f"finance-provider-{provider.name}-{method}",
                daemon=True,
            ).start()

    rows: list[tuple[MarketDataProvider, bool, Any]] = []
    deadline = time.monotonic() + timeout
    try:
        for provider, _, flight in registrations:
            remaining = deadline - time.monotonic()
            if remaining > 0 and not flight.event.is_set():
                flight.event.wait(remaining)
            with inflight_lock:
                completed_in_time = (
                    flight.completed_at is not None and flight.completed_at <= deadline
                )
                ok = flight.ok
                value = flight.value
            if not completed_in_time:
                rows.append(
                    (
                        provider,
                        False,
                        _OperationDeadlineTimeout(
                            f"timed out after {timeout:g}s operation deadline"
                        ),
                    )
                )
                continue
            if ok:
                try:
                    value = deepcopy(value)
                except Exception as exc:  # noqa: BLE001 - expose an unusable provider result
                    ok = False
                    value = exc
            rows.append((provider, ok, value))
    finally:
        with inflight_lock:
            for _, key, flight in registrations:
                flight.waiters -= 1
                if flight.waiters == 0 and flight.event.is_set() and inflight.get(key) is flight:
                    inflight.pop(key, None)
    return rows


def _run_provider_call(
    provider: MarketDataProvider,
    method: str,
    args: tuple[Any, ...],
    key: ProviderCallKey,
    flight: ProviderFlight,
    inflight: ProviderFlights,
    inflight_lock: threading.Lock,
) -> None:
    try:
        ok = True
        value = getattr(provider, method)(*args)
    except Exception as exc:  # noqa: BLE001 - returned to every registered waiter
        ok = False
        value = exc
    completed_at = time.monotonic()
    with inflight_lock:
        flight.ok = ok
        flight.value = value
        flight.completed_at = completed_at
        flight.event.set()
        if flight.waiters == 0 and inflight.get(key) is flight:
            inflight.pop(key, None)


def _freeze_call_arg(value: Any) -> Hashable:
    if isinstance(value, dict):
        items = ((_freeze_call_arg(key), _freeze_call_arg(item)) for key, item in value.items())
        return tuple(sorted(items, key=repr))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_call_arg(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted((_freeze_call_arg(item) for item in value), key=repr))
    try:
        hash(value)
    except TypeError:
        return repr(value)
    return cast(Hashable, value)


def circuit_error(
    circuit_until: dict[int, float],
    provider: MarketDataProvider,
    *,
    now: float | None = None,
) -> str:
    """Return a stable diagnostic while a provider circuit remains open."""
    current = time.monotonic() if now is None else now
    remaining = circuit_until.get(id(provider), 0.0) - current
    if remaining <= 0:
        circuit_until.pop(id(provider), None)
        return ""
    return f"temporarily skipped after timeout ({remaining:.1f}s cooldown remaining)"


def trip_circuit(
    circuit_until: dict[int, float],
    provider: MarketDataProvider,
    cooldown: float,
    *,
    now: float | None = None,
) -> None:
    """Open a provider circuit without coupling the policy to the chain class."""
    current = time.monotonic() if now is None else now
    circuit_until[id(provider)] = current + cooldown
