/**
 * Soak test — sustained load over time.
 *
 * Catches: memory leaks, connection pool exhaustion, GC pauses,
 * log file growth, DB connection drift.
 *
 * Run:
 *   k6 run k6/soak.ts    # requires MEMCLAW_API_KEY & MEMCLAW_TENANT_ID in env (use .envrc)
 */

import { sleep } from "k6";
import { trafficMix } from "./traffic.ts";

export const options = {
  stages: [
    { duration: "2m", target: 20 },   // ramp up
    { duration: "2h", target: 20 },   // hold steady for 2 hours
    { duration: "1m", target: 0 },    // ramp down
  ],
  thresholds: {
    http_req_duration: ["p(95)<3000", "p(99)<5000"],
    http_req_failed: ["rate<0.01"],
  },
};

export default function (): void {
  trafficMix();
  sleep(Math.random() * 1.5 + 0.5);
}