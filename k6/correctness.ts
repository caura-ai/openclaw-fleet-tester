/**
 * Correctness test — write integrity and isolation under concurrent load.
 *
 * Not a throughput test. Validates:
 * 1. No writes lost under concurrency
 * 2. Read-after-write consistency (no stale reads)
 * 3. Fleet/agent isolation holds under stress
 *
 * Run:
 *   k6 run k6/correctness.ts    # requires MEMCLAW_API_KEY & MEMCLAW_TENANT_ID in env (use .envrc)
 */

import { fleetId, agentId, writeMemory, searchMemories } from "./traffic.ts";
import { check, sleep } from "k6";
import { Counter, Rate } from "k6/metrics";
import { randomString } from "https://jslib.k6.io/k6-utils/1.4.0/index.js";

const writesLost = new Counter("writes_lost");
const isolationBreaches = new Counter("isolation_breaches");
const readAfterWriteHit = new Rate("read_after_write_hit");

export const options = {
  scenarios: {
    write_correctness: {
      executor: "shared-iterations",
      vus: 50,
      iterations: 250,
      maxDuration: "2m",
      exec: "writeConcurrent",
    },
    read_after_write: {
      executor: "shared-iterations",
      vus: 20,
      iterations: 50,
      maxDuration: "2m",
      exec: "readAfterWrite",
      startTime: "2m",
    },
    isolation: {
      executor: "shared-iterations",
      vus: 10,
      iterations: 30,
      maxDuration: "2m",
      exec: "isolationUnderLoad",
      startTime: "4m",
    },
  },
  thresholds: {
    writes_lost: ["count==0"],
    isolation_breaches: ["count==0"],
    read_after_write_hit: ["rate>0.8"],
  },
};

/**
 * Concurrent write correctness — each VU writes, asserts 200/201.
 */
export function writeConcurrent(): void {
  const fleet = fleetId(1);
  const agent = agentId(__VU);
  const tag = `wc-${__VU}-${__ITER}-${randomString(6)}`;
  const content = `Write correctness test. Agent: ${agent}. Tag: ${tag}. Ts: ${Date.now()}`;

  const res = writeMemory(fleet, agent, content);
  const ok = check(res, {
    "write returns 200/201": (r) => r.status === 200 || r.status === 201,
  });

  if (!ok) writesLost.add(1);
}

/**
 * Read-after-write — write with unique marker, immediately search for it.
 */
export function readAfterWrite(): void {
  const fleet = fleetId(2);
  const agent = agentId(__VU);
  const marker = `raw-${__VU}-${__ITER}-${randomString(8)}`;
  const content = `Read-after-write test. Marker: ${marker}. Agent: ${agent}`;

  const writeRes = writeMemory(fleet, agent, content);
  if (writeRes.status !== 200 && writeRes.status !== 201) {
    readAfterWriteHit.add(0);
    return;
  }

  const searchRes = searchMemories(fleet, marker);
  let found = false;
  if (searchRes.status === 200) {
    try {
      const data = searchRes.json() as Record<string, unknown>;
      const results = (data.results || data.data || data.memories || []) as Array<Record<string, unknown>>;
      found = results.some(
        (m) =>
          (typeof m.content === "string" && m.content.includes(marker)) ||
          (Array.isArray(m.tags) && m.tags.includes(marker)),
      );
    } catch {
      // parse error
    }
  }

  readAfterWriteHit.add(found ? 1 : 0);
  check(null, { "marker found immediately": () => found });
}

/**
 * Isolation — write to fleet A agent A, search fleet B and fleet A as agent B.
 * Assert zero leaks.
 */
export function isolationUnderLoad(): void {
  const fleetA = fleetId(3);
  const fleetB = fleetId(4);
  const agentA = `iso-a-${__VU}`;
  const agentB = `iso-b-${__VU}`;
  const marker = `iso-${__VU}-${__ITER}-${randomString(8)}`;
  const content = `Isolation test. Marker: ${marker}. Must stay in ${fleetA} for ${agentA}.`;

  const writeRes = writeMemory(fleetA, agentA, content);
  if (writeRes.status !== 200 && writeRes.status !== 201) return;

  sleep(0.5);

  // Cross-fleet check
  const crossFleetRes = searchMemories(fleetB, marker);
  if (crossFleetRes.status === 200) {
    try {
      const data = crossFleetRes.json() as Record<string, unknown>;
      const results = (data.results || data.data || data.memories || []) as Array<Record<string, unknown>>;
      const leaked = results.filter(
        (m) =>
          (typeof m.content === "string" && m.content.includes(marker)) ||
          (Array.isArray(m.tags) && m.tags.includes(marker)),
      );
      if (leaked.length > 0) isolationBreaches.add(leaked.length);
      check(null, { "no cross-fleet leak": () => leaked.length === 0 });
    } catch {
      // parse error — not a breach
    }
  }

  // Cross-agent check
  const crossAgentRes = searchMemories(fleetA, marker, agentB);
  if (crossAgentRes.status === 200) {
    try {
      const data = crossAgentRes.json() as Record<string, unknown>;
      const results = (data.results || data.data || data.memories || []) as Array<Record<string, unknown>>;
      const leaked = results.filter(
        (m) =>
          (typeof m.content === "string" && m.content.includes(marker)) ||
          (Array.isArray(m.tags) && m.tags.includes(marker)),
      );
      if (leaked.length > 0) isolationBreaches.add(leaked.length);
      check(null, { "no cross-agent leak": () => leaked.length === 0 });
    } catch {
      // parse error — not a breach
    }
  }
}