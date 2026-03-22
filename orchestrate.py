#!/usr/bin/env python3
"""
OpenClaw Fleet Memory Test v2 — Orchestrator

Usage:
    python orchestrate.py --phase all --count 10
    python orchestrate.py --phase provision --count 10
    python orchestrate.py --phase bootstrap
    python orchestrate.py --phase plugin
    python orchestrate.py --phase agents
    python orchestrate.py --phase tasks
    python orchestrate.py --phase verify
    python orchestrate.py --phase teardown
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import os
import shlex
import sys
import tempfile
import time
from pathlib import Path

import httpx
from rich.console import Console
from rich.table import Table

import config
from config import (
    GCP_PROJECT,
    GCP_ZONE,
    MEMCLAW_API_URL,
    VM_COUNT_DEFAULT,
    TASKS,
    build_workspace_files,
    vm_name_prefix,
    make_vm_fleets,
)

console = Console()

PHASES_ORDERED = ["provision", "bootstrap", "plugin", "agents", "tasks", "verify", "teardown"]

# Runtime-resolved from env — set in main() before any phase runs
_VM_NAME_PREFIX: str = "openclaw-vm"
_VM_FLEETS: list[dict] = []


def _init_naming(user_prefix: str) -> None:
    global _VM_NAME_PREFIX, _VM_FLEETS
    _VM_NAME_PREFIX = vm_name_prefix(user_prefix)
    _VM_FLEETS = make_vm_fleets(user_prefix)

# ─── Environment ──────────────────────────────────────────────────────────────


def resolve_tenant(api_key: str) -> str | None:
    """Auto-resolve tenant_id from the MemClaw API key via install-plugin endpoint."""
    try:
        resp = httpx.get(
            f"{MEMCLAW_API_URL}/api/install-plugin",
            params={"api_key": api_key, "fleet_id": "probe", "api_url": MEMCLAW_API_URL},
            timeout=10,
        )
        if resp.status_code == 200:
            for line in resp.text.splitlines():
                if line.startswith("MEMCLAW_TENANT_ID="):
                    return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return None


def load_env() -> dict[str, str]:
    """Load .env from project directory into os.environ and return as dict."""
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        console.print("[red]Error: .env file not found at[/red]", env_path)
        console.print("Create it with OPENAI_API_KEY, MEMCLAW_API_KEY, MEMCLAW_ADMIN_KEY, etc.")
        sys.exit(1)

    env: dict[str, str] = {}
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip().strip('"').strip("'")
        env[key.strip()] = val
        os.environ.setdefault(key.strip(), val)

    return env


def vm_name(index: int) -> str:
    return f"{_VM_NAME_PREFIX}-{index:02d}"


# ─── Subprocess Helpers ───────────────────────────────────────────────────────


async def run_async(
    cmd: list[str],
    label: str = "",
    timeout: int = 600,
    env: dict | None = None,
) -> tuple[int, str, str]:
    """Run a subprocess asynchronously. Returns (returncode, stdout, stderr)."""
    proc_env = dict(os.environ)
    if env:
        proc_env.update(env)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=proc_env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        try:
            await asyncio.wait_for(proc.communicate(), timeout=5)
        except asyncio.TimeoutError:
            pass  # process truly stuck; move on
        return -1, "", f"Timeout after {timeout}s"

    rc = proc.returncode
    out = stdout.decode(errors="replace").strip()
    err = stderr.decode(errors="replace").strip()

    status = "[green]OK[/green]" if rc == 0 else f"[red]FAIL rc={rc}[/red]"
    tag = f" [{label}]" if label else ""
    console.print(f"  {status}{tag}")
    if rc != 0 and err:
        console.print(f"    [dim]{err[:300]}[/dim]")

    return rc, out, err


def ssh_cmd(
    name: str, command: str, project: str, zone: str, extra_flags: list[str] | None = None
) -> list[str]:
    """Build gcloud compute ssh command list."""
    cmd = [
        "gcloud", "compute", "ssh", name,
        "--zone", zone,
        "--project", project,
        "--command", command,
        "--quiet",
        "--",
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=30",
        "-o", "BatchMode=yes",
    ]
    if extra_flags:
        cmd.extend(extra_flags)
    return cmd


def login_ssh_cmd(name: str, command: str, project: str, zone: str) -> list[str]:
    """SSH command that prepends openclaw's install location to PATH."""
    wrapped = f"export PATH=$HOME/.npm-global/bin:$PATH; {command}"
    return ssh_cmd(name, wrapped, project, zone)


