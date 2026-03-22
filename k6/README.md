# MemClaw k6 Load Tests

Load and correctness tests for the MemClaw memory API.

## Prerequisites

- [k6](https://grafana.com/docs/k6/latest/set-up/install-k6/)
- [direnv](https://direnv.net/) (recommended) or manually source `.env`
- `MEMCLAW_API_KEY` — load-test-capable key (rate limit bypass)
- `MEMCLAW_TENANT_ID` — resolve with (requires `MEMCLAW_API_KEY` already in `.env`):
  ```bash
  direnv allow
  curl -s "https://memclaw.net/api/install-plugin?api_key=${MEMCLAW_API_KEY}&fleet_id=probe&api_url=https://memclaw.net" | grep MEMCLAW_TENANT_ID
  ```
  Then add the result to `.env` and `direnv allow` again.

## Tests

| Test | Purpose | Duration | VUs |
|------|---------|----------|-----|
| `load.ts` | Find throughput ceiling | ~3m | 20→50 |
| `stress.ts` | Degradation curve | ~14m | 20→150 |
| `spike.ts` | Instant burst recovery | ~5m | 10→200 |
| `soak.ts` | Sustained load (leaks, drift) | ~2h | 20 |
| `latency.ts` | Per-endpoint SLO validation | 1m | 5 |
| `correctness.ts` | Write integrity, read-after-write, isolation | ~6m | 10-50 |

## Run

```bash
# From project root:
make load          # throughput ceiling
make stress        # degradation curve
make spike         # burst recovery
make soak          # sustained (2h)
make latency       # SLO validation
make correctness   # write integrity + isolation
```

Or directly:
```bash
k6 run k6/load.ts
```

## Architecture

```
k6/
├── client/memClaw.ts   # generated from openapi.json (@grafana/openapi-to-k6)
├── openapi.json        # MemClaw OpenAPI spec
├── traffic.ts          # shared client setup + weighted traffic mix
├── load.ts             # ramped load test
├── stress.ts           # push past breaking point
├── spike.ts            # instant burst
├── soak.ts             # 2h sustained
├── latency.ts          # per-endpoint SLOs
└── correctness.ts      # write correctness, read-after-write, isolation
```

`traffic.ts` provides the shared client, helpers (`writeMemory`, `searchMemories`, `recallMemories`), and a `trafficMix()` function with weighted endpoint distribution (40% write, 35% search, 15% recall, 10% health).

## Regenerate client

If the MemClaw API changes:
```bash
curl -sf https://memclaw.net/api/openapi.json -o k6/openapi.json
bunx @grafana/openapi-to-k6 k6/openapi.json k6/client
```

## Custom metrics

`correctness.ts` tracks:
- `writes_lost` — must be 0
- `isolation_breaches` — must be 0
- `read_after_write_hit` — must be > 80%

`latency.ts` tracks per-endpoint p99:
- `latency_health` < 100ms
- `latency_write` < 2000ms
- `latency_search` < 1500ms
- `latency_recall` < 3000ms
- `latency_list_memories` < 1000ms
