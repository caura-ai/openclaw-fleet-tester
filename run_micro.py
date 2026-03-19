#!/usr/bin/env python3
"""Run micro-tasks: short recall + single write per agent. Completes in ~2 min each."""

import asyncio
import os
import shlex
import sys
from pathlib import Path

import httpx
from rich.console import Console

console = Console()

for line in Path(__file__).parent.joinpath(".env").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

API_KEY = os.environ["MEMCLAW_API_KEY"]
PROJECT = os.environ.get("GCP_PROJECT", "alpine-theory-469016-c8")
ZONE = os.environ.get("GCP_ZONE", "us-central1-a")
PREFIX = os.environ.get("TESTER_PREFIX", "erni")

def vm(idx): return f"{PREFIX}-openclaw-vm-{idx}"

# Micro-tasks: recall one thing, store one thing. Fast.
MICRO = [
    # ── fleet-01 (vm-01) ─────────────────────────────
    (vm("01"), "eng-architect",
     "Recall what NEXUS reported. Then store ONE decision memory: 'ADR-001: Use PostgreSQL with read replicas for the payment gateway to avoid single-point-of-failure.'"),
    (vm("01"), "marketing",
     "Recall what ai-assistant found about vector databases. Then store ONE fact memory: '90-day GTM milestone: launch developer docs portal by Day 30.'"),
    (vm("01"), "finance",
     "Recall what NEXUS reported. Then store ONE fact memory: 'Q2 assumption: monthly burn rate $85k, runway 14 months at current headcount of 20.'"),
    (vm("01"), "legal",
     "Recall what eng-architect decided. Then store ONE fact memory: 'NDA requirement: all vendor agreements must include a 2-year non-disclosure clause.'"),
    (vm("01"), "home-assistant",
     "Recall any existing meal plans. Then store ONE preference memory: 'User prefers Mediterranean cuisine, no shellfish, shops on Sundays.'"),
    (vm("01"), "customer-success",
     "Recall what operations stored about incident response. Then store ONE procedure memory: 'Onboarding step 1: Schedule kickoff call within 48 hours of contract signature.'"),
    # ── fleet-02 (vm-02) ─────────────────────────────
    (vm("02"), "qa-engineer",
     "Recall the P1 runbook from operations. Then store ONE task memory: 'Test case TC-001: Verify runbook Step 1 (declare incident) triggers PagerDuty alert within 60 seconds.'"),
    (vm("02"), "algotrader",
     "Recall what operations stored. Then store ONE fact memory: 'Momentum strategy rule: enter long when 3-day return exceeds 21-day return by 0.8 standard deviations.'"),
    (vm("02"), "marketing",
     "Recall what operations wrote about P1 response. Then store ONE decision memory: 'Position our reliability story around sub-15-minute incident detection.'"),
    (vm("02"), "legal",
     "Recall what operations stored. Then store ONE fact memory: 'Compliance: P1 incidents must be reported to affected customers within 4 hours per SLA.'"),
    (vm("02"), "finance",
     "Recall what operations wrote. Then store ONE fact memory: 'Q2 line item: $12k/month allocated to infrastructure monitoring and alerting tools.'"),
    (vm("02"), "eng-architect",
     "Recall the P1 runbook. Then store ONE decision memory: 'ADR-002: Deploy active-passive database failover with automated health checks every 30 seconds.'"),
    # ── fleet-03 (vm-03) ─────────────────────────────
    (vm("03"), "ai-assistant",
     "Recall what home-assistant stored about preferences. Then store ONE fact memory: 'Vector DB recommendation: Pinecone for managed SaaS, pgvector for self-hosted PostgreSQL deployments.'"),
    (vm("03"), "qa-engineer",
     "Recall what home-assistant stored. Then store ONE task memory: 'Test case TC-002: Verify meal plan API returns 7 days of meals with valid nutritional data.'"),
    (vm("03"), "customer-success",
     "Recall what home-assistant stored. Then store ONE procedure memory: 'Onboarding step 2: Send welcome email with login credentials and quick-start guide.'"),
    (vm("03"), "algotrader",
     "Recall what home-assistant stored. Then store ONE fact memory: 'Mean-reversion entry rule: buy when price drops 2 ATR below 20-day VWAP with volume confirmation.'"),
]


async def run_one(vm_name: str, agent_id: str, prompt: str) -> int:
    inner = (
        f"export PATH=$HOME/.npm-global/bin:$PATH; "
        f"export TASK_PROMPT={shlex.quote(prompt)}; "
        f'timeout 300 openclaw agent --agent {agent_id} --message "$TASK_PROMPT" --timeout 300'
    )
    ssh = [
        "gcloud", "compute", "ssh", vm_name,
        "--zone", ZONE, "--project", PROJECT,
        "--command", inner, "--quiet",
        "--", "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=30", "-o", "BatchMode=yes",
    ]
    proc = await asyncio.create_subprocess_exec(
        *ssh, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=360)
    except asyncio.TimeoutError:
        proc.kill()
        try: await asyncio.wait_for(proc.communicate(), timeout=5)
        except: pass
        return 124
    return proc.returncode or 0


async def main():
    # Check what already exists
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get("https://memclaw.net/api/memories",
                        params={"tenant_id": "ernitest2", "limit": 500},
                        headers={"X-API-Key": API_KEY})
    existing = {(m.get("fleet_id"), m.get("agent_id")) for m in (r.json() if isinstance(r.json(), list) else [])}

    console.print(f"[bold cyan]Micro-task Runner[/bold cyan] — {len(MICRO)} tasks, sequential")

    ok = 0
    for i, (vm_name, agent_id, prompt) in enumerate(MICRO, 1):
        console.print(f"\n[{i}/{len(MICRO)}] {agent_id}@{vm_name}")
        console.print(f"  [dim]{prompt[:80]}...[/dim]")
        rc = await run_one(vm_name, agent_id, prompt)
        if rc == 0:
            console.print(f"  [green]OK[/green]")
            ok += 1
        else:
            console.print(f"  [yellow]rc={rc}[/yellow]")
        await asyncio.sleep(3)  # breathing room for MemClaw enrichment

    console.print(f"\n[bold]Done: {ok}/{len(MICRO)} completed[/bold]")

    # Final count
    async with httpx.AsyncClient(timeout=15) as c:
        for f in ["erni-fleet-01", "erni-fleet-02", "erni-fleet-03"]:
            r = await c.get("https://memclaw.net/api/memories",
                            params={"tenant_id": "ernitest2", "fleet_id": f, "limit": 200},
                            headers={"X-API-Key": API_KEY})
            m = r.json() if isinstance(r.json(), list) else []
            a = sorted({x.get("agent_id") for x in m})
            console.print(f"  {f}: {len(m)} memories — {a}")


if __name__ == "__main__":
    asyncio.run(main())
