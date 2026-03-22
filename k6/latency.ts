/**
 * Latency test — SLO validation at low concurrency.
 *
 * Runs at steady low concurrency and asserts per-endpoint latency SLOs.
 * Fails if any SLO is breached.
 *
 * Run:
 *   k6 run k6/latency.ts    # requires MEMCLAW_API_KEY & MEMCLAW_TENANT_ID in env (use .envrc)
 */

import { client, fleetId, agentId, writeMemory, searchMemories, recallMemories } from "./traffic.ts";
import { check, sleep } from "k6";
import { Trend } from "k6/metrics";
import { randomString } from "https://jslib.k6.io/k6-utils/1.4.0/index.js";

const AUTH = { headers: { "X-API-Key": __ENV.MEMCLAW_API_KEY } };

const healthLatency = new Trend("latency_health");
const writeLatency = new Trend("latency_write");
const searchLatency = new Trend("latency_search");
const recallLatency = new Trend("latency_recall");
const listMemoriesLatency = new Trend("latency_list_memories");

export const options = {
  vus: 5,
  duration: "1m",
  thresholds: {
    latency_health:        ["p(99)<200"],
    latency_write:         ["p(99)<15000"],  // includes LLM enrichment (classification, entities, summary)
    latency_search:        ["p(99)<5000"],   // includes LLM ranking
    latency_recall:        ["p(99)<5000"],   // includes LLM synthesis
    latency_list_memories: ["p(99)<1000"],
    http_req_failed:       ["rate<0.01"],
  },
};

export default function (): void {
  const fleet = fleetId(1);
  const agent = agentId(__VU);

  // Health
  {
    const { response } = client.healthApiHealthGet(AUTH);
    check(response, { "health 200": (r) => r.status === 200 });
    healthLatency.add(response.timings.duration);
  }

  // Write
  {
    const content = `Latency test from ${agent}. Tag: lt-${randomString(8)}. Ts: ${Date.now()}`;
    const res = writeMemory(fleet, agent, content);
    check(res, { "write ok": (r) => r.status === 200 || r.status === 201 });
    writeLatency.add(res.timings.duration);
  }

  // Search
  {
    const res = searchMemories(fleet, "status update");
    check(res, { "search ok": (r) => r.status === 200 });
    searchLatency.add(res.timings.duration);
  }

  // Recall
  {
    const res = recallMemories("current tasks and progress", agent, fleet);
    check(res, { "recall ok": (r) => r.status === 200 });
    recallLatency.add(res.timings.duration);
  }

  // List memories
  {
    const { response } = client.listMemoriesApiMemoriesGet(
      { tenant_id: __ENV.MEMCLAW_TENANT_ID, fleet_id: fleet },
      AUTH,
    );
    check(response, { "list memories ok": (r) => r.status === 200 });
    listMemoriesLatency.add(response.timings.duration);
  }

  sleep(1);
}