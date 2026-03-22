/**
 * Shared traffic mix for MemClaw k6 load tests.
 *
 * Weighted endpoint distribution reflecting real agent usage patterns.
 * All scenarios import this to ensure consistent traffic shape.
 *
 * Config: use direnv with .envrc (sources .env automatically):
 *   echo 'dotenv' > .envrc && direnv allow
 *   k6 run k6/load.ts
 */

import { MemClawClient } from "./client/memClaw.ts";
import { check } from "k6";
import type { Response } from "k6/http";
import { randomString } from "https://jslib.k6.io/k6-utils/1.4.0/index.js";

const API_KEY = __ENV.MEMCLAW_API_KEY;
const TENANT_ID = __ENV.MEMCLAW_TENANT_ID;
const FLEET_PREFIX = __ENV.FLEET_PREFIX || "loadtest";

if (!API_KEY) {
  throw new Error("MEMCLAW_API_KEY not set. Add it to .env and run: direnv allow");
}
if (!TENANT_ID) {
  throw new Error(
    "MEMCLAW_TENANT_ID not set. Resolve it with (after direnv allow):\n" +
    `  curl -s "${__ENV.MEMCLAW_API_URL || "https://memclaw.net"}/api/install-plugin?api_key=\${MEMCLAW_API_KEY}&fleet_id=probe&api_url=${__ENV.MEMCLAW_API_URL || "https://memclaw.net"}" | grep MEMCLAW_TENANT_ID\n` +
    "Then add the result to .env and direnv allow again"
  );
}

export const client = new MemClawClient({
  baseUrl: __ENV.MEMCLAW_API_URL || "https://memclaw.net",
});

const AUTH = { headers: { "X-API-Key": API_KEY } };

export function fleetId(index: number): string {
  return `${FLEET_PREFIX}-fleet-${String(index).padStart(2, "0")}`;
}

export function agentId(vu: number): string {
  return `lt-agent-${String(vu).padStart(3, "0")}`;
}

/**
 * Write a memory via the generated client.
 */
export function writeMemory(fleet: string, agent: string, content: string) {
  const { response } = client.writeMemoryApiMemoriesPost(
    {
      tenant_id: TENANT_ID,
      fleet_id: fleet,
      agent_id: agent,
      content,
    },
    AUTH,
  );
  return response;
}

/**
 * Search memories via the generated client.
 */
export function searchMemories(fleet: string, query: string, agent?: string) {
  const body: Record<string, unknown> = {
    tenant_id: TENANT_ID,
    fleet_id: fleet,
    query,
    limit: 3,
  };
  if (agent) body.agent_id = agent;

  const { response } = client.searchApiSearchPost(body, AUTH);
  return response;
}

/**
 * Recall (synthesized context) via the generated client.
 */
export function recallMemories(query: string, agent?: string, fleet?: string) {
  const body: Record<string, unknown> = {
    tenant_id: TENANT_ID,
    query,
  };
  if (agent) body.agent_id = agent;
  if (fleet) body.fleet_id = fleet;

  const { response } = client.recallEndpointApiRecallPost(body, AUTH);
  return response;
}

/**
 * Weighted traffic mix reflecting real agent usage:
 *   40% writes, 35% search, 15% recall, 10% health
 */
export function trafficMix(): Response {
  const roll = Math.random();
  const fleet = fleetId(Math.ceil(Math.random() * 5));
  const agent = agentId(__VU);

  if (roll < 0.4) {
    // 40% — write memory
    const content = `Load test memory from ${agent}. Tag: lt-${randomString(8)}. Ts: ${Date.now()}`;
    const res = writeMemory(fleet, agent, content);
    check(res, { "write ok": (r) => r.status === 200 || r.status === 201 });
    return res;
  } else if (roll < 0.75) {
    // 35% — search
    const res = searchMemories(fleet, "status update progress");
    check(res, { "search ok": (r) => r.status === 200 });
    return res;
  } else if (roll < 0.9) {
    // 15% — recall
    const res = recallMemories("current status and active tasks", agent, fleet);
    check(res, { "recall ok": (r) => r.status === 200 });
    return res;
  } else {
    // 10% — health
    const { response } = client.healthApiHealthGet(AUTH);
    check(response, { "health ok": (r) => r.status === 200 });
    return response;
  }
}