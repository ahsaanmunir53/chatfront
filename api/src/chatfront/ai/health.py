"""Endpoint health for failover (feature 114).

Every role has a chain of endpoints serving the same model: Baseten, then Modal, then
Fireworks. An endpoint that fails twice in a row rests for a minute, so the next calls go
straight to the one after it instead of each waiting out a timeout first; a resting endpoint
is still tried last when nothing else answers, and one good answer ends its rest. An outage
drill takes an endpoint out by hand for a few minutes, so failover can be shown live; a
drilled endpoint is not called at all until the drill ends.

The state lives in the process: Chatfront runs as one service, and a restart simply starts
every endpoint fresh.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from chatfront.ai.config import Endpoint
from chatfront.core.clock import utcnow

REST_AFTER = 2  # failures in a row
REST = timedelta(seconds=60)
MAX_DRILL = timedelta(minutes=30)


class Why(StrEnum):
    FAILING = "failing"
    DRILL = "drill"


@dataclass(slots=True)
class State:
    failures: int = 0
    resting_until: datetime | None = None
    why: Why | None = None
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class Status:
    name: str
    ready: bool
    failures: int
    resting_until: datetime | None
    why: Why | None
    last_error: str | None


class Health:
    def __init__(self, clock: Callable[[], datetime] = utcnow) -> None:
        self._clock = clock
        self._states: dict[str, State] = {}

    def _state(self, name: str) -> State:
        return self._states.setdefault(name, State())

    def _resting(self, name: str) -> Why | None:
        state = self._state(name)
        if state.resting_until is None:
            return None
        if self._clock() >= state.resting_until:
            state.resting_until, state.why = None, None
            return None
        return state.why

    def order(self, endpoints: Sequence[Endpoint]) -> list[Endpoint]:
        """The chain to try: ready endpoints in their order, then the resting ones; an
        endpoint out for a drill is left out."""
        resting = {endpoint.name: self._resting(endpoint.name) for endpoint in endpoints}
        ready = [endpoint for endpoint in endpoints if resting[endpoint.name] is None]
        tired = [endpoint for endpoint in endpoints if resting[endpoint.name] == Why.FAILING]
        return ready + tired

    def failed(self, name: str, error: str) -> None:
        state = self._state(name)
        state.failures += 1
        state.last_error = error[:200]
        if state.failures >= REST_AFTER and state.why != Why.DRILL:
            state.resting_until, state.why = self._clock() + REST, Why.FAILING

    def answered(self, name: str) -> None:
        state = self._state(name)
        state.failures = 0
        if state.why == Why.FAILING:
            state.resting_until, state.why = None, None

    def drill(self, name: str, minutes: int) -> datetime | None:
        """Take an endpoint out for some minutes (at most 30); 0 brings it back now."""
        state = self._state(name)
        if minutes <= 0:
            state.resting_until, state.why = None, None
            return None
        state.resting_until = self._clock() + min(timedelta(minutes=minutes), MAX_DRILL)
        state.why = Why.DRILL
        return state.resting_until

    def status(self, endpoints: Sequence[Endpoint]) -> list[Status]:
        out = []
        for endpoint in endpoints:
            why = self._resting(endpoint.name)
            state = self._state(endpoint.name)
            out.append(
                Status(
                    endpoint.name,
                    why is None,
                    state.failures,
                    state.resting_until,
                    why,
                    state.last_error,
                )
            )
        return out
