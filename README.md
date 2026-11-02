# tiny-circuit-py — Python Circuit Breaker

A zero-dependency, **circuit breaker** for Python 3.8+ — purpose-built for AI agent
resilience.

> When a downstream service (AI API, database, queue) starts failing, stop
> hammering it. Fail fast, recover automatically, and surface the failure as a
> structured error your agent can reason about.

`tiny-circuit-py` is ~130 lines of plain Python, runs anywhere Python 3.8+ runs, has no
runtime dependencies, and ships with 11 tests.

---

## Why?

Autonomous AI agents fan out across many unreliable services. Without a circuit
breaker, one flaky API can:

- waste tokens / budget on retries that have zero chance of succeeding
- hold your event loop hostage on slow calls
- cascade failures across every tool call in the agent loop

A circuit breaker turns repeated failures into **fast, structured rejections**,
lets the downstream catch its breath, and probes for recovery automatically.

---

## State Machine

```
                    +-------------+
                    |   CLOSED    |  normal operation; calls flow through
                    | (failures:0)|
                    +------+------+
                           |
                  N consecutive failures
                           |
                           v
                    +-------------+
        manual -->  |    OPEN     |  fail fast; rejects with CircuitOpenError
         close      | (rejecting) |
                    +------+------+
                           |
                    recoveryTimeout
                           |
                           v
                    +-------------+
                    |  HALF_OPEN  |  allow <= half_open_max_calls probe calls
                    |  (probing)  |
                    +------+------+
                           |
              +------------+------------+
              |                         |
       K consecutive           any failure
         successes
              |                         |
              v                         v
         CLOSED <--------------- OPEN
```

- **CLOSED -> OPEN** after `failure_threshold` consecutive failures
- **OPEN -> HALF_OPEN** after `recovery_timeout` seconds
- **HALF_OPEN -> CLOSED** after `success_threshold` consecutive probe successes
- **HALF_OPEN -> OPEN** on any single failure (no partial recovery)

---

## Install

``bash
pip install tiny-circuit-py
``

(No deps — pure Python 3.8+, standard library only.)

---

## Usage

### Basic — wrap any function

```python
from circuit import CircuitBreaker, CircuitOpenError

cb = CircuitBreaker(
    failure_threshold=5,      # open after 5 consecutive failures
    recovery_timeout=30.0,    # probe again after 30s
    half_open_max_calls=3,    # up to 3 concurrent probes
    success_threshold=2,      # 2 consecutive probe successes to close
    name="anthropic-api",     # label for logs/metrics
)

@cb
def call_api() -> str:
    return "ok"

try:
    call_api()
except CircuitOpenError as e:
    print(e.retry_after)   # seconds until the next probe is allowed
```

### Fail-fast on a bad pricing feed

```python
pricing_cb = CircuitBreaker(failure_threshold=3)

@pricing_cb
def get_quote(symbol: str) -> float:
    return fetch_quote(symbol)

# After 3 failures the breaker is OPEN for ~30s. Every call rejects
# instantly with CircuitOpenError — no waiting on a dead endpoint.
```

### Selective tripping with `expected_exceptions`

Don't count a `ValueError` (your bug) the same as a `TimeoutError` (their problem):

```python
from circuit import CircuitBreaker

cb = CircuitBreaker(
    failure_threshold=5,
    expected_exceptions=(TimeoutError, ConnectionError),
)
```

Only the listed exception types trip the breaker. Anything else propagates
without changing the circuit state.

### Async functions

The decorator detects coroutine functions and returns an async wrapper:

```python
@cb
async def call_api_async() -> str:
    return "ok"
```

### Inspect metrics

```python
stats = cb.get_stats()
# {
#   "name": "anthropic-api", "state": "closed",
#   "failures": 9, "successes": 130, "total_calls": 142,
#   "recent_failures": 2,
#   "last_failure": "2026-11-02T01:02:03Z",
#   "last_success": "2026-11-02T01:02:09Z",
#   "failure_threshold": 5, "success_threshold": 2,
#   "recovery_timeout": 30.0,
# }
cb.get_state()        # "closed" | "open" | "half_open"
cb.allow_request()    # (allowed: bool, retry_after: float)
```

### Manual override

```python
cb.reset()            # force back to CLOSED and clear all counters
```

---

## API

### `CircuitBreaker(options)`

| Option               | Default | Meaning                                                       |
| -------------------- | ------- | ------------------------------------------------------------- |
| `failure_threshold`  | `5`     | consecutive failures before tripping OPEN                     |
| `success_threshold`  | `1`     | consecutive probe successes in HALF_OPEN before closing        |
| `recovery_timeout`   | `30.0`  | seconds to wait in OPEN before probing HALF_OPEN              |
| `half_open_max_calls`| `3`     | max concurrent probe calls in HALF_OPEN                       |
| `expected_exceptions`| `(Exception,)` | exception types that trip the breaker                |
| `name`               | `"default"` | label for logs/metrics                                   |

### Methods

- `cb.get_state() -> str` — "closed", "open" or "half_open"
- `cb.allow_request() -> (bool, float)` — whether a call may proceed, and seconds to wait
- `cb.record_success()` / `cb.record_failure()` — manual bookkeeping
- `cb.get_stats() -> dict` — JSON-serialisable metrics
- `cb.reset()` — force CLOSED and clear all counters

### Exceptions

- `CircuitOpenError(name, retry_after)` — raised when the circuit rejects a call

---

## Notes

- The failure window is a **sliding window** of `recovery_timeout * 2` seconds,
  not an infinite counter — old failures age out.
- `record_success()` only counts toward closing when the circuit is in
  `HALF_OPEN`. A probe must be admitted via `allow_request()` to count, so
  the number of admitted probes and the number of successes stay consistent.

---

## Comparison

| Feature                | **tiny-circuit-py**    | `opossum` (JS) | `pybreaker` (Python) |
| ---------------------- | ---------------------- | ---------------------- | ---------------------- |
| Runtime dependencies   | **0**                  | 4 (hoek, joi, ...)      | 0                     |
| Thread-safe            | **yes**                | yes                    | yes                   |
| Async-aware            | **yes**                | yes (promise)          | partial               |
| Concurrent probe cap   | **yes**                | yes                    | yes                   |
| Per-call timeout       | ❌                     | yes                    | yes                   |
| Event callbacks        | ❌                     | yes                    | yes                   |
| Fallback function      | ❌                     | yes                    | yes                   |

**Pick `tiny-circuit-py` if** you want a tiny, auditable, dependency-free breaker
that does the basics perfectly — ideal for embedding into AI agents and edge
functions where install size and supply-chain surface matter.

---

## License

MIT (c) 2026 hussain-alsaibai
