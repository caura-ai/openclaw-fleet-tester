/**
 * Spike test — sudden traffic burst, then drop.
 *
 * Catches: autoscaler responsiveness, cold-start latency,
 * connection pool starvation during burst, queue backpressure,
 * whether the system recovers after the spike subsides.
 *
 * Run:
 *   k6 run k6/spike.ts    # requires MEMCLAW_API_KEY & MEMCLAW_TENANT_ID in env (use .envrc)
 */

import { sleep } from "k6";
import { trafficMix } from "./traffic.ts";

export const options = {
  stages: [
    { duration: "30s", target: 10 },  // warm up
    { duration: "1m", target: 10 },   // baseline
    { duration: "10s", target: 200 }, // SPIKE — instant burst
    { duration: "1m", target: 200 },  // hold spike
    { duration: "10s", target: 10 },  // drop back to baseline
    { duration: "2m", target: 10 },   // recovery — does latency return to normal?
    { duration: "10s", target: 0 },   // cool down
  ],
  thresholds: {
    http_req_failed: ["rate<0.15"],   // spikes can cause transient errors
  },
};

export default function (): void {
  trafficMix();
  sleep(Math.random() * 0.3 + 0.1);
}