def scp_cmd(local_path: str, name: str, remote_path: str, project: str, zone: str) -> list[str]:
    """Build gcloud compute scp command for recursive directory copy."""
    return [
        "gcloud", "compute", "scp",
        "--recurse",
        local_path,
        f"{name}:{remote_path}",
        "--zone", zone,
        "--project", project,
        "--quiet",
    ]


# ─── Phase 1 — Provision ─────────────────────────────────────────────────────


async def phase_provision(count: int, env: dict) -> None:
    project = env.get("GCP_PROJECT", GCP_PROJECT)
    zone = env.get("GCP_ZONE", GCP_ZONE)

    console.print(f"\n[bold]Phase 1: Provision {count} VMs[/bold]")

    async def create_vm(idx: int) -> tuple[int, str, str]:
        name = vm_name(idx)
        cmd = [
            "gcloud", "compute", "instances", "create", name,
            "--zone", zone,
            "--project", project,
            "--machine-type", "e2-standard-2",
            "--image-family", "debian-12",
            "--image-project", "debian-cloud",
            "--tags", "http-server",
            "--scopes", "cloud-platform",
            "--quiet",
        ]
        return await run_async(cmd, label=name, timeout=300)

    results = await asyncio.gather(*[create_vm(i) for i in range(1, count + 1)])

    failed = [vm_name(i + 1) for i, (rc, _, _) in enumerate(results) if rc != 0]
    if failed:
        console.print(f"[red]VM creation failed: {failed}[/red]")
        sys.exit(1)

    console.print(f"\n[bold]Waiting for SSH on {count} VMs...[/bold]")
    await asyncio.gather(*[_wait_for_ssh(vm_name(i), project, zone) for i in range(1, count + 1)])
    console.print("[green]All VMs SSH-reachable.[/green]")


async def _wait_for_ssh(name: str, project: str, zone: str, timeout: int = 300) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        cmd = ssh_cmd(name, "echo ok", project, zone)
        rc, out, _ = await run_async(cmd, label=f"ssh-check:{name}", timeout=30)
        if rc == 0 and "ok" in out:
            console.print(f"  [green]{name} SSH ready[/green]")
            return
        await asyncio.sleep(10)
    console.print(f"  [red]{name} SSH timeout after {timeout}s[/red]")
    sys.exit(1)


# ─── Phase 2 — Bootstrap ─────────────────────────────────────────────────────


