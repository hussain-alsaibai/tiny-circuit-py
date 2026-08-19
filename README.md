# tiny-circuit-py — Python Circuit Breaker

A zero-dependency, **circuit breaker** for Python 3.8+ — purpose-built for AI agent
resilience.

> When a downstream service (AI API, database, queue) starts failing, stop
> hammering it. Fail fast, recover automatically, and surface the failure as a
> structured error your agent can reason about.

`tiny-circuit` is ~250 lines of plain Python, runs anywhere Python 3.8+ runs, has no
runtime dependencies, and ships with 25+ tests.

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
                    ┌─────────────┐
                    │   CLOSED    │  normal operation; calls flow through
                    │ (failures:0)│
                    └──────┬──────┘
                           │
                  N consecutive failures
                           │
                           ▼
                    ┌─────────────┐
        manual ───▶ │    OPEN     │  fail fast; rejects with CircuitOpenError
         close      │ (rejecting) │
                    └──────┬──────┘
                           │
                    recoveryTimeout
                           │
                           ▼
                    ┌─────────────┐
                    │  HALF_OPEN  │  allow ≤ halfOpenMax probe calls
                    │  (probing)  │
                    └──────┬──────┘
                           │
              ┌────────────┴────────────┐
              │                         │
       K consecutive           any failure
         successes
              │                         │
              ▼                         ▼
         CLOSED ◀─────────────── OPEN
```

- **CLOSED → OPEN** after `failureThreshold` consecutive failures
- **OPEN → HALF_OPEN** after `recoveryTimeout` ms
- **HALF_OPEN → CLOSED** after `successThreshold` consecutive successes
- **HALF_OPEN → OPEN** on any single failure (no partial recovery)

---

## Install

```bash
npm install tiny-circuit
```

(No deps — just Node ≥ 18.)

---

## Usage

### Basic — wrap any async function

```javascript
const { CircuitBreaker } = require('tiny-circuit');

const cb = new CircuitBreaker({
  failureThreshold: 5,     // open after 5 consecutive failures
  recoveryTimeout: 30_000, // probe again after 30s
  halfOpenMax: 3,          // up to 3 concurrent probes
  timeout: 10_000,         // per-call timeout in ms
  name: 'anthropic-api',   // label for logs/metrics
});

const result = await cb.execute(async () => {
  const res = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      'x-api-key': process.env.ANTHROPIC_API_KEY,
    },
    body: JSON.stringify({
      model: 'claude-opus-4-1',
      max_tokens: 256,
      messages: [{ role: 'user', content: 'Hello' }],
    }),
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
});
```

### Fail-fast on bad pricing data

```javascript
const pricingCB = new CircuitBreaker({ failureThreshold: 3 });

async function getQuote(symbol) {
  return pricingCB.execute(async () => {
    const r = await fetch(`https://api.example.com/quote/${symbol}`);
    if (!r.ok) throw new Error(`quote http ${r.status}`);
    return r.json();
  });
}

// After 3 failures, the breaker is OPEN for ~30s. All concurrent calls
// reject instantly with CircuitOpenError — no waiting on a dead endpoint.
```

### Selective tripping with `shouldTrip`

Don't count a 4xx (your bug) the same as a 5xx (their problem):

```javascript
const cb = new CircuitBreaker({
  failureThreshold: 5,
  shouldTrip: (err) => {
    // Only trip on 5xx-class errors.
    const m = /http (\d{3})/.exec(err.message);
    return !m || m[1].startsWith('5');
  },
});
```

### Listen to lifecycle events

```javascript
const cb = new CircuitBreaker({ failureThreshold: 3 });

cb.on('open',     () => log.warn('circuit OPEN — falling back to cached results'));
cb.on('half-open', () => log.info('probing downstream'));
cb.on('close',    () => log.info('downstream recovered'));
cb.on('reject',   ({ err }) => metrics.increment('cb.rejects'));
cb.on('failure',  ({ err }) => log.error({ err }, 'call failed'));
cb.on('timeout',  () => log.warn('call timed out'));
```

### Inspect metrics

```javascript
console.log(cb.metrics);
// {
//   fires: 142, successes: 130, failures: 9, rejects: 3, timeouts: 2,
//   stateChanges: 4, openedAt: 1722343200000, closedAt: 1722343260000,
//   lastFailureAt: 1722343259000, lastSuccessAt: 1722343260500, ...
// }
console.log(cb.state);       // 'CLOSED' | 'OPEN' | 'HALF_OPEN'
console.log(cb.isAllowing()); // false while OPEN
```

### Manual override

```javascript
cb.open();   // trip immediately (e.g. before deploys / known-down windows)
cb.close();  // reset (e.g. after a successful canary)
```

---

## API

### `new CircuitBreaker(options)`

| Option             | Default | Meaning                                                       |
| ------------------ | ------- | ------------------------------------------------------------- |
| `failureThreshold` | `5`     | consecutive failures before tripping OPEN                     |
| `successThreshold` | `2`     | consecutive successes in HALF_OPEN before closing              |
| `recoveryTimeout`  | `30000` | ms to wait in OPEN before probing HALF_OPEN                   |
| `halfOpenMax`      | `3`     | max concurrent probe calls in HALF_OPEN                       |
| `timeout`          | `0`     | per-call timeout (ms); `0` disables                           |
| `shouldTrip`       | `null`  | `(err) => boolean` — selective error classification           |
| `name`             | `'circuit'` | label for logs/metrics                                   |

### `cb.execute(fn) → Promise<T>`

Runs `fn()`. Rejects with the original error if it throws. Rejects with
`CircuitOpenError` if the circuit is open.

### Events

`success`, `failure`, `reject`, `timeout`, `open`, `half-open`, `close`

Use `cb.on(event, fn)` / `cb.off(event, fn)` / `cb.once(event, fn)`.

### Properties

- `cb.state` — `'CLOSED' | 'OPEN' | 'HALF_OPEN'`
- `cb.metrics` — counters + timestamps (see above)
- `cb.options` — frozen option snapshot
- `cb.isAllowing()` — `false` while OPEN

---

## Comparison

| Feature                | **tiny-circuit**    | [`opossum`](https://www.npmjs.com/package/opossum)         |
| ---------------------- | ------------------- | ---------------------------------------------------------- |
| Runtime dependencies   | **0**               | 4 (hoek, joi, …)                                           |
| Install size           | ~6 KB               | ~250 KB                                                    |
| Lines of source        | ~250                | ~1500                                                      |
| Python 3.8+ test runner    | ✅ built-in `pytest` | external (mocha / jest)                              |
| Concurrent probe cap   | ✅ `halfOpenMax`    | ✅ `volumeThreshold`                                      |
| Per-call timeout       | ✅                  | ✅                                                         |
| EventEmitter           | ✅ custom events    | ✅ EventEmitter                                            |
| Fallback function      | ❌ (handle it yourself — trivial) | ✅                                          |
| Bulkheading / semaphores | ❌              | ✅                                                         |
| Rolling stats window   | ❌ (consecutive only) | ✅ rolling counts                                       |
| Plugin hooks           | ❌                  | ✅ (firewall, metrics, …)                                  |

**Pick `tiny-circuit` if** you want a tiny, auditable, dependency-free breaker
that does the basics perfectly — ideal for embedding into AI agents and edge
functions where install size and supply-chain surface matter.

**Pick `opossum` if** you want fallbacks, bulkheading, rolling windows, and an
ecosystem of plugins.

---

## License

MIT © 2026 hussain-alsaibai
