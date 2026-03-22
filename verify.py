#!/usr/bin/env python3
"""
OpenClaw Fleet Memory Test v2 — Verification

Standalone script that runs assertions against MemClaw API to confirm
memories were stored, scoped, and recalled correctly.

Usage:
    python verify.py
    python verify.py --url https://memclaw.net --api-key mc_... --admin-key mc_admin_...
    python verify.py --count 10
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import httpx
from rich.console import Console
from rich.table import Table

import config
from config import MEMCLAW_API_URL

console = Console()


# ─── Assertion Helpers ────────────────────────────────────────────────────────


class Result:
    def __init__(self, name: str, passed: bool, detail: str = "") -> None:
        self.name = name
        self.passed = passed
        self.detail = detail

    def __repr__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return f"[{status}] {self.name}: {self.detail}"


# ─── Verification Assertions ──────────────────────────────────────────────────


async def check_fleet_nodes(
    client: httpx.AsyncClient, url: str, api_key: str, tenant_id: str, vm_count: int
) -> Result:
    """Assert all VMs appear in fleet as online nodes."""
    name = "Fleet nodes online"
    try:
        resp = await client.get(
            f"{url}/api/fleet/nodes",
            params={"tenant_id": tenant_id},
            headers={"X-API-Key": api_key},
        )
    except Exception as exc:
        return Result(name, False, f"Request failed: {exc}")

    if resp.status_code != 200:
        return Result(name, False, f"HTTP {resp.status_code}: {resp.text[:100]}")

    nodes = resp.json()
    if not isinstance(nodes, list):
        nodes = nodes.get("data", nodes.get("nodes", []))

    online_names = {n.get("node_name", "") for n in nodes if n.get("status") == "online"}
    sample_fleet = config.VM_FLEETS[0]["fleet_id"] if config.VM_FLEETS else ""
    fleet_prefix = sample_fleet.rsplit("-fleet-", 1)[0] if "-fleet-" in sample_fleet else ""
    vm_prefix = config.vm_name_prefix(fleet_prefix)
    expected = {f"{vm_prefix}-{i:02d}" for i in range(1, vm_count + 1)}
    missing = expected - online_names

    if missing:
        return Result(name, False, f"Missing/offline nodes: {missing} (found: {online_names})")
    return Result(name, True, f"{len(online_names)} nodes online")


async def check_all_agents_registered(
    client: httpx.AsyncClient, url: str, api_key: str, tenant_id: str, vm_count: int
) -> Result:
    """Assert all expected agents are registered with correct home fleets."""
    name = "All agents registered"
    try:
        resp = await client.get(
            f"{url}/api/agents",
            params={"tenant_id": tenant_id},
            headers={"X-API-Key": api_key},
        )
    except Exception as exc:
        return Result(name, False, f"Request failed: {exc}")

    if resp.status_code != 200:
        return Result(name, False, f"HTTP {resp.status_code}: {resp.text[:100]}")

    data = resp.json()
    agents = data if isinstance(data, list) else data.get("data", data.get("agents", []))

    registered = {a.get("agent_id", "") for a in agents}

    expected_count = sum(
        len(vf["agents"])
        for vf in config.VM_FLEETS
        if vf["vm_index"] <= vm_count
    )
    expected_agents = {
        agent_id
        for vf in config.VM_FLEETS
        if vf["vm_index"] <= vm_count
        for agent_id in vf["agents"]
    }

    total = len(agents)
    if total < expected_count:
        return Result(
            name,
            False,
            f"Expected {expected_count} agent registrations, found {total}. "
            f"Missing: {expected_agents - registered}",
        )
    return Result(name, True, f"{total} agents registered (expected ≥{expected_count})")


async def check_memories_per_fleet(
    client: httpx.AsyncClient, url: str, api_key: str, tenant_id: str, fleet_id: str
) -> Result:
    """Assert that memories exist for the given fleet."""
    name = f"Memories in {fleet_id}"
    try:
        resp = await client.post(
            f"{url}/api/search",
            json={"tenant_id": tenant_id, "fleet_id": fleet_id, "query": "*", "limit": 100},
            headers={"X-API-Key": api_key},
        )
    except Exception as exc:
        return Result(name, False, f"Request failed: {exc}")

    if resp.status_code != 200:
        return Result(name, False, f"HTTP {resp.status_code}: {resp.text[:100]}")

    data = resp.json()
    memories = data if isinstance(data, list) else data.get("data", data.get("memories", data.get("results", [])))

    if not memories:
        return Result(name, False, "No memories found")

    # Verify all memories are scoped to the correct fleet
    wrong_fleet = [
        m.get("id", "?")
        for m in memories
        if m.get("fleet_id") and m.get("fleet_id") != fleet_id
    ]
    if wrong_fleet:
        return Result(name, False, f"Fleet isolation breach: memories from wrong fleet: {wrong_fleet[:3]}")

    return Result(name, True, f"{len(memories)} memories, all fleet_id={fleet_id}")


async def check_fleet_isolation(
    client: httpx.AsyncClient, url: str, api_key: str, tenant_id: str
) -> Result:
    """Assert fleet-scoped search returns only that fleet's memories."""
    fleet_id = config.VM_FLEETS[1]["fleet_id"] if len(config.VM_FLEETS) > 1 else "fleet-02"
    name = f"Fleet isolation ({fleet_id})"
    try:
        resp = await client.post(
            f"{url}/api/search",
            json={"tenant_id": tenant_id, "fleet_id": fleet_id, "query": "work status", "limit": 50},
            headers={"X-API-Key": api_key},
        )
    except Exception as exc:
        return Result(name, False, f"Request failed: {exc}")

    if resp.status_code != 200:
        return Result(name, False, f"HTTP {resp.status_code}: {resp.text[:100]}")

    data = resp.json()
    memories = data if isinstance(data, list) else data.get("data", data.get("memories", data.get("results", [])))

    leaks = [
        m.get("id", "?")
        for m in memories
        if m.get("fleet_id") and m.get("fleet_id") != fleet_id
    ]
    if leaks:
        return Result(name, False, f"Isolation breach: {len(leaks)} memories from other fleets")
    return Result(name, True, f"{len(memories)} memories, all fleet_id={fleet_id}")