async def phase_bootstrap(count: int, env: dict) -> None:
    project = env.get("GCP_PROJECT", GCP_PROJECT)
    zone = env.get("GCP_ZONE", GCP_ZONE)
    openai_key = env.get("OPENAI_API_KEY", "")

    if not openai_key:
        console.print("[red]Error: OPENAI_API_KEY not set in .env[/red]")
        sys.exit(1)

    console.print(f"\n[bold]Phase 2: Bootstrap {count} VMs (install OpenClaw)[/bold]")

    # Collect web search API keys to deploy to each VM
    brave_key = env.get("BRAVE_API_KEY", "")
    jina_key = env.get("JINA_API_KEY", "")
    tavily_key = env.get("TAVILY_API_KEY", "")

    async def bootstrap_vm(idx: int) -> None:
        name = vm_name(idx)
        console.print(f"  Bootstrapping {name}...")

        # Step 1: Install OpenClaw
        rc, _, err = await run_async(
            ssh_cmd(
                name,
                "curl -fsSL https://openclaw.ai/install.sh | bash -s -- --no-prompt --no-onboard",
                project, zone,
            ),
            label=f"install:{name}",
            timeout=600,
        )
        if rc != 0:
            console.print(f"  [red]OpenClaw install failed on {name}: {err[:200]}[/red]")
            sys.exit(1)

        # Step 2: Onboard with OpenAI key + Brave search tool
        env_exports = f"export OPENAI_API_KEY={openai_key}; "
        if brave_key:
            env_exports += f"export BRAVE_API_KEY={brave_key}; "
        if jina_key:
            env_exports += f"export JINA_API_KEY={jina_key}; "
        if tavily_key:
            env_exports += f"export TAVILY_API_KEY={tavily_key}; "

        onboard_cmd = (
            env_exports +
            "export OPENCLAW_NO_PROMPT=1; "
            "openclaw onboard --non-interactive --accept-risk --install-daemon --auth-choice openai-api-key"
        )
        rc, _, err = await run_async(
            login_ssh_cmd(name, onboard_cmd, project, zone),
            label=f"onboard:{name}",
            timeout=120,
        )
        if rc != 0:
            console.print(f"  [yellow]Onboard rc={rc} on {name} — may already be configured[/yellow]")
            console.print(f"  [dim]{err[:200]}[/dim]")

        # Step 3: Deploy web search API keys to VM environment
        if brave_key or jina_key or tavily_key:
            env_lines = []
            if brave_key:
                env_lines.append(f"BRAVE_API_KEY={brave_key}")
            if jina_key:
                env_lines.append(f"JINA_API_KEY={jina_key}")
            if tavily_key:
                env_lines.append(f"TAVILY_API_KEY={tavily_key}")
            env_content = "\\n".join(env_lines)
            deploy_env_cmd = (
                f'printf "{env_content}\\n" >> ~/.openclaw/.env; '
                "sort -u -t= -k1,1 ~/.openclaw/.env -o ~/.openclaw/.env"
            )
            await run_async(
                ssh_cmd(name, deploy_env_cmd, project, zone),
                label=f"web-keys:{name}",
                timeout=30,
            )

    await asyncio.gather(*[bootstrap_vm(i) for i in range(1, count + 1)])
    console.print("[green]Bootstrap complete on all VMs.[/green]")


# ─── Phase 3 — Install MemClaw Plugin ────────────────────────────────────────


async def phase_plugin(count: int, env: dict) -> None:
    project = env.get("GCP_PROJECT", GCP_PROJECT)
    zone = env.get("GCP_ZONE", GCP_ZONE)
    api_key = env.get("MEMCLAW_API_KEY", "")

    if not api_key:
        console.print("[red]Error: MEMCLAW_API_KEY not set in .env[/red]")
        sys.exit(1)

    console.print(f"\n[bold]Phase 3: Install MemClaw plugin on {count} VMs[/bold]")

    async def install_on_vm(vf: dict) -> None:
        idx = vf["vm_index"]
        if idx > count:
            return
        name = vm_name(idx)
        fleet_id = vf["fleet_id"]

        install_url = (
            f"https://memclaw.net/api/install-plugin"
            f"?api_key={api_key}&fleet_id={fleet_id}&api_url={MEMCLAW_API_URL}"
        )
        install_plugin_cmd = f"curl -s '{install_url}' | bash"

        console.print(f"  Installing MemClaw on {name} (fleet: {fleet_id})...")
        rc, _, err = await run_async(
            ssh_cmd(name, install_plugin_cmd, project, zone),
            label=f"plugin:{name}",
            timeout=300,
        )
        if rc != 0:
            console.print(f"  [red]Plugin install failed on {name}: {err[:200]}[/red]")
            sys.exit(1)

        # Restart via systemd to avoid openclaw CLI hanging on post-restart health check
        systemd_restart = (
            "systemctl --user restart openclaw-gateway.service 2>/dev/null || "
            f"export PATH=$HOME/.npm-global/bin:$PATH; "
            "timeout 15 openclaw gateway restart || true"
        )
        rc, _, err = await run_async(
            ssh_cmd(name, systemd_restart, project, zone),
            label=f"restart:{name}",
            timeout=30,
        )
        if rc != 0:
            console.print(f"  [red]Plugin install failed on {name}: {err[:200]}[/red]")
            sys.exit(1)

        # Verify node appears in fleet within 60s
        await _verify_node_online(name, fleet_id, api_key)

    for vf in _VM_FLEETS:
        await install_on_vm(vf)

    console.print("[green]MemClaw plugin installed on all VMs.[/green]")


