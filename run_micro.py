#!/usr/bin/env python3
"""Run micro-tasks: short recall + single write per agent across 10 VMs / 50 agents."""

import asyncio
import os
import shlex
import sys
from pathlib import Path

import httpx
from rich.console import Console

from config import TENANT

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

# 50 micro-tasks: one per agent, recall one thing, store one thing. Fast.
MICRO = [
    # ── fleet-01 (vm-01) — Command & Product ──────────────
    (vm("01"), "nexus",
     "Recall org-wide status. Then store ONE plan memory: 'v2 fleet initialization complete — 10 VMs, 50 agents online. Delegation wave ready.'"),
    (vm("01"), "chief-of-staff",
     "Recall what NEXUS reported. Then store ONE fact memory: 'Weekly executive briefing: all 10 fleets operational, payment gateway initiative on track.'"),
    (vm("01"), "program-manager",
     "Recall fleet status. Then store ONE task memory: 'Milestone M1: Payment Gateway architecture review due in 2 weeks.'"),
    (vm("01"), "product-manager",
     "Recall program milestones. Then store ONE task memory: 'User story US-001: As a merchant, I can process a credit card payment via API.'"),
    (vm("01"), "technical-writer",
     "Recall product specs. Then store ONE fact memory: 'API doc section: POST /payments endpoint accepts amount, currency, and payment_method fields.'"),

    # ── fleet-02 (vm-02) — Engineering ────────────────────
    (vm("02"), "eng-architect",
     "Recall NEXUS delegation tasks. Then store ONE decision memory: 'ADR-001: Use PostgreSQL with read replicas for the payment gateway to avoid single-point-of-failure.'"),
    (vm("02"), "backend-engineer",
     "Recall architecture decisions. Then store ONE decision memory: 'Schema decision: payments table uses UUID primary key, amount stored as bigint in minor currency units.'"),
    (vm("02"), "frontend-engineer",
     "Recall product specs. Then store ONE fact memory: 'Component spec: PaymentForm uses controlled inputs with real-time card validation via Luhn check.'"),
    (vm("02"), "data-engineer",
     "Recall architecture decisions. Then store ONE decision memory: 'Pipeline design: payment events stream to Kafka topic payment.events with 7-day retention.'"),
    (vm("02"), "devops-engineer",
     "Recall architecture decisions. Then store ONE procedure memory: 'CI/CD stage 1: Run unit tests, integration tests, and SAST scan before merge to main.'"),

    # ── fleet-03 (vm-03) — Reliability & Ops ─────────────
    (vm("03"), "operations",
     "Recall fleet status. Then store ONE procedure memory: 'P1 runbook step 1: Declare incident in PagerDuty, assign Incident Commander, open bridge call.'"),
    (vm("03"), "sre-engineer",
     "Recall architecture decisions. Then store ONE fact memory: 'SLO-001: Payment API availability target 99.95%, error budget 21.6 minutes per month.'"),
    (vm("03"), "release-manager",
     "Recall QA status. Then store ONE procedure memory: 'Release checklist step 1: Verify all CI pipelines green, no P1 bugs open, QA sign-off obtained.'"),
    (vm("03"), "qa-engineer",
     "Recall the P1 runbook. Then store ONE task memory: 'Test case TC-001: Verify POST /payments returns 201 with valid card, correct amount, and idempotency key.'"),
    (vm("03"), "security-engineer",
     "Recall architecture decisions. Then store ONE fact memory: 'Threat finding: STRIDE-001 (Spoofing) — API key auth without IP allowlist rated medium severity.'"),

    # ── fleet-04 (vm-04) — Research Hub (web search) ─────
    (vm("04"), "ai-assistant",
     "Use web search to find Pinecone pricing. Then store ONE fact memory: 'Pinecone pricing 2025: Starter free tier, Standard from $70/mo, Enterprise custom. Source: pinecone.io/pricing'"),
    (vm("04"), "data-scientist",
     "Recall AI research findings. Then store ONE fact memory: 'ML evaluation metric: recall@10 measures fraction of relevant items in top 10 results, baseline 0.85 for production.'"),
    (vm("04"), "market-researcher",
     "Use web search to find vector DB market size. Then store ONE fact memory: 'Vector database TAM estimated at $3.2B by 2028, growing 25% CAGR. Source: market research report.'"),
    (vm("04"), "web-researcher",
     "Use brave_search for 'vector database benchmark 2025'. Then store ONE fact memory: 'ANN benchmark: Qdrant leads on latency (0.5ms p99), Milvus leads on throughput (50K qps). Source: ann-benchmarks.com'"),
    (vm("04"), "fact-checker",
     "Recall market researcher claims. Then store ONE fact memory: 'Verification: Vector DB TAM claim cross-referenced with 2 sources — confidence HIGH. Both Gartner and IDC cite similar range.'"),

    # ── fleet-05 (vm-05) — Finance ───────────────────────
    (vm("05"), "finance",
     "Recall NEXUS delegation. Then store ONE fact memory: 'Q2 assumption: monthly burn rate $85k, runway 14 months at current headcount of 20.'"),
    (vm("05"), "revenue-analyst",
     "Recall budget forecast. Then store ONE fact memory: 'ARR projection base case: $2.4M by Q4, assuming 15% MoM growth in payment API revenue.'"),
    (vm("05"), "procurement-agent",
     "Recall budget and architecture decisions. Then store ONE fact memory: 'Vendor evaluation: GCP committed-use discount saves 35% vs on-demand for e2-standard-2 instances.'"),
    (vm("05"), "tax-strategist",
     "Recall revenue projections. Then store ONE fact memory: 'R&D tax credit: payment gateway development qualifies under Section 41, estimated $120k credit for FY2025.'"),
    (vm("05"), "investor-relations",
     "Recall Q2 forecast and ARR projections. Then store ONE fact memory: 'Board update Q2: ARR at $2M, burn rate $85k/mo, runway 14 months, next funding round targeted Q1 2026.'"),

    # ── fleet-06 (vm-06) — Legal & Compliance ────────────
    (vm("06"), "legal",
     "Recall vendor evaluation. Then store ONE fact memory: 'NDA requirement: all payment processor agreements must include 2-year non-disclosure and data destruction clauses.'"),
    (vm("06"), "privacy-officer",
     "Recall architecture decisions. Then store ONE fact memory: 'PIA finding: payment gateway collects PAN data — requires PCI-DSS Level 1 compliance and tokenization.'"),
    (vm("06"), "ip-counsel",
     "Recall tech stack decisions. Then store ONE fact memory: 'License audit: PostgreSQL (PostgreSQL License — permissive), Kafka (Apache 2.0 — permissive), no copyleft risk.'"),
    (vm("06"), "regulatory-analyst",
     "Recall payment gateway plans. Then store ONE fact memory: 'Regulatory requirement: PCI-DSS v4.0 compliance mandatory by March 2025 for payment data handling.'"),

    # ── fleet-07 (vm-07) — Marketing & Growth ────────────
    (vm("07"), "marketing",
     "Recall product decisions. Then store ONE fact memory: '90-day GTM milestone: launch developer docs portal by Day 30, beta program by Day 60.'"),
    (vm("07"), "content-strategist",
     "Recall GTM plan. Then store ONE task memory: 'Content calendar: Week 1 — launch blog post, Week 2 — API tutorial, Week 3 — case study with beta customer.'"),
    (vm("07"), "growth-hacker",
     "Recall marketing positioning. Then store ONE fact memory: 'Growth experiment GE-001: Free sandbox with 1000 test transactions/month, hypothesis: 30% convert to paid in 90 days.'"),
    (vm("07"), "brand-manager",
     "Recall marketing strategy. Then store ONE fact memory: 'Brand guideline: product name is PaymentFlow, color palette is navy (#1a237e) + white, tone is technical but approachable.'"),
    (vm("07"), "community-manager",
     "Recall GTM plan. Then store ONE fact memory: 'Community launch plan: Discord server with #api-help, #feedback, #showcase channels. Champion program starts Week 4.'"),

    # ── fleet-08 (vm-08) — Revenue & Customer ────────────
    (vm("08"), "customer-success",
     "Recall onboarding best practices. Then store ONE procedure memory: 'Onboarding step 1: Schedule kickoff call within 48 hours of contract signature, share API credentials.'"),
    (vm("08"), "sales-strategist",
     "Recall marketing positioning. Then store ONE fact memory: 'Sales qualification: MEDDIC — Metrics: payment volume >$1M/mo, Decision criteria: API latency <200ms p99.'"),
    (vm("08"), "solutions-architect",
     "Recall architecture decisions. Then store ONE fact memory: 'Reference architecture: Direct API integration via REST, webhook for async notifications, batch for reconciliation.'"),
    (vm("08"), "support-engineer",
     "Recall API documentation. Then store ONE fact memory: 'KB article: Common error 402 — insufficient funds. Resolution: verify card balance, retry with fallback payment method.'"),
    (vm("08"), "account-manager",
     "Recall customer success procedures. Then store ONE fact memory: 'Account plan template: health score = (API uptime × 0.4) + (support tickets × 0.3) + (expansion signals × 0.3).'"),

    # ── fleet-09 (vm-09) — Design & Intelligence (web search) ─
    (vm("09"), "product-designer",
     "Recall product specs. Then store ONE fact memory: 'Checkout flow: 4 screens — card entry → 3DS challenge → processing spinner → confirmation with receipt download.'"),
    (vm("09"), "ux-researcher",
     "Recall checkout flow design. Then store ONE fact memory: 'Usability study plan: 5 tasks (add card, pay, retry failed, request refund, view history), 8 participants, think-aloud protocol.'"),
    (vm("09"), "localization-lead",
     "Recall checkout flow. Then store ONE fact memory: 'Localization priority: en-US (launch), es-MX, fr-FR, de-DE, ja-JP. Currency formatting per ICU standards.'"),
    (vm("09"), "competitive-analyst",
     "Use web search for 'Stripe pricing 2025'. Then store ONE fact memory: 'Stripe pricing: 2.9% + $0.30 per transaction, volume discounts at $80K+/mo. Source: stripe.com/pricing'"),
    (vm("09"), "trend-analyst",
     "Use web search for 'payment technology trends 2025'. Then store ONE fact memory: 'Trend: Real-time payments (RTP) adoption growing 40% YoY, expected to surpass card transactions by 2028.'"),
    (vm("09"), "news-monitor",
     "Use brave_search for 'payment industry news March 2025'. Then store ONE fact memory: 'News: Visa announced new tokenization standard for embedded payments, effective Q3 2025. Source: payments journal.'"),

    # ── fleet-10 (vm-10) — Specialized Domains ───────────
    (vm("10"), "algotrader",
     "Recall market data. Then store ONE fact memory: 'Momentum strategy rule: enter long when 3-day return exceeds 21-day return by 0.8 standard deviations.'"),
    (vm("10"), "home-assistant",
     "Recall any existing preferences. Then store ONE preference memory: 'User prefers Mediterranean cuisine, no shellfish, shops on Sundays, budget $150/week.'"),
    (vm("10"), "supply-chain-analyst",
     "Recall procurement evaluations. Then store ONE fact memory: 'Supply chain risk: single-vendor GCP dependency rated medium. Recommend multi-cloud DR with AWS us-east-1 failover.'"),
    (vm("10"), "sustainability-officer",
     "Recall infrastructure plans. Then store ONE fact memory: 'Carbon footprint: 10 e2-standard-2 VMs in us-central1 emit ~0.8 tCO2e/year. GCP matches 100% renewable energy.'"),
    (vm("10"), "talent-recruiter",
     "Recall program milestones. Then store ONE task memory: 'Hiring plan: 2 backend engineers (senior, L5+), 1 frontend engineer (mid, L4), 1 SRE (senior). Target close by Q3.'"),
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
    # Resolve tenant
    from orchestrate import resolve_tenant
    tenant = resolve_tenant(API_KEY) or TENANT

    # Check what already exists
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get("https://memclaw.net/api/memories",
                        params={"tenant_id": tenant, "limit": 1000},
                        headers={"X-API-Key": API_KEY})
    existing = {(m.get("fleet_id"), m.get("agent_id")) for m in (r.json() if isinstance(r.json(), list) else [])}

    console.print(f"[bold cyan]Micro-task Runner v2[/bold cyan] — {len(MICRO)} tasks, sequential")
    console.print(f"  Existing (fleet, agent) pairs: {len(existing)}")

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

    # Final count per fleet
    async with httpx.AsyncClient(timeout=15) as c:
        for i in range(1, 11):
            f = f"{PREFIX}-fleet-{i:02d}"
            r = await c.get("https://memclaw.net/api/memories",
                            params={"tenant_id": tenant, "fleet_id": f, "limit": 200},
                            headers={"X-API-Key": API_KEY})
            m = r.json() if isinstance(r.json(), list) else []
            a = sorted({x.get("agent_id") for x in m})
            console.print(f"  {f}: {len(m)} memories — {a}")


if __name__ == "__main__":
    asyncio.run(main())