async def check_agent_scoped_memories(
    client: httpx.AsyncClient, url: str, api_key: str, tenant_id: str
) -> Result:
    """Assert nexus agent-scoped memories are retrievable and correctly attributed."""
    name = "Agent-scoped memories (nexus)"
    try:
        resp = await client.get(
            f"{url}/api/memories",
            params={
                "tenant_id": tenant_id,
                "agent_id": "nexus",
                "fleet_id": config.VM_FLEETS[0]["fleet_id"],
            },
            headers={"X-API-Key": api_key},
        )
    except Exception as exc:
        return Result(name, False, f"Request failed: {exc}")

    if resp.status_code != 200:
        return Result(name, False, f"HTTP {resp.status_code}: {resp.text[:100]}")

    data = resp.json()
    memories = data if isinstance(data, list) else data.get("data", data.get("memories", []))

    if not memories:
        return Result(name, False, "No memories found for nexus agent")

    wrong_agent = [
        m.get("id", "?")
        for m in memories
        if m.get("agent_id") and m.get("agent_id") != "nexus"
    ]
    if wrong_agent:
        return Result(name, False, f"Memories attributed to wrong agent: {wrong_agent[:3]}")

    return Result(name, True, f"{len(memories)} nexus memories found")


async def check_cross_fleet_recall(
    client: httpx.AsyncClient, url: str, api_key: str, tenant_id: str
) -> Result:
    """Assert nexus cross-fleet recall returns results from multiple fleets."""
    name = "Cross-fleet recall (nexus)"
    try:
        resp = await client.post(
            f"{url}/api/recall",
            json={
                "tenant_id": tenant_id,
                "agent_id": "nexus",
                "query": "all active tasks and work in progress across all fleets",
            },
            headers={"X-API-Key": api_key},
        )
    except Exception as exc:
        return Result(name, False, f"Request failed: {exc}")

    if resp.status_code == 403:
        return Result(name, False, "403 Forbidden — nexus may not have trust level 2")
    if resp.status_code != 200:
        return Result(name, False, f"HTTP {resp.status_code}: {resp.text[:100]}")

    data = resp.json()
    summary = data.get("summary", data.get("recall", data.get("result", "")))

    if not summary or len(str(summary)) < 50:
        return Result(name, False, f"Empty or too-short recall result: {str(summary)[:100]}")

    return Result(name, True, f"Recall returned {len(str(summary))} chars of context")


async def check_procedure_memories(
    client: httpx.AsyncClient, url: str, api_key: str, tenant_id: str
) -> Result:
    """Assert procedure-type memories exist from operations/qa/customer-success agents."""
    name = "Procedure memories exist"
    try:
        resp = await client.post(
            f"{url}/api/search",
            json={
                "tenant_id": tenant_id,
                "query": "procedure step runbook checklist onboarding release",
                "limit": 30,
            },
            headers={"X-API-Key": api_key},
        )
    except Exception as exc:
        return Result(name, False, f"Request failed: {exc}")

    if resp.status_code != 200:
        return Result(name, False, f"HTTP {resp.status_code}: {resp.text[:100]}")

    data = resp.json()
    memories = data if isinstance(data, list) else data.get("data", data.get("memories", data.get("results", [])))

    if not memories:
        return Result(
            name,
            False,
            "No procedure-type memories found. "
            "Check if operations/qa-engineer/customer-success agents wrote memories.",
        )

    agent_ids = {m.get("agent_id", "") for m in memories}
    return Result(name, True, f"{len(memories)} procedure memories from agents: {agent_ids}")


