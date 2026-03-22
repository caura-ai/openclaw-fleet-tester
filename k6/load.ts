/**
 * Load test — find throughput ceiling and latency under pressure.
 *
 * Prints estimated max supported VUs at the end based on where
 * error rate stays below 5%.
 *
 * Run:
 *   k6 run k6/load.ts    # requires MEMCLAW_API_KEY & MEMCLAW_TENANT_ID in env (use .envrc)
 */

import { sleep } from "k6";
import { textSummary } from "https://jslib.k6.io/k6-summary/0.1.0/index.js";
import { trafficMix } from "./traffic.ts";

const STAGES = [10, 20, 30];

export const options = {
  stages: [
    { duration: "30s", target: STAGES[0] },  // ramp up
    { duration: "1m", target: STAGES[0] },   // steady state
    { duration: "30s", target: STAGES[1] },  // push
    { duration: "1m", target: STAGES[1] },   // hold
    { duration: "30s", target: STAGES[2] },  // push ceiling
    { duration: "1m", target: STAGES[2] },   // hold ceiling
    { duration: "10s", target: 0 },          // ramp down
  ],
  thresholds: {
    http_req_duration: ["p(95)<30000"],
    http_req_failed: ["rate<0.05"],
  },
};

export default function (): void {
  trafficMix();
  sleep(Math.random() * 1.5 + 0.5);
}

export function handleSummary(data: Record<string, unknown>): Record<string, string> {
  const metrics = data.metrics as Record<string, any>;
  const totalReqs = metrics?.http_reqs?.values?.count || 0;
  const failedRate = metrics?.http_req_failed?.values?.rate || 0;
  const p95 = metrics?.http_req_duration?.values?.["p(95)"] || 0;
  const rps = metrics?.http_reqs?.values?.rate || 0;

  // Estimate ceiling: highest stage where error rate would be acceptable
  let ceiling = STAGES[STAGES.length - 1];
  if (failedRate > 0.05) {
    if (failedRate > 0.3) {
      ceiling = STAGES[0];
    } else if (failedRate > 0.1) {
      ceiling = STAGES[1];
    } else {
      ceiling = STAGES[2];
    }
  }

  const box = [
    "",
    "  ┌─────────────────────────────────────────────┐",
    "  │            LOAD TEST CEILING                 │",
    "  ├─────────────────────────────────────────────┤",
    `  │  Total requests:      ${String(totalReqs).padStart(7)}              │`,
    `  │  Throughput:           ${rps.toFixed(1).padStart(6)} req/s          │`,
    `  │  Error rate:          ${(failedRate * 100).toFixed(1).padStart(6)}%              │`,
    `  │  p95 latency:         ${(p95 / 1000).toFixed(1).padStart(6)}s              │`,
    `  │  Estimated ceiling:   ~${String(ceiling).padStart(3)} VUs              │`,
    "  └─────────────────────────────────────────────┘",
    "",
  ].join("\n");

  return {
    stdout: textSummary(data, { indent: "  ", enableColors: true }) + box,
  };
}