async def _verify_node_online(vm: str, fleet_id: str, api_key: str, timeout: int = 60) -> None:
    """Poll /api/fleet/nodes until this VM's node appears as online."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{MEMCLAW_API_URL}/api/fleet/nodes",
                    params={"tenant_id": config.TENANT},
                    headers={"X-API-Key": api_key},
                )
            if resp.status_code == 200:
                nodes = resp.json()
                node_names = [n.get("node_name", "") for n in nodes]
                if vm in node_names:
                    console.print(f"  [green]{vm} node online in Fleet UI[/green]")
                    return
        except Exception as exc:
            console.print(f"  [dim]Fleet check error: {exc}[/dim]")
        await asyncio.sleep(10)
    console.print(f"  [yellow]Warning: {vm} not visible in Fleet UI after {timeout}s[/yellow]")


# ─── Phase 4 — Provision Agent Workspaces ────────────────────────────────────


async def phase_agents(count: int, env: dict) -> None:
    project = env.get("GCP_PROJECT", GCP_PROJECT)
    zone = env.get("GCP_ZONE", GCP_ZONE)

    console.print(f"\n[bold]Phase 4: Provision agent workspaces on {count} VMs[/bold]")

    async def provision_vm(vf: dict) -> None:
        idx = vf["vm_index"]
        if idx > count:
            return
        name = vm_name(idx)
        fleet_id = vf["fleet_id"]
        agent_ids = vf["agents"]

        console.print(f"  Provisioning {len(agent_ids)} agents on {name} ({fleet_id})...")

        with tempfile.TemporaryDirectory() as tmpdir:
            ws_root = Path(tmpdir) / "workspaces"
            ws_root.mkdir()

            for agent_id in agent_ids:
                ws_dir = ws_root / agent_id
                ws_dir.mkdir()
                files = build_workspace_files(agent_id, fleet_id, config.TENANT)
                for fname, content in files.items():
                    (ws_dir / fname).write_text(content, encoding="utf-8")

            # Create remote workspaces directory
            rc, _, err = await run_async(
                ssh_cmd(name, "mkdir -p ~/.openclaw/workspaces", project, zone),
                label=f"mkdir:{name}",
                timeout=30,
            )
            if rc != 0:
                console.print(f"  [red]mkdir failed on {name}: {err}[/red]")
                sys.exit(1)

            # SCP workspaces directory to VM
            rc, _, err = await run_async(
                scp_cmd(str(ws_root), name, "~/.openclaw/", project, zone),
                label=f"scp:{name}",
                timeout=120,
            )
            if rc != 0:
                console.print(f"  [red]SCP failed for {name}: {err[:200]}[/red]")
                sys.exit(1)

        console.print(f"  [green]{name}: {len(agent_ids)} workspaces deployed[/green]")

        # Restart gateway so it picks up the new workspace files
        systemd_restart = (
            "systemctl --user restart openclaw-gateway.service 2>/dev/null || "
            "export PATH=$HOME/.npm-global/bin:$PATH; "
            "timeout 15 openclaw gateway restart || true"
        )
        await run_async(
            ssh_cmd(name, systemd_restart, project, zone),
            label=f"restart:{name}",
            timeout=30,
        )

        # Register each agent individually
        registered = 0
        for agent_id in agent_ids:
            add_cmd = (
                f"timeout 20 openclaw agents add {agent_id} --non-interactive "
                f"--workspace ~/.openclaw/workspaces/{agent_id} 2>/dev/null || true"
            )
            rc, out, _ = await run_async(
                login_ssh_cmd(name, add_cmd, project, zone),
                label=f"add:{agent_id}@{name}",
                timeout=30,
            )
            registered += 1
        console.print(f"  [green]{name}: {registered}/{len(agent_ids)} agents registered[/green]")

    await asyncio.gather(*[provision_vm(vf) for vf in _VM_FLEETS])
    console.print("[green]Agent workspaces provisioned on all VMs.[/green]")


# ─── Phase 5 — Run Tasks (3-wave delegation) ────────────────────────────────


async def phase_tasks(count: int, env: dict) -> None:
    project = env.get("GCP_PROJECT", GCP_PROJECT)
    zone = env.get("GCP_ZONE", GCP_ZONE)
    api_key = env.get("MEMCLAW_API_KEY", "")
    admin_key = env.get("MEMCLAW_ADMIN_KEY", "")

    console.print(f"\n[bold]Phase 5: Run agent tasks (3-wave delegation)[/bold]")

    async def run_agent_task(name: str, agent_id: str, task_prompt: str) -> None:
        console.print(f"  Running task: {name} / {agent_id}")
        inner = f"export TASK_PROMPT={shlex.quote(task_prompt)}; timeout 1800 openclaw agent --agent {agent_id} --message \"$TASK_PROMPT\" --timeout 1800"
        rc, _, err = await run_async(
            login_ssh_cmd(name, inner, project, zone),
            label=f"{agent_id}@{name}",
            timeout=1860,
        )
        if rc != 0:
            console.print(f"  [yellow]Task warning for {agent_id}@{name}: {err[:150]}[/yellow]")

    # Fetch which (fleet, agent) pairs already have memories — skip them
    already_wrote: set[tuple[str, str]] = set()
    if api_key:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{MEMCLAW_API_URL}/api/memories",
                    params={"tenant_id": config.TENANT, "limit": 1000},
                    headers={"X-API-Key": api_key},
                )
            if resp.status_code == 200:
                mems = resp.json() if isinstance(resp.json(), list) else []
                already_wrote = {(m.get("fleet_id"), m.get("agent_id")) for m in mems}
                console.print(f"  [dim]{len(already_wrote)} (fleet, agent) pairs already have memories — skipping[/dim]")
        except Exception:
            pass

    # ── Wave 1: NEXUS writes delegation task memories to target fleets ────
    nexus_vm_entry = next((vf for vf in _VM_FLEETS if "nexus" in vf["agents"]), None)
    if nexus_vm_entry and nexus_vm_entry["vm_index"] <= count:
        nexus_vm = vm_name(nexus_vm_entry["vm_index"])
        nexus_fleet = nexus_vm_entry["fleet_id"]

        if (nexus_fleet, "nexus") not in already_wrote:
            console.print("\n  [bold]Wave 1: NEXUS bootstrap + delegation[/bold]")

            # Bootstrap NEXUS
            bootstrap_prompt = (
                "Store this initialization memory: "
                "NEXUS master orchestrator is online and initializing cross-fleet coordination for v2 "
                "(10 VMs, 50 agents). Delegation wave beginning."
            )
            await run_agent_task(nexus_vm, "nexus", bootstrap_prompt)

            # Promote nexus trust to level 3 (cross-fleet reads AND writes)
            auth_key = admin_key or api_key
            if auth_key:
                console.print("  [dim]Promoting NEXUS to trust level 3 (cross-fleet read+write)...[/dim]")
                try:
                    async with httpx.AsyncClient(timeout=15) as client:
                        resp = await client.patch(
                            f"{MEMCLAW_API_URL}/api/agents/nexus/trust",
                            params={"tenant_id": config.TENANT},
                            json={"trust_level": 3},
                            headers={"X-API-Key": auth_key},
                        )
                    if resp.status_code == 200:
                        console.print("  [green]NEXUS trust level promoted to 3[/green]")
                    else:
                        console.print(
                            f"  [yellow]Trust promotion returned {resp.status_code}: {resp.text[:100]}[/yellow]"
                        )
                except Exception as exc:
                    console.print(f"  [yellow]Trust promotion error: {exc}[/yellow]")

            # NEXUS writes delegation task memories to its own fleet
            # (other agents see them via cross-fleet recall)
            delegation_prompt = (
                "Store the following delegation task memories in YOUR fleet (use your own fleet_id). "
                "Other fleets will see them via cross-fleet recall. "
                "Store each as a separate task memory with 'NEXUS delegation' prefix: "
                "1. 'NEXUS delegation to Engineering (fleet-02): Produce architecture review for payment gateway. Coordinate ADRs and system design.' "
                "2. 'NEXUS delegation to Research (fleet-04): Research current AI infrastructure trends using web search tools. Store findings with source URLs.' "
                "3. 'NEXUS delegation to Finance (fleet-05): Prepare Q2 budget forecast with detailed assumptions and line items.' "
                "4. 'NEXUS delegation to Intelligence (fleet-09): Analyze competitor pricing via web search. Focus on Stripe, Square, Adyen.' "
            )
            await run_agent_task(nexus_vm, "nexus", delegation_prompt)
            already_wrote.add((nexus_fleet, "nexus"))
        else:
            console.print("  [dim]NEXUS already has memories — skipping Wave 1[/dim]")

    # ── Wave 2: All fleet agents run in parallel ─────────────────────────
    console.print("\n  [bold]Wave 2: All fleet agents run in parallel[/bold]")
    wave2_coros = []
    for vf in _VM_FLEETS:
        idx = vf["vm_index"]
        if idx > count:
            continue
        name = vm_name(idx)
        for agent_id in vf["agents"]:
            if agent_id == "nexus":
                continue
            if (vf["fleet_id"], agent_id) in already_wrote:
                console.print(f"  [dim]Skip {agent_id}@{vf['fleet_id']} — already has memories[/dim]")
                continue
            task_prompt = TASKS.get(agent_id, "Recall your context and summarize your current status.")
            wave2_coros.append(run_agent_task(name, agent_id, task_prompt))

    if wave2_coros:
        await asyncio.gather(*wave2_coros)
    else:
        console.print("  [dim]All wave-2 agents already wrote memories.[/dim]")

    # ── Wave 3: NEXUS cross-fleet synthesis ──────────────────────────────
    if nexus_vm_entry and nexus_vm_entry["vm_index"] <= count:
        console.print("\n  [bold]Wave 3: NEXUS cross-fleet synthesis[/bold]")
        synthesis_prompt = (
            "Recall ALL work across ALL fleets (omit fleet_id for org-wide recall). "
            "Synthesize a comprehensive cross-fleet status report covering: "
            "1. Engineering progress (fleet-02) "
            "2. Research findings (fleet-04) "
            "3. Financial projections (fleet-05) "
            "4. Competitive intelligence (fleet-09) "
            "5. Any blockers or cross-fleet dependencies. "
            "Store the synthesis as a plan memory."
        )
        await run_agent_task(vm_name(nexus_vm_entry["vm_index"]), "nexus", synthesis_prompt)

    console.print("[green]All agent tasks complete.[/green]")


# ─── Phase 6 — Verify ────────────────────────────────────────────────────────


async def phase_verify(count: int, env: dict) -> None:
    from verify import run_verification

    console.print(f"\n[bold]Phase 6: Verify memory storage[/bold]")

    api_key = env.get("MEMCLAW_API_KEY", "")
    admin_key = env.get("MEMCLAW_ADMIN_KEY", "")

    if not api_key:
        console.print("[red]Error: MEMCLAW_API_KEY not set — cannot verify[/red]")
        sys.exit(1)

    passed, failed = await run_verification(
        url=MEMCLAW_API_URL,
        api_key=api_key,
        admin_key=admin_key,
        tenant_id=config.TENANT,
        vm_count=count,
    )

    if failed:
        console.print(f"\n[red]Verification: {passed} passed, {failed} failed[/red]")
        sys.exit(1)
    else:
        console.print(f"\n[green]Verification: {passed} passed, 0 failed[/green]")


# ─── Teardown ─────────────────────────────────────────────────────────────────


async def phase_teardown(count: int, env: dict) -> None:
    project = env.get("GCP_PROJECT", GCP_PROJECT)
    zone = env.get("GCP_ZONE", GCP_ZONE)

    names = [vm_name(i) for i in range(1, count + 1)]
    console.print(f"\n[bold]Teardown: Deleting {len(names)} VMs[/bold]")
    console.print(f"  VMs: {', '.join(names)}")

    confirm = input("  Type 'yes' to confirm deletion: ").strip()
    if confirm.lower() != "yes":
        console.print("  Aborted.")
        return

    cmd = [
        "gcloud", "compute", "instances", "delete",
        *names,
        "--zone", zone,
        "--project", project,
        "--quiet",
    ]
    rc, _, err = await run_async(cmd, label="teardown", timeout=300)
    if rc != 0:
        console.print(f"[red]Teardown failed: {err[:200]}[/red]")
        sys.exit(1)
    console.print("[green]VMs deleted.[/green]")


# ─── Interactive Mode ─────────────────────────────────────────────────────────


def interactive_menu(env: dict, count: int) -> None:
    """Interactive menu: UI tunnel, custom task runner, or full E2E test."""
    from rich.prompt import Prompt, Confirm

    project = env.get("GCP_PROJECT", GCP_PROJECT)
    zone = env.get("GCP_ZONE", GCP_ZONE)

    console.print("\n[bold cyan]OpenClaw Fleet Tester v2 — Interactive Mode[/bold cyan]")
    console.print(f"  VMs: {', '.join(vm_name(i) for i in range(1, count + 1))}\n")

    choices = {
        "1": "Connect to OpenClaw UI (SSH tunnel to gateway)",
        "2": "Run a single agent task",
        "3": "Run all tasks (skip agents with existing memories)",
        "4": "Run full end-to-end test",
        "5": "Run verification only",
        "6": "Teardown VMs",
        "q": "Quit",
    }

    for k, v in choices.items():
        console.print(f"  [{k}] {v}")

    choice = Prompt.ask("\nChoice", choices=list(choices.keys()), default="q")

    if choice == "1":
        _interactive_ui_tunnel(env, count, project, zone)
    elif choice == "2":
        _interactive_single_task(env, count, project, zone)
    elif choice == "3":
        asyncio.run(phase_tasks(count, env))
    elif choice == "4":
        async def run_all():
            for phase in PHASES_ORDERED[:-1]:  # skip teardown
                await phase_fns_map()[phase](count, env)
        asyncio.run(run_all())
    elif choice == "5":
        asyncio.run(phase_verify(count, env))
    elif choice == "6":
        asyncio.run(phase_teardown(count, env))


def _interactive_ui_tunnel(env: dict, count: int, project: str, zone: str) -> None:
    """Open SSH tunnel to the OpenClaw gateway on a chosen VM."""
    from rich.prompt import Prompt

    vm_choices = {str(i): vm_name(i) for i in range(1, count + 1)}
    console.print("\n  Available VMs:")
    for k, v in vm_choices.items():
        console.print(f"    [{k}] {v}")

    idx = Prompt.ask("  VM number", choices=list(vm_choices.keys()), default="1")
    name = vm_choices[idx]
    local_port = int(Prompt.ask("  Local port", default="18789"))
    remote_port = 18789

    console.print(f"\n  Opening SSH tunnel: localhost:{local_port} → {name}:{remote_port}")
    console.print(f"  OpenClaw gateway will be available at [bold]ws://localhost:{local_port}[/bold]")
    console.print(f"  Run [bold]openclaw tui[/bold] in another terminal to connect.")
    console.print(f"  Press Ctrl+C to close the tunnel.\n")

    tunnel_cmd = [
        "gcloud", "compute", "ssh", name,
        "--zone", zone, "--project", project,
        "--quiet",
        "--",
        "-N",
        "-L", f"{local_port}:localhost:{remote_port}",
        "-o", "StrictHostKeyChecking=no",
        "-o", "ServerAliveInterval=30",
    ]
    try:
        import subprocess
        subprocess.run(tunnel_cmd)
    except KeyboardInterrupt:
        console.print("\n  Tunnel closed.")


def _interactive_single_task(env: dict, count: int, project: str, zone: str) -> None:
    """Let the user pick a VM, agent, and optionally override the task prompt."""
    from rich.prompt import Prompt

    # Build list of all (vm, fleet, agent) options
    options = []
    for vf in _VM_FLEETS:
        if vf["vm_index"] > count:
            continue
        for agent_id in vf["agents"]:
            options.append((vm_name(vf["vm_index"]), vf["fleet_id"], agent_id))

    console.print("\n  Available agents:")
    for i, (vm, fid, aid) in enumerate(options, 1):
        default_task = TASKS.get(aid, "")[:60]
        console.print(f"  [{i:2d}] {aid:<24} ({fid}) — {default_task}…")

    idx = int(Prompt.ask("  Agent number", default="1")) - 1
    if idx < 0 or idx >= len(options):
        console.print("[red]Invalid choice.[/red]")
        return

    vm, fid, agent_id = options[idx]
    default_prompt = TASKS.get(agent_id, "Recall your context and summarize your status.")
    task_prompt = Prompt.ask("  Task prompt (Enter to use default)", default=default_prompt)

    console.print(f"\n  Running: {agent_id} on {vm}")
    console.print(f"  Prompt: {task_prompt[:80]}…\n")

    inner = f"export TASK_PROMPT={shlex.quote(task_prompt)}; timeout 1800 openclaw agent --agent {agent_id} --message \"$TASK_PROMPT\" --timeout 1800"
    rc, out, err = asyncio.run(run_async(
        login_ssh_cmd(vm, inner, project, zone),
        label=f"{agent_id}@{vm}",
        timeout=1860,
    ))
    if rc != 0:
        console.print(f"[yellow]Warning: rc={rc}[/yellow]")
    console.print("[green]Done.[/green]")


def phase_fns_map() -> dict:
    return {
        "provision": phase_provision,
        "bootstrap": phase_bootstrap,
        "plugin": phase_plugin,
        "agents": phase_agents,
        "tasks": phase_tasks,
        "verify": phase_verify,
        "teardown": phase_teardown,
    }


# ─── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="OpenClaw Fleet Memory Test v2 Orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="\n".join([
            "Phases: " + ", ".join(PHASES_ORDERED),
            "  all       — run all phases in order",
            "  provision — create GCP VMs",
            "  bootstrap — install OpenClaw on each VM",
            "  plugin    — install MemClaw plugin per VM",
            "  agents    — provision agent workspaces",
            "  tasks     — run agent tasks (3-wave delegation)",
            "  verify    — verify memory storage",
            "  teardown  — delete GCP VMs",
        ]),
    )
    parser.add_argument(
        "--phase",
        required=False,
        choices=PHASES_ORDERED + ["all"],
        help="Phase to run (omit to launch interactive menu)",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Launch interactive menu (UI tunnel, task picker, full E2E)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help=f"Number of VMs (default: VM_COUNT from .env or {VM_COUNT_DEFAULT})",
    )
    args = parser.parse_args()

    env = load_env()

    # Auto-resolve tenant_id from the API key
    api_key = env.get("MEMCLAW_API_KEY", "")
    if api_key:
        resolved = resolve_tenant(api_key)
        if resolved:
            config.TENANT = resolved
            console.print(f"  [dim]Resolved tenant: {resolved}[/dim]")
        else:
            console.print("[yellow]Warning: could not resolve tenant from API key — using default[/yellow]")

    # Initialize VM naming based on user prefix
    user_prefix = env.get("TESTER_PREFIX", "")
    _init_naming(user_prefix)

    if args.count is not None:
        count = args.count
    else:
        count = int(env.get("VM_COUNT", str(VM_COUNT_DEFAULT)))

    if count < 1 or count > 20:
        console.print(f"[red]VM count must be 1–20, got {count}[/red]")
        sys.exit(1)

    console.print(f"\n[bold cyan]OpenClaw Fleet Memory Test v2[/bold cyan]")
    console.print(f"  Tenant:  {config.TENANT}")
    console.print(f"  Prefix:  {user_prefix or '(none)'}")
    console.print(f"  VM name: {vm_name(1)} … {vm_name(count)}")
    console.print(f"  Project: {env.get('GCP_PROJECT', GCP_PROJECT)}")
    console.print(f"  Zone:    {env.get('GCP_ZONE', GCP_ZONE)}")
    console.print(f"  VMs:     {count}")

    # Interactive mode
    if args.interactive or not args.phase:
        interactive_menu(env, count)
        return

    console.print(f"  Phase:   {args.phase}")

    phase_fns = phase_fns_map()
    phases_to_run = PHASES_ORDERED if args.phase == "all" else [args.phase]

    # For teardown-only, don't provision
    if args.phase == "teardown":
        asyncio.run(phase_teardown(count, env))
        return

    async def run_phases() -> None:
        for phase in phases_to_run:
            fn = phase_fns[phase]
            if phase == "teardown":
                await phase_teardown(count, env)
            else:
                await fn(count, env)

    asyncio.run(run_phases())
    console.print("\n[bold green]Done.[/bold green]")


if __name__ == "__main__":
    main()
