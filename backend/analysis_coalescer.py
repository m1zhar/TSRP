"""Share identical in-flight work without retaining completed provider data."""

from __future__ import annotations

from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from copy import deepcopy
from threading import Event, Lock
from typing import Callable, Hashable, TypeVar


_ANALYSIS_LOCK = Lock()
_RESULT = TypeVar("_RESULT")


class _SharedCancellation:
    """Cancel shared work only after every participating request disconnects."""

    def __init__(self):
        self._lock = Lock()
        self._events: list[Event | None] = []

    def _is_set_locked(self) -> bool:
        return bool(self._events) and all(event is not None and event.is_set() for event in self._events)

    def add_if_active(self, event: Event | None) -> bool:
        """Register under the same lock used by cancellation snapshots."""
        with self._lock:
            if self._is_set_locked():
                return False
            self._events.append(event)
            return True

    def is_set(self) -> bool:
        with self._lock:
            return self._is_set_locked()


class _Flight:
    def __init__(self):
        self.future: Future = Future()
        self.cancellation = _SharedCancellation()


_ANALYSIS_INFLIGHT: dict[tuple[str, Hashable], _Flight] = {}


def _coalesce(
    key: tuple[str, Hashable],
    calculate: Callable[[], _RESULT],
    *,
    cancel_event: Event | None = None,
    run_with_cancellation: Callable[[Event, Callable[[], _RESULT]], _RESULT] | None = None,
) -> _RESULT:
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("request disconnected before shared provider work")

    with _ANALYSIS_LOCK:
        flight = _ANALYSIS_INFLIGHT.get(key)
        if flight is not None and not flight.cancellation.add_if_active(cancel_event):
            flight = None
        owner = flight is None
        if flight is None:
            flight = _Flight()
            _ANALYSIS_INFLIGHT[key] = flight
            flight.cancellation.add_if_active(cancel_event)

    if not owner:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("request disconnected while awaiting shared provider work")
            try:
                return deepcopy(flight.future.result(timeout=0.05))
            except FutureTimeoutError:
                if flight.future.done():
                    return deepcopy(flight.future.result())
                continue

    try:
        if flight.cancellation.is_set():
            raise InterruptedError("request disconnected before shared provider work")
        if run_with_cancellation is None:
            result = calculate()
        else:
            result = run_with_cancellation(flight.cancellation, calculate)
        flight.future.set_result(deepcopy(result))
        return result
    except BaseException as error:
        flight.future.set_exception(error)
        raise
    finally:
        with _ANALYSIS_LOCK:
            if _ANALYSIS_INFLIGHT.get(key) is flight:
                del _ANALYSIS_INFLIGHT[key]


def coalesce_analysis(
    ticker: str,
    calculate: Callable[[str], dict],
    *,
    cancel_event: Event | None = None,
    run_with_cancellation: Callable[[Event, Callable[[], dict]], dict] | None = None,
) -> dict:
    """Share concurrent ticker analyses, honoring each request's disconnect."""
    return _coalesce(
        ("analysis", ticker),
        lambda: calculate(ticker),
        cancel_event=cancel_event,
        run_with_cancellation=run_with_cancellation,
    )


def coalesce_inflight(
    key: Hashable,
    calculate: Callable[[], _RESULT],
    *,
    cancel_event: Event | None = None,
    run_with_cancellation: Callable[[Event, Callable[[], _RESULT]], _RESULT] | None = None,
) -> _RESULT:
    """Share one concurrent public-data request; completed results are not cached."""
    return _coalesce(
        ("request", key),
        calculate,
        cancel_event=cancel_event,
        run_with_cancellation=run_with_cancellation,
    )
