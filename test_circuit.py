"""Tests for tiny-circuit-py."""
import time, threading, pytest
from circuit import CircuitBreaker, CircuitOpenError, CircuitState

def test_closed_by_default():
    cb = CircuitBreaker()
    assert cb.get_state() == "closed"
    assert cb.allow_request()[0] is True

def test_opens_after_threshold():
    cb = CircuitBreaker(failure_threshold=3)
    cb.record_failure(); cb.record_failure()
    assert cb.get_state() == "closed"
    cb.record_failure()
    assert cb.get_state() == "open"
    assert cb.allow_request()[0] is False

def test_half_open_after_recovery_timeout():
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1)
    cb.record_failure(); cb.record_failure()
    assert cb.get_state() == "open"
    time.sleep(0.15)
    allowed, _ = cb.allow_request()
    assert allowed is True
    assert cb.get_state() == "half_open"

def test_closes_after_half_open_success():
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.05, half_open_max_calls=1)
    cb._transition_to(CircuitState.OPEN)
    time.sleep(0.1)
    # Admit the probe the same way the decorator does: allow_request() is what
    # counts the probe against half_open_max_calls.
    allowed, _ = cb.allow_request()
    assert allowed is True
    assert cb.get_state() == "half_open"
    cb.record_success()
    assert cb.get_state() == "closed"

def test_decorator_blocks_when_open():
    cb = CircuitBreaker(failure_threshold=1)
    @cb
    def fragile(): raise ValueError("boom")
    # The FIRST call must reach the function - that failure is what trips the
    # breaker. Only the NEXT call is rejected without running the function.
    with pytest.raises(ValueError): fragile()
    assert cb.get_state() == "open"
    with pytest.raises(CircuitOpenError): fragile()

def test_half_open_needs_success_threshold_successes():
    """A single lucky probe must NOT close the circuit when more are in flight.

    With 3 probes admitted and success_threshold=2, one success is not enough:
    the downstream has not proven itself stable yet.
    """
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.05,
                        half_open_max_calls=3, success_threshold=2)
    cb._transition_to(CircuitState.OPEN)
    time.sleep(0.1)
    for _ in range(3):
        assert cb.allow_request()[0] is True
    cb.record_success()
    assert cb.get_state() == "half_open", "one success must not close the circuit"
    cb.record_success()
    assert cb.get_state() == "closed"


def test_half_open_failure_reopens_immediately():
    """A failing probe must send the circuit straight back to OPEN."""
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.05)
    cb._transition_to(CircuitState.OPEN)
    time.sleep(0.1)
    assert cb.allow_request()[0] is True
    cb.record_failure()
    assert cb.get_state() == "open"


def test_reset_clears_half_open_successes():
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.05, success_threshold=2)
    cb._transition_to(CircuitState.OPEN)
    time.sleep(0.1)
    cb.allow_request()
    cb.record_success()
    cb.reset()
    assert cb.get_state() == "closed"
    assert cb.get_stats()["success_threshold"] == 2


def test_reset():
    cb = CircuitBreaker(failure_threshold=1)
    cb.record_failure()
    assert cb.get_state() == "open"
    cb.reset()
    assert cb.get_state() == "closed"
    assert cb.get_stats()["failures"] == 0

def test_stats_serializable():
    cb = CircuitBreaker(failure_threshold=3, name="payment")
    cb.record_success(); cb.record_failure()
    stats = cb.get_stats()
    assert isinstance(stats, dict)
    assert stats["name"] == "payment"
    assert stats["failures"] == 1

def test_thread_safety():
    cb = CircuitBreaker(failure_threshold=100)
    def hammer():
        for _ in range(50):
            cb.record_success(); cb.record_failure()
    threads = [threading.Thread(target=hammer) for _ in range(10)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert cb.get_stats()["total_calls"] == 1000

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
