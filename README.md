# openclaw-fleet-tester

End-to-end test harness that provisions a fleet of GCP VMs, installs [OpenClaw](https://openclaw.ai) with the [MemClaw](https://memclaw.net) memory plugin, runs 20 AI agents across 3 fleets, and verifies that memories were stored, scoped, and recalled correctly.

## What it tests

| Check | What passes |
|-------|-------------|
| Fleet nodes online | All VMs appear in MemClaw Fleet UI |
| Agents registered | 20 agents (8+7+5) auto-register on first memory write |
| Fleet isolation | fleet-02 search returns only fleet-02 memories |
| Agent-scoped memories | Agent-private memories not visible cross-agent |
| Cross-fleet recall (NEXUS) | Master orchestrator reads memories from all 3 fleets |
| Memory types | `procedure` memories from ops/QA/CS agents |
| Entity extraction | People, technologies, orgs auto-extracted from content |

## Fleet layout (3 VMs)

| VM | Fleet | Agents |
|----|-------|--------|
| vm-01 | test-fleet-01 | nexus *(orchestrator)*, ai-assistant, eng-architect, marketing, finance, legal, home-assistant, customer-success |
| vm-02 | test-fleet-02 | operations, qa-engineer, algotrader, marketing, legal, finance, eng-architect |
| vm-03 | test-fleet-03 | ai-assistant, qa-engineer, home-assistant, customer-success, algotrader |

## Prerequisites

- `gcloud` CLI authenticated (`gcloud auth login`)
- GCP project with Compute Engine API enabled
- MemClaw account at [memclaw.net](https://memclaw.net) with an API key
- OpenAI API key (used by OpenClaw agents)
- Python 3.11+

## Setup

```bash
git clone https://github.com/your-org/openclaw-fleet-tester
cd openclaw-fleet-tester

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env with your keys (see .env.example for required fields)
```

## Run

```bash
# Full run — provision → bootstrap → plugin → agents → tasks → verify
python orchestrate.py --phase all --count 3

# Individual phases
python orchestrate.py --phase provision --count 3
python orchestrate.py --phase bootstrap
python orchestrate.py --phase plugin
python orchestrate.py --phase agents
python orchestrate.py --phase tasks
python orchestrate.py --phase verify

# Standalone verification (no VM changes)
python verify.py

# Teardown (prompts for confirmation)
python orchestrate.py --phase teardown
```

## Required `.env` fields

| Key | Required | Description |
|-----|----------|-------------|
| `OPENAI_API_KEY` | ✅ | OpenAI key — passed to each VM to authenticate OpenClaw |
| `MEMCLAW_API_KEY` | ✅ | MemClaw tenant API key — used for plugin install + verify |
| `MEMCLAW_ADMIN_KEY` | optional | Admin key for NEXUS trust promotion + privileged queries |
| `GCP_PROJECT` | ✅ | GCP project ID |
| `GCP_ZONE` | optional | Default: `us-central1-a` |
| `VM_COUNT` | optional | Default: `3` (max: 10, but fleet defs cover 3) |

See `.env.example` for a ready-to-copy template.

## How it works

### Phase 1 — Provision
Creates `openclaw-test-vm-{01..N}` GCP VMs (Debian 12, e2-standard-2) in parallel and waits for SSH readiness.

### Phase 2 — Bootstrap
Installs OpenClaw via the official one-liner on each VM in parallel. Runs non-interactive onboard with `--install-daemon` to start the gateway as a systemd service.

### Phase 3 — Plugin
Installs the MemClaw plugin on each VM via the official one-liner. Each VM gets its own `fleet_id` (`test-fleet-01`, `test-fleet-02`, `test-fleet-03`). Verifies nodes appear in the Fleet UI within 60s.

### Phase 4 — Agents
Generates workspace files (SOUL.md, IDENTITY.md, TOOLS.md, AGENTS.md, BOOTSTRAP.md, HEARTBEAT.md) for all 20 agents locally, SCPs them to each VM, and registers each agent with `openclaw agents add`.

### Phase 5 — Tasks
Runs all non-nexus agents in parallel via `openclaw agent --agent <id> --message "..."`. Skips agents that already have memories (idempotent). After all others complete:
1. Registers NEXUS
2. Promotes NEXUS to trust level 2 (cross-fleet reads) via MemClaw API
3. Runs NEXUS cross-fleet recall task

### Phase 6 — Verify
Runs 8 assertions against the MemClaw API and prints a pass/fail table.

## Files

| File | Purpose |
|------|---------|
| `orchestrate.py` | Main runner — all 7 phases, `--phase` flag |
| `config.py` | Fleet/agent definitions, task prompts, workspace file builders |
| `verify.py` | Standalone verification — 8 assertions |
| `requirements.txt` | `httpx`, `rich` |
| `.env.example` | Template for required environment variables |
