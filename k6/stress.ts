/**
 * Stress test — push past expected load to find the degradation curve.
 *
 * Catches: breaking points, error rate cliffs, cascading failures,
 * recovery behavior after overload.
 *
 * Run:
 *   k6 run k6/stress.ts    # requires MEMCLAW_API_KEY & MEMCLAW_TENANT_ID in env (use .envrc)
 */

import { sleep } from "k6";
import { trafficMix } from "./traffic.ts";

export const options = {
  stages: [
    { duration: "1m", target: 20 },   // normal load
    { duration: "2m", target: 20 },   // hold normal
    { duration: "1m", target: 50 },   // ramp to high
    { duration: "2m", target: 50 },   // hold high
    { duration: "1m", target: 100 },  // ramp to stress
    { duration: "2m", target: 100 },  // hold stress
    { duration: "1m", target: 150 },  // push breaking point
    { duration: "2m", target: 150 },  // hold breaking point
    { duration: "2m", target: 0 },    // recovery — does it come back?
  ],
  // Intentionally lenient — we're measuring degradation, not enforcing SLOs
  thresholds: {
    http_req_failed: ["rate<0.10"],
  },
};

export default function (): void {
  trafficMix();
  sleep(Math.random() * 0.5 + 0.1);
}
