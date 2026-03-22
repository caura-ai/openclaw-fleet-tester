#!/usr/bin/env python3
"""Run missing agents sequentially with recall-heavy tasks across 10 VMs / 50 agents.

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
TENANT = "ernitest3"  # will be resolved
PROJECT = os.environ.get("GCP_PROJECT", "alpine-theory-469016-c8")
ZONE = os.environ.get("GCP_ZONE", "us-central1-a")
PREFIX = os.environ.get("TESTER_PREFIX", "erni")

# Recall-heavy tasks — each one forces the agent to recall before writing
RECALL_TASKS = {
    # ─── Fleet 01 — Command & Product ────────────────────────────────────
    "chief-of-staff": (
        "Use memclaw_recall to find what NEXUS has written about cross-fleet status. "
        "Then draft an executive action-item list covering all 10 fleets. "
        "Store each action item as a separate task memory."
    ),
    "program-manager": (
        "Use memclaw_recall to find NEXUS delegation tasks and engineering progress. "
        "Then create a program risk register with 5 risks across fleets. "
        "Store each risk as a separate memory with probability and impact."
    ),
    "product-manager": (
        "Use memclaw_recall to find engineering architecture decisions and QA test plans. "
        "Then write 5 user stories for the payment gateway MVP. "
        "Store each user story as a separate task memory."
    ),
    "technical-writer": (
        "Use memclaw_recall to find API documentation and architecture decisions. "
        "Then write quickstart guide sections for the payment gateway. "
        "Store each guide section as a separate memory."
    ),

    # ─── Fleet 02 — Engineering ──────────────────────────────────────────
    "eng-architect": (
        "Use memclaw_recall to find NEXUS delegation tasks for fleet-02. "
        "Then design the payment gateway service mesh: API gateway, payment service, "
        "ledger service, notification service. Store each ADR as a separate decision memory."
    ),
    "backend-engineer": (
        "Use memclaw_recall to find architecture decisions from eng-architect. "
        "Then design the database schema: payments, refunds, ledger_entries, webhooks tables. "
        "Store each table design as a separate decision memory."
    ),
    "frontend-engineer": (
        "Use memclaw_recall to find product specs and UI component requirements. "
        "Then design the React component hierarchy for the checkout flow. "
        "Store each component spec as a separate memory."
    ),
    "data-engineer": (
        "Use memclaw_recall to find architecture decisions and data requirements. "
        "Then design the event streaming pipeline: Kafka topics, schemas, consumers. "
        "Store each pipeline stage design as a separate memory."
    ),
    "devops-engineer": (
        "Use memclaw_recall to find architecture and SRE requirements. "
        "Then design the Kubernetes deployment: namespaces, deployments, services, HPA. "
        "Store each infrastructure component as a separate memory."
    ),

    # ─── Fleet 03 — Reliability & Ops ────────────────────────────────────
    "operations": (
        "Use memclaw_recall to find the current fleet status and any incidents. "
        "Then write a P1 database outage incident response runbook with 8 steps. "
        "Store each runbook step as a separate procedure memory."
    ),
    "sre-engineer": (
        "Use memclaw_recall to find architecture decisions and performance requirements. "
        "Then define SLOs for payment API: availability, latency, throughput, error rate. "
        "Store each SLO with its error budget as a separate memory."
    ),
    "release-manager": (
        "Use memclaw_recall to find QA test plans and CI/CD pipeline design. "
        "Then create a release checklist for payment gateway v1.0 with 10 items. "
        "Store each checklist item as a separate procedure memory."
    ),
    "qa-engineer": (
        "Use memclaw_recall to find the architecture, API specs, and runbooks. "
        "Then write test cases for the payment API: create, get, refund, webhook. "
        "Store each test case as a separate task memory."
    ),
    "security-engineer": (
        "Use memclaw_recall to find architecture decisions and deployment plans. "
        "Then perform STRIDE threat modeling for the payment gateway. "
        "Store each threat with severity and mitigation as a separate memory."
    ),

    # ─── Fleet 04 — Research Hub (web search) ────────────────────────────
    "ai-assistant": (
        "Use memclaw_recall to find what NEXUS delegated to fleet-04. "
        "Then use web search to research AI infrastructure trends in 2025. "
        "Store each finding with its source URL as a separate memory."
    ),
    "data-scientist": (
        "Use memclaw_recall to find AI research findings and vector DB comparisons. "
        "Then design an ML evaluation framework for search quality. "
        "Store each evaluation metric as a separate memory."
    ),
    "market-researcher": (
        "Use memclaw_recall to find existing market research and NEXUS tasks. "
        "Then use web search to analyze the payment gateway market landscape. "
        "Store each market insight with source URL as a separate memory."
    ),
    "web-researcher": (
        "Use memclaw_recall to find pending research requests. "
        "Then use brave_search to find latest vector DB benchmarks and payment API comparisons. "
        "Store each finding with source_uri as a separate memory."
    ),
    "fact-checker": (
        "Use memclaw_recall to find claims from market-researcher and web-researcher. "
        "Then verify the top 5 claims using web search. "
        "Store each verification with confidence rating as a separate memory."
    ),

    # ─── Fleet 05 — Finance ──────────────────────────────────────────────
    "finance": (
        "Use memclaw_recall to find NEXUS delegation tasks and revenue projections. "
        "Then build a detailed Q2 budget: headcount, infrastructure, marketing, legal. "
        "Store each line item as a separate fact memory."
    ),
    "revenue-analyst": (
        "Use memclaw_recall to find budget forecasts and market research. "
        "Then build ARR projection models: base, upside, downside. "
        "Store each scenario with assumptions as a separate memory."
    ),
    "procurement-agent": (
        "Use memclaw_recall to find budget and architecture decisions. "
        "Then evaluate AWS, GCP, Azure for the payment gateway hosting. "
        "Store each vendor evaluation with pricing as a separate memory."
    ),
    "tax-strategist": (
        "Use memclaw_recall to find revenue projections and R&D activities. "
        "Then identify all applicable tax credits and deductions. "
        "Store each tax planning item as a separate memory."
    ),
    "investor-relations": (
        "Use memclaw_recall to find Q2 forecast, ARR projections, and key metrics. "
        "Then draft 4 sections for the board update deck. "
        "Store each section as a separate memory."
    ),

    # ─── Fleet 06 — Legal & Compliance ───────────────────────────────────
    "legal": (
        "Use memclaw_recall to find vendor evaluations and architecture decisions. "
        "Then draft NDA review checklist for payment processor partners. "
        "Store each requirement as a separate memory."
    ),
    "privacy-officer": (
        "Use memclaw_recall to find architecture decisions and data flows. "
        "Then conduct privacy impact assessment for payment data handling. "
        "Store each privacy requirement as a separate memory."
    ),
    "ip-counsel": (
        "Use memclaw_recall to find tech stack and dependency decisions. "
        "Then audit open-source license compliance for all proposed dependencies. "
        "Store each license finding as a separate memory."
    ),
    "regulatory-analyst": (
        "Use memclaw_recall to find payment gateway plans and legal requirements. "
        "Then map PCI-DSS, PSD2, and state licensing requirements. "
        "Store each regulatory requirement as a separate memory."
    ),

    # ─── Fleet 07 — Marketing & Growth ───────────────────────────────────
    "marketing": (
        "Use memclaw_recall to find product specs and competitive intelligence. "
        "Then develop a 90-day GTM plan with Day 30/60/90 checkpoints. "
        "Store each milestone as a separate memory."
    ),
    "content-strategist": (
        "Use memclaw_recall to find GTM plan and product positioning. "
        "Then create a 12-week content calendar: blog, tutorial, case study, webinar. "
        "Store each content item as a separate memory."
    ),
    "growth-hacker": (
        "Use memclaw_recall to find marketing strategy and conversion data. "
        "Then design 3 growth experiments with hypotheses and success criteria. "
        "Store each experiment as a separate memory."
    ),
    "brand-manager": (
        "Use memclaw_recall to find marketing positioning and product naming. "
        "Then define brand guidelines: visual identity, tone, naming conventions. "
        "Store each guideline as a separate memory."
    ),
    "community-manager": (
        "Use memclaw_recall to find GTM plan and growth experiments. "
        "Then design developer community launch: channels, programs, events. "
        "Store each initiative as a separate memory."
    ),

    # ─── Fleet 08 — Revenue & Customer ───────────────────────────────────
    "customer-success": (
        "Use memclaw_recall to find product specs and sales process. "
        "Then create a 10-step customer onboarding checklist. "
        "Store each step as a separate procedure memory."
    ),
    "sales-strategist": (
        "Use memclaw_recall to find marketing positioning and competitive analysis. "
        "Then design the enterprise sales process: MEDDIC qualification, demo, POC, close. "
        "Store each process component as a separate memory."
    ),
    "solutions-architect": (
        "Use memclaw_recall to find architecture decisions and customer requirements. "
        "Then design 3 reference architectures for enterprise integrations. "
        "Store each reference architecture as a separate memory."
    ),
    "support-engineer": (
        "Use memclaw_recall to find API documentation and common issues. "
        "Then build knowledge base: top 5 issues with troubleshooting steps. "
        "Store each KB article as a separate memory."
    ),
    "account-manager": (
        "Use memclaw_recall to find customer success procedures and sales strategy. "
        "Then create account plan template with health scoring methodology. "
        "Store each template section as a separate memory."
    ),

    # ─── Fleet 09 — Design & Intelligence (web search) ───────────────────
    "product-designer": (
        "Use memclaw_recall to find product specs and UX research findings. "
        "Then design the payment checkout flow: 5 screens with transitions. "
        "Store each screen design as a separate memory."
    ),
    "ux-researcher": (
        "Use memclaw_recall to find checkout flow designs and product specs. "
        "Then design a usability study: tasks, metrics, recruitment criteria. "
        "Store each study component as a separate memory."
    ),
    "localization-lead": (
        "Use memclaw_recall to find UI designs and market research. "
        "Then define localization requirements for 5 target locales. "
        "Store each locale requirement as a separate memory."
    ),
    "competitive-analyst": (
        "Use memclaw_recall to find existing competitive intelligence. "
        "Then use web search to analyze Stripe, Square, Adyen pricing and features. "
        "Store each competitor analysis with source URLs as a separate memory."
    ),
    "trend-analyst": (
        "Use memclaw_recall to find market research and competitive analysis. "
        "Then use web search to identify 5 payment technology trends for 2025. "
        "Store each trend with evidence and source URLs as a separate memory."
    ),
    "news-monitor": (
        "Use memclaw_recall to find monitored topics and existing news items. "
        "Then use brave_search to find recent payment industry news. "
        "Store each news item with source URL and impact assessment as a separate memory."
    ),

    # ─── Fleet 10 — Specialized Domains ──────────────────────────────────
    "algotrader": (
        "Use memclaw_recall to find market data and trading strategies. "
        "Then design a momentum-based crypto trading strategy with entry/exit rules. "
        "Store strategy, risk parameters, and rules as separate memories."
    ),
    "home-assistant": (
        "Use memclaw_recall to find user preferences and dietary restrictions. "
        "Then plan a 7-day Mediterranean meal plan with shopping list. "
        "Store meal plan and preference updates as separate memories."
    ),
    "supply-chain-analyst": (
        "Use memclaw_recall to find procurement evaluations and infrastructure plans. "
        "Then analyze cloud supply chain: vendor risk, multi-cloud strategy, DR. "
        "Store each recommendation as a separate memory."
    ),
    "sustainability-officer": (
        "Use memclaw_recall to find infrastructure plans and procurement analysis. "
        "Then assess carbon footprint and recommend green hosting options. "
        "Store each sustainability metric as a separate memory."
    ),
    "talent-recruiter": (
        "Use memclaw_recall to find program milestones and team requirements. "
        "Then create hiring plans for 4 roles with job specs and interview rubrics. "
        "Store each job spec as a separate memory."
    ),
}

# VM → fleet → agents mapping (10 VMs)
VM_AGENTS = [
    ("01", f"{PREFIX}-fleet-01", ["chief-of-staff", "program-manager", "product-manager", "technical-writer"]),
    ("02", f"{PREFIX}-fleet-02", ["eng-architect", "backend-engineer", "frontend-engineer", "data-engineer", "devops-engineer"]),
    ("03", f"{PREFIX}-fleet-03", ["operations", "sre-engineer", "release-manager", "qa-engineer", "security-engineer"]),
    ("04", f"{PREFIX}-fleet-04", ["ai-assistant", "data-scientist", "market-researcher", "web-researcher", "fact-checker"]),
    ("05", f"{PREFIX}-fleet-05", ["finance", "revenue-analyst", "procurement-agent", "tax-strategist", "investor-relations"]),
    ("06", f"{PREFIX}-fleet-06", ["legal", "privacy-officer", "ip-counsel", "regulatory-analyst"]),
    ("07", f"{PREFIX}-fleet-07", ["marketing", "content-strategist", "growth-hacker", "brand-manager", "community-manager"]),
    ("08", f"{PREFIX}-fleet-08", ["customer-success", "sales-strategist", "solutions-architect", "support-engineer", "account-manager"]),
    ("09", f"{PREFIX}-fleet-09", ["product-designer", "ux-researcher", "localization-lead", "competitive-analyst", "trend-analyst", "news-monitor"]),
    ("10", f"{PREFIX}-fleet-10", ["algotrader", "home-assistant", "supply-chain-analyst", "sustainability-officer", "talent-recruiter"]),
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
    # Resolve tenant
    from orchestrate import resolve_tenant
    global TENANT
    resolved = resolve_tenant(API_KEY)
    if resolved:
        TENANT = resolved

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            "https://memclaw.net/api/memories",
            params={"tenant_id": TENANT, "limit": 1000},
            headers={"X-API-Key": API_KEY},
        )
    mems = resp.json() if isinstance(resp.json(), list) else []
    return {(m.get("fleet_id"), m.get("agent_id")) for m in mems}


async def main():
    existing = await get_existing_pairs()
    console.print(f"[bold cyan]Sequential Agent Runner v2[/bold cyan]")
    console.print(f"  Tenant: {TENANT}")
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

    # Final memory count per fleet
    async with httpx.AsyncClient(timeout=15) as client:
        for i in range(1, 11):
            fleet = f"{PREFIX}-fleet-{i:02d}"
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