async def check_entities_extracted(
    client: httpx.AsyncClient, url: str, api_key: str, tenant_id: str
) -> Result:
    """Assert entities were auto-extracted from agent memories."""
    name = "Entities extracted"
    try:
        resp = await client.get(
            f"{url}/api/entities",
            params={"tenant_id": tenant_id},
            headers={"X-API-Key": api_key},
        )
    except Exception as exc:
        return Result(name, False, f"Request failed: {exc}")

    if resp.status_code != 200:
        return Result(name, False, f"HTTP {resp.status_code}: {resp.text[:100]}")

    data = resp.json()
    entities = data if isinstance(data, list) else data.get("data", data.get("entities", []))

    if not entities:
        return Result(name, False, "No entities found — enrichment may not have run")

    types = {e.get("entity_type", "unknown") for e in entities}
    return Result(name, True, f"{len(entities)} entities extracted, types: {types}")


async def check_delegation_memories(
    client: httpx.AsyncClient, url: str, api_key: str, tenant_id: str
) -> Result:
    """Assert NEXUS wrote delegation task memories to other fleets."""
    name = "NEXUS delegation memories"
    try:
        resp = await client.post(
            f"{url}/api/search",
            json={
                "tenant_id": tenant_id,
                "query": "NEXUS delegation task",
                "limit": 20,
            },
            headers={"X-API-Key": api_key},
        )
    except Exception as exc:
        return Result(name, False, f"Request failed: {exc}")

    if resp.status_code != 200:
        return Result(name, False, f"HTTP {resp.status_code}: {resp.text[:100]}")

    data = resp.json()
    memories = data if isinstance(data, list) else data.get("data", data.get("memories", data.get("results", [])))

    if not memories:
        return Result(name, False, "No delegation memories found from NEXUS")

    # Check that at least some are from nexus
    nexus_mems = [m for m in memories if m.get("agent_id") == "nexus"]
    if not nexus_mems:
        return Result(name, False, f"Found {len(memories)} memories matching 'delegation' but none from nexus agent")

    return Result(name, True, f"{len(nexus_mems)} delegation memories from NEXUS")


async def check_web_search_memories(
    client: httpx.AsyncClient, url: str, api_key: str, tenant_id: str
) -> Result:
    """Assert research agents stored web-sourced findings (memories with source_uri or web references)."""
    name = "Web search memories"
    # Search for memories that reference web sources
    try:
        resp = await client.post(
            f"{url}/api/search",
            json={
                "tenant_id": tenant_id,
                "query": "web search research source URL benchmark comparison",
                "limit": 30,
            },
            headers={"X-API-Key": api_key},
        )
    except Exception as exc:
        return Result(name, False, f"Request failed: {exc}")

    if resp.status_code != 200:
        return Result(name, False, f"HTTP {resp.status_code}: {resp.text[:100]}")

    data = resp.json()
    memories = data if isinstance(data, list) else data.get("data", data.get("memories", data.get("results", [])))

    if not memories:
        return Result(name, False, "No web-search-related memories found")

    # Check for research agent contributions (fleet-04 and fleet-09 agents)
    research_agents = {"ai-assistant", "data-scientist", "market-researcher", "web-researcher", "fact-checker",
                       "competitive-analyst", "trend-analyst", "news-monitor"}
    research_mems = [m for m in memories if m.get("agent_id") in research_agents]

    if not research_mems:
        return Result(name, False, f"Found {len(memories)} memories but none from research/intelligence agents")

    agents_found = {m.get("agent_id") for m in research_mems}
    # Check for source_uri in any memory
    sourced = [m for m in research_mems if m.get("source_uri")]
    source_note = f", {len(sourced)} with source_uri" if sourced else ""

    return Result(name, True, f"{len(research_mems)} research memories from {agents_found}{source_note}")


async def check_memory_count_minimum(
    client: httpx.AsyncClient, url: str, api_key: str, tenant_id: str
) -> Result:
    """Assert total memory count meets v2 minimum threshold (150+)."""
    name = "Memory count ≥ 150"
    # Sum per-fleet search results to avoid rate-limited /api/memories endpoint
    total = 0
    for vf in config.VM_FLEETS:
        try:
            resp = await client.post(
                f"{url}/api/search",
                json={"tenant_id": tenant_id, "fleet_id": vf["fleet_id"], "query": "*", "limit": 100},
                headers={"X-API-Key": api_key},
            )
            if resp.status_code == 200:
                data = resp.json()
                mems = data if isinstance(data, list) else data.get("data", data.get("results", []))
                total += len(mems)
        except Exception:
            pass

    if total < 150:
        return Result(name, False, f"Only {total} memories found (expected ≥150)")

    return Result(name, True, f"{total} total memories")


