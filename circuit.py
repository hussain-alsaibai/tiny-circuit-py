"""tiny-circuit — Zero-dependency circuit breaker for Python. Thread-safe, async-aware."""
from __future__ import annotations
import asyncio, threading, time, functools
from enum import Enum
from typing import Any, Callable, TypeVar
T = TypeVar("T")

class CircuitState(Enum):
    CLOSED = "closed"; OPEN = "open"; HALF_OPEN = "half_open"

class CircuitOpenError(Exception):
    def __init__(self, name: str, retry_after: float):
        self.name, self.retry_after = name, retry_after
        super().__init__(f"Circuit '{name}' is OPEN. Retry after {retry_after:.1f}s")

class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 30.0,
                 half_open_max_calls: int = 3, name: str = "default",
                 expected_exceptions: tuple = (Exception,)):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        self.name = name
        self.expected_exceptions = expected_exceptions
        self._state = CircuitState.CLOSED
        self._lock = threading.RLock()
        self._failure_count = 0; self._success_count = 0; self._total_calls = 0
        self._last_failure_time = None; self._last_success_time = None
        self._opened_at = None; self._half_open_calls = 0
        self._failure_timestamps: list[float] = []

    def get_state(self) -> str:
        with self._lock:
            self._check_transition()
            return self._state.value

    def _check_transition(self) -> None:
        if self._state == CircuitState.OPEN and self._opened_at is not None:
            if time.monotonic() - self._opened_at >= self.recovery_timeout:
                self._transition_to(CircuitState.HALF_OPEN)

    def _transition_to(self, new_state: CircuitState) -> None:
        self._state = new_state
        if new_state == CircuitState.OPEN:
            self._opened_at = time.monotonic()
            self._failure_timestamps = []
        elif new_state == CircuitState.HALF_OPEN:
            self._half_open_calls = 0
        elif new_state == CircuitState.CLOSED:
            self._failure_count = 0
            self._failure_timestamps = []; self._opened_at = None

    def allow_request(self) -> tuple[bool, float]:
        with self._lock:
            self._check_transition()
            state = self._state
            if state == CircuitState.CLOSED: return True, 0.0
            if state == CircuitState.OPEN:
                retry_after = self.recovery_timeout - (time.monotonic() - (self._opened_at or 0))
                return False, max(0.0, retry_after)
            if self._half_open_calls < self.half_open_max_calls:
                self._half_open_calls += 1
                return True, 0.0
            return False, self.recovery_timeout

    def record_success(self) -> None:
        with self._lock:
            self._success_count += 1; self._total_calls += 1
            self._last_success_time = time.time()
            if self._state == CircuitState.HALF_OPEN and self._half_open_calls >= self.half_open_max_calls:
                self._transition_to(CircuitState.CLOSED)

    def record_failure(self) -> None:
        with self._lock:
            self._failure_count += 1; self._total_calls += 1
            self._last_failure_time = time.time()
            now = time.monotonic()
            self._failure_timestamps.append(now)
            self._failure_timestamps = [ts for ts in self._failure_timestamps if ts > now - self.recovery_timeout * 2]
            if self._state == CircuitState.HALF_OPEN:
                self._transition_to(CircuitState.OPEN)
                return
            if self._state == CircuitState.CLOSED and len(self._failure_timestamps) >= self.failure_threshold:
                self._transition_to(CircuitState.OPEN)

    def __call__(self, func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            allowed, retry_after = self.allow_request()
            if not allowed: raise CircuitOpenError(self.name, retry_after)
            try:
                result = func(*args, **kwargs); self.record_success(); return result
            except self.expected_exceptions: self.record_failure(); raise
        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> T:
            allowed, retry_after = self.allow_request()
            if not allowed: raise CircuitOpenError(self.name, retry_after)
            try:
                result = await func(*args, **kwargs); self.record_success(); return result
            except self.expected_exceptions: self.record_failure(); raise
        return async_wrapper if asyncio.iscoroutinefunction(func) else wrapper

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            self._check_transition()
            return {
                "name": self.name, "state": self._state.value,
                "failures": self._failure_count, "successes": self._success_count,
                "total_calls": self._total_calls,
                "recent_failures": len(self._failure_timestamps),
                "last_failure": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self._last_failure_time)) if self._last_failure_time else None,
                "last_success": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self._last_success_time)) if self._last_success_time else None,
                "failure_threshold": self.failure_threshold,
                "recovery_timeout": self.recovery_timeout,
            }

    def reset(self) -> None:
        with self._lock:
            self._transition_to(CircuitState.CLOSED)
            self._failure_count = 0; self._success_count = 0; self._total_calls = 0
            self._failure_timestamps = []; self._opened_at = None; self._half_open_calls = 0

    def __repr__(self) -> str: return f"<CircuitBreaker {self.name} state={self.get_state()}>"
