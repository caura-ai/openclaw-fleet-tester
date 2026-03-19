#!/usr/bin/env python3
"""Run missing agents sequentially with recall-heavy tasks.

Each task explicitly instructs the agent to recall existing memories first,
which drives up the RECALLS counter on those memories.
"""

import asyncio
import os
import shlex
import sys
from pathlib import Path

import httpx
from rich.console import Console

console = Console()

# Load .env
env_path = Path(__file__).parent / ".env"
for line in env_path.read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

API_KEY = os.environ["MEMCLAW_API_KEY"]
TENANT = "ernitest2"  # will be resolved
PROJECT = os.environ.get("GCP_PROJECT", "alpine-theory-469016-c8")
ZONE = os.environ.get("GCP_ZONE", "us-central1-a")
PREFIX = os.environ.get("TESTER_PREFIX", "erni")

# Recall-heavy tasks — each one forces the agent to recall before writing
# Phase 1: In-fleet recall tasks — agents recall fleet-mates' work, then produce their own
RECALL_TASKS = {
    # ─── erni-fleet-01 (nexus already wrote 2 memories) ──────────────────────
    "ai-assistant": (
        "Use memclaw_recall to find what NEXUS has written about the fleet status. "
        "Then research the top 3 vector databases for production use. "
        "Store a comparison finding per database as individual fact memories."
    ),
    "eng-architect": (
        "Use memclaw_recall to find the NEXUS cross-fleet status report in this fleet. "
        "Then design a microservices architecture for a payment gateway. "
        "Store each design decision as a separate decision memory (ADR format)."
    ),
    "marketing": (
        "Use memclaw_recall to check what other agents in this fleet have stored. "
        "Then develop a 90-day GTM plan for a developer-tools SaaS. "
        "Store positioning decisions and key milestones as separate memories."
    ),
    "finance": (
        "Use memclaw_recall to find any plans or decisions stored by fleet-mates. "
        "Then build a Q2 budget forecast for a 20-person startup. "
        "Store each assumption and line-item projection as a separate fact memory."
    ),
    "legal": (
        "Use memclaw_recall to check what decisions and plans exist in this fleet. "
        "Then draft a vendor NDA review checklist. "
        "Store each requirement as an individual fact memory."
    ),
    "home-assistant": (
        "Use memclaw_recall to find any stored preferences or prior meal plans in this fleet. "
        "Then plan a 5-day Mediterranean meal plan with a shopping list. "
        "Store the meal plan and preferences as separate memories."
    ),
    "customer-success": (
        "Use memclaw_recall to find any procedures or plans stored in this fleet. "
        "Then create a new-customer onboarding checklist for a B2B SaaS. "
        "Store each step as a separate procedure memory."
    ),
    # ─── erni-fleet-02 (operations already wrote 7 memories) ─────────────────
    "qa-engineer": (
        "Use memclaw_recall to find the P1 database outage runbook stored by operations in this fleet. "
        "Then write test cases that validate each runbook step. "
        "Store each test case as a separate task memory."
    ),
    "algotrader": (
        "Use memclaw_recall to check what the operations agent stored in this fleet. "
        "Then design a momentum-based crypto trading strategy. "
        "Store strategy overview, risk parameters, and entry/exit rules as separate memories."
    ),
    # ─── erni-fleet-03 (home-assistant already wrote 2 memories) ─────────────
    # fleet-03 agents recall home-assistant's meal plan, then do their own work
}

# VM → fleet → agents mapping
VM_AGENTS = [
    ("01", "erni-fleet-01", ["ai-assistant", "eng-architect", "marketing", "finance", "legal", "home-assistant", "customer-success"]),
    ("02", "erni-fleet-02", ["qa-engineer", "algotrader", "marketing", "legal", "finance", "eng-architect"]),
    ("03", "erni-fleet-03", ["ai-assistant", "qa-engineer", "customer-success", "algotrader"]),
]


def vm_name(idx: str) -> str:
    return f"{PREFIX}-openclaw-vm-{idx}"


async def run_cmd(cmd: list[str], label: str, timeout: int = 1860) -> int:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        try:
            await asyncio.wait_for(proc.communicate(), timeout=5)
        except asyncio.TimeoutError:
            pass
        console.print(f"  [red]TIMEOUT[/red] {label}")
        return 124
    rc = proc.returncode or 0
    if rc == 0:
        console.print(f"  [green]OK[/green] {label}")
    else:
        err = stderr.decode(errors="replace").strip()[:150]
        console.print(f"  [yellow]rc={rc}[/yellow] {label}: {err}")
    return rc


async def get_existing_pairs() -> set[tuple[str, str]]:
    """Return (fleet_id, agent_id) pairs that already have memories."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            "https://memclaw.net/api/memories",
            params={"tenant_id": TENANT, "limit": 500},
            headers={"X-API-Key": API_KEY},
        )
    mems = resp.json() if isinstance(resp.json(), list) else []
    return {(m.get("fleet_id"), m.get("agent_id")) for m in mems}


async def main():
    existing = await get_existing_pairs()
    console.print(f"[bold cyan]Sequential Agent Runner[/bold cyan]")
    console.print(f"  Existing (fleet, agent) pairs: {len(existing)}")

    total_run = 0
    total_ok = 0

    for vm_idx, fleet_id, agents in VM_AGENTS:
        name = vm_name(vm_idx)
        console.print(f"\n[bold]{name} ({fleet_id})[/bold]")

        for agent_id in agents:
            if (fleet_id, agent_id) in existing:
                console.print(f"  [dim]skip {agent_id} — already has memories[/dim]")
                continue

            task = RECALL_TASKS.get(agent_id, "Recall all existing context, summarize your status, and store a brief status memory.")
            prompt_safe = shlex.quote(task)

            inner = (
                f"export PATH=$HOME/.npm-global/bin:$PATH; "
                f"export TASK_PROMPT={prompt_safe}; "
                f'timeout 1800 openclaw agent --agent {agent_id} --message "$TASK_PROMPT" --timeout 1800'
            )

            ssh = [
                "gcloud", "compute", "ssh", name,
                "--zone", ZONE, "--project", PROJECT,
                "--command", inner,
                "--quiet",
                "--", "-o", "StrictHostKeyChecking=no",
                "-o", "ConnectTimeout=30", "-o", "BatchMode=yes",
            ]

            console.print(f"  [dim]running {agent_id}...[/dim]")
            total_run += 1
            rc = await run_cmd(ssh, f"{agent_id}@{name}", timeout=1860)
            if rc == 0:
                total_ok += 1

            # Brief pause between agents to let MemClaw enrichment finish
            await asyncio.sleep(5)

    console.print(f"\n[bold]Done:[/bold] {total_ok}/{total_run} agents completed successfully")

    # Final memory count
    async with httpx.AsyncClient(timeout=15) as client:
        for fleet in ["erni-fleet-01", "erni-fleet-02", "erni-fleet-03"]:
            resp = await client.get(
                "https://memclaw.net/api/memories",
                params={"tenant_id": TENANT, "fleet_id": fleet, "limit": 500},
                headers={"X-API-Key": API_KEY},
            )
            mems = resp.json() if isinstance(resp.json(), list) else []
            agents = sorted({m.get("agent_id") for m in mems})
            console.print(f"  {fleet}: {len(mems)} memories — {agents}")


if __name__ == "__main__":
    asyncio.run(main())