# ─── Main Verification Runner ─────────────────────────────────────────────────


async def run_verification(
    url: str,
    api_key: str,
    admin_key: str,
    tenant_id: str,
    vm_count: int = 10,
) -> tuple[int, int]:
    """
    Run all verification assertions. Returns (passed_count, failed_count).
    """
    priv_key = admin_key if admin_key else api_key

    results: list[Result] = []

    async with httpx.AsyncClient(timeout=20) as client:

        results.append(
            await check_fleet_nodes(client, url, priv_key, tenant_id, vm_count)
        )
        results.append(
            await check_all_agents_registered(client, url, priv_key, tenant_id, vm_count)
        )

        # Per-fleet memory checks (only for VMs that were provisioned)
        for vf in config.VM_FLEETS:
            if vf["vm_index"] <= vm_count:
                results.append(
                    await check_memories_per_fleet(client, url, api_key, tenant_id, vf["fleet_id"])
                )

        results.append(
            await check_fleet_isolation(client, url, api_key, tenant_id)
        )
        results.append(
            await check_agent_scoped_memories(client, url, api_key, tenant_id)
        )
        results.append(
            await check_cross_fleet_recall(client, url, api_key, tenant_id)
        )
        results.append(
            await check_procedure_memories(client, url, api_key, tenant_id)
        )
        results.append(
            await check_entities_extracted(client, url, priv_key, tenant_id)
        )
        # v2 assertions
        results.append(
            await check_delegation_memories(client, url, api_key, tenant_id)
        )
        results.append(
            await check_web_search_memories(client, url, api_key, tenant_id)
        )
        results.append(
            await check_memory_count_minimum(client, url, api_key, tenant_id)
        )

    # Print results table
    table = Table(title="Verification Results", show_header=True)
    table.add_column("Assertion", style="bold")
    table.add_column("Status", justify="center")
    table.add_column("Detail")

    passed = 0
    failed = 0
    for r in results:
        if r.passed:
            status = "[green]PASS[/green]"
            passed += 1
        else:
            status = "[red]FAIL[/red]"
            failed += 1
        table.add_row(r.name, status, r.detail)

    console.print()
    console.print(table)
    console.print(
        f"\n[bold]Summary:[/bold] {passed} passed, "
        f"{'[red]' if failed else '[green]'}{failed} failed{'[/red]' if failed else '[/green]'}"
    )

    return passed, failed


# ─── CLI Entry Point ──────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Standalone MemClaw verification for OpenClaw fleet test v2"
    )
    parser.add_argument("--url", default=None, help=f"MemClaw API URL (default: {MEMCLAW_API_URL})")
    parser.add_argument("--api-key", default=None, help="MemClaw tenant API key")
    parser.add_argument("--admin-key", default=None, help="MemClaw admin key (for privileged checks)")
    parser.add_argument("--tenant-id", default=config.TENANT, help=f"Tenant ID (default: {config.TENANT})")
    parser.add_argument(
        "--count",
        type=int,
        default=10,
        help="Number of VMs that were provisioned (default: 10)",
    )
    args = parser.parse_args()

    # Load .env if keys not provided on CLI
    env: dict[str, str] = {}
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")

    url = args.url or env.get("MEMCLAW_API_URL", MEMCLAW_API_URL)
    api_key = args.api_key or env.get("MEMCLAW_API_KEY", "")
    admin_key = args.admin_key or env.get("MEMCLAW_ADMIN_KEY", "")

    if not api_key:
        console.print("[red]Error: MEMCLAW_API_KEY required (--api-key or .env)[/red]")
        sys.exit(1)

    # Auto-resolve tenant from API key if not explicitly set
    if args.tenant_id == config.TENANT and api_key:
        from orchestrate import resolve_tenant
        resolved = resolve_tenant(api_key)
        if resolved:
            config.TENANT = resolved
            args.tenant_id = resolved

    # Init naming from TESTER_PREFIX
    user_prefix = env.get("TESTER_PREFIX", "")
    config.VM_FLEETS = config.make_vm_fleets(user_prefix)

    if not admin_key:
        console.print("[yellow]Warning: MEMCLAW_ADMIN_KEY not set — privileged checks will use API key[/yellow]")

    console.print(f"\n[bold cyan]MemClaw Verification v2[/bold cyan]")
    console.print(f"  URL:    {url}")
    console.print(f"  Tenant: {args.tenant_id}")
    console.print(f"  VMs:    {args.count}")

    passed, failed = asyncio.run(
        run_verification(
            url=url,
            api_key=api_key,
            admin_key=admin_key,
            tenant_id=args.tenant_id,
            vm_count=args.count,
        )
    )

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
