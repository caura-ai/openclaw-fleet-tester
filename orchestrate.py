#!/usr/bin/env python3
"""
OpenClaw Fleet Memory Test — Orchestrator

Usage:
    python orchestrate.py --phase all --count 3
    python orchestrate.py --phase provision --count 3
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

from config import (
    TENANT,
    VM_NAME_PREFIX,
    GCP_PROJECT,
    GCP_ZONE,
    MEMCLAW_API_URL,
    VM_COUNT_DEFAULT,
    VM_FLEETS,
    TASKS,
    build_workspace_files,
)

console = Console()

PHASES_ORDERED = ["provision", "bootstrap", "plugin", "agents", "tasks", "verify", "teardown"]

# ─── Environment ──────────────────────────────────────────────────────────────


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
    return f"{VM_NAME_PREFIX}-{index:02d}"


def fleet_for_vm(index: int) -> dict | None:
    """Return VM_FLEETS entry for a 1-based VM index, or None if out of range."""
    for vf in VM_FLEETS:
        if vf["vm_index"] == index:
            return vf
    return None


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
    """SSH command that prepends openclaw's install location to PATH.

    The openclaw installer puts the binary in ~/.npm-global/bin/ and adds it
    to ~/.bashrc. But ~/.bashrc is skipped in non-interactive SSH sessions.
    We prepend the path explicitly instead.
    """
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

    async def bootstrap_vm(idx: int) -> None:
        name = vm_name(idx)
        console.print(f"  Bootstrapping {name}...")

        # Step 1: Install OpenClaw (plain shell — curl/bash needs no special PATH)
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

        # Step 2: Onboard via login shell so openclaw is in PATH
        # OpenAI keys are alphanumeric+hyphen — safe to embed without quoting issues
        onboard_cmd = (
            f"export OPENAI_API_KEY={openai_key}; "
            "export OPENCLAW_NO_PROMPT=1; "
            "openclaw onboard --non-interactive --accept-risk --install-daemon --auth-choice openai-api-key"
        )
        rc, _, err = await run_async(
            login_ssh_cmd(name, onboard_cmd, project, zone),
            label=f"onboard:{name}",
            timeout=120,
        )
        if rc != 0:
            # Non-fatal: openclaw may already be configured or onboard may prompt differently
            console.print(f"  [yellow]Onboard rc={rc} on {name} — may already be configured[/yellow]")
            console.print(f"  [dim]{err[:200]}[/dim]")

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
        # Plugin install runs curl, which needs no special PATH
        # Gateway restart needs openclaw in PATH → login shell
        install_plugin_cmd = f"curl -s '{install_url}' | bash"
        restart_cmd = "openclaw gateway restart"

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

    for vf in VM_FLEETS:
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
                    params={"tenant_id": TENANT},
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
                files = build_workspace_files(agent_id, fleet_id, TENANT)
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
            # str(ws_root) = /tmp/XXX/workspaces  (no trailing slash)
            # → creates ~/.openclaw/workspaces/ with agent subdirs inside
            rc, _, err = await run_async(
                scp_cmd(str(ws_root), name, "~/.openclaw/", project, zone),
                label=f"scp:{name}",
                timeout=120,
            )
            if rc != 0:
                console.print(f"  [red]SCP failed for {name}: {err[:200]}[/red]")
                sys.exit(1)

        console.print(f"  [green]{name}: {len(agent_ids)} workspaces deployed[/green]")

        # Register each agent individually with a per-command timeout
        # (openclaw agents add can hang if gateway is slow to respond)
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

    await asyncio.gather(*[provision_vm(vf) for vf in VM_FLEETS])
    console.print("[green]Agent workspaces provisioned on all VMs.[/green]")


# ─── Phase 5 — Run Tasks ──────────────────────────────────────────────────────


async def phase_tasks(count: int, env: dict) -> None:
    project = env.get("GCP_PROJECT", GCP_PROJECT)
    zone = env.get("GCP_ZONE", GCP_ZONE)
    api_key = env.get("MEMCLAW_API_KEY", "")
    admin_key = env.get("MEMCLAW_ADMIN_KEY", "")

    console.print(f"\n[bold]Phase 5: Run agent tasks[/bold]")

    async def run_agent_task(name: str, agent_id: str, task_prompt: str) -> None:
        console.print(f"  Running task: {name} / {agent_id}")
        # timeout 600: kills openclaw on the remote side after 10 min so SSH closes cleanly
        # Pass prompt via env var to avoid shell quoting complexity
        inner = f"export TASK_PROMPT={shlex.quote(task_prompt)}; timeout 1800 openclaw agent --agent {agent_id} --message \"$TASK_PROMPT\""
        rc, _, err = await run_async(
            login_ssh_cmd(name, inner, project, zone),
            label=f"{agent_id}@{name}",
            timeout=1860,  # tasks can involve many LLM calls + memory writes
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
                    params={"tenant_id": TENANT, "limit": 500},
                    headers={"X-API-Key": api_key},
                )
            if resp.status_code == 200:
                mems = resp.json() if isinstance(resp.json(), list) else []
                already_wrote = {(m.get("fleet_id"), m.get("agent_id")) for m in mems}
                console.print(f"  [dim]{len(already_wrote)} (fleet, agent) pairs already have memories — skipping[/dim]")
        except Exception:
            pass

    # Step 1: Run all non-nexus agents in parallel (by fleet), skip already done
    console.print("\n  [dim]Running non-nexus agents across all fleets...[/dim]")
    non_nexus_coros = []
    for vf in VM_FLEETS:
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
            non_nexus_coros.append(run_agent_task(name, agent_id, task_prompt))

    if non_nexus_coros:
        await asyncio.gather(*non_nexus_coros)
    else:
        console.print("  [dim]All non-nexus agents already wrote memories.[/dim]")

    # Step 2: Bootstrap nexus (registers it in MemClaw on first write)
    nexus_vm_entry = next((vf for vf in VM_FLEETS if "nexus" in vf["agents"]), None)
    if nexus_vm_entry and nexus_vm_entry["vm_index"] <= count:
        nexus_vm = vm_name(nexus_vm_entry["vm_index"])
        console.print("\n  [dim]Registering NEXUS agent...[/dim]")
        bootstrap_prompt = (
            "Store this initialization memory: "
            "NEXUS master orchestrator is online and initializing cross-fleet coordination."
        )
        await run_agent_task(nexus_vm, "nexus", bootstrap_prompt)

        # Step 3: Promote nexus trust to level 2 (cross-fleet reads)
        auth_key = admin_key or api_key
        if auth_key:
            console.print("  [dim]Promoting NEXUS to trust level 2 (cross-fleet)...[/dim]")
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.patch(
                        f"{MEMCLAW_API_URL}/api/agents/nexus/trust",
                        params={"tenant_id": TENANT},
                        json={"trust_level": 2},
                        headers={"X-API-Key": auth_key},
                    )
                if resp.status_code == 200:
                    console.print("  [green]NEXUS trust level promoted to 2[/green]")
                else:
                    console.print(
                        f"  [yellow]Trust promotion returned {resp.status_code}: {resp.text[:100]}[/yellow]"
                    )
            except Exception as exc:
                console.print(f"  [yellow]Trust promotion error: {exc}[/yellow]")
        else:
            console.print("  [yellow]No API key available — skipping NEXUS trust promotion[/yellow]")

        # Step 4: Run nexus's cross-fleet task
        console.print("\n  [dim]Running NEXUS cross-fleet recall task...[/dim]")
        await run_agent_task(nexus_vm, "nexus", TASKS["nexus"])

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
        tenant_id=TENANT,
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


# ─── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="OpenClaw Fleet Memory Test Orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="\n".join([
            "Phases: " + ", ".join(PHASES_ORDERED),
            "  all       — run all phases in order",
            "  provision — create GCP VMs",
            "  bootstrap — install OpenClaw on each VM",
            "  plugin    — install MemClaw plugin per VM",
            "  agents    — provision agent workspaces",
            "  tasks     — run agent tasks",
            "  verify    — verify memory storage",
            "  teardown  — delete GCP VMs",
        ]),
    )
    parser.add_argument(
        "--phase",
        required=True,
        choices=PHASES_ORDERED + ["all"],
        help="Phase to run",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help=f"Number of VMs (default: VM_COUNT from .env or {VM_COUNT_DEFAULT})",
    )
    args = parser.parse_args()

    env = load_env()

    if args.count is not None:
        count = args.count
    else:
        count = int(env.get("VM_COUNT", str(VM_COUNT_DEFAULT)))

    if count < 1 or count > 10:
        console.print(f"[red]VM count must be 1–10, got {count}[/red]")
        sys.exit(1)

    console.print(f"\n[bold cyan]OpenClaw Fleet Memory Test[/bold cyan]")
    console.print(f"  Tenant:  {TENANT}")
    console.print(f"  Project: {env.get('GCP_PROJECT', GCP_PROJECT)}")
    console.print(f"  Zone:    {env.get('GCP_ZONE', GCP_ZONE)}")
    console.print(f"  VMs:     {count}")
    console.print(f"  Phase:   {args.phase}")

    phases_to_run = PHASES_ORDERED if args.phase == "all" else [args.phase]

    # For teardown-only, don't provision
    if args.phase == "teardown":
        asyncio.run(phase_teardown(count, env))
        return

    phase_fns = {
        "provision": phase_provision,
        "bootstrap": phase_bootstrap,
        "plugin": phase_plugin,
        "agents": phase_agents,
        "tasks": phase_tasks,
        "verify": phase_verify,
        "teardown": phase_teardown,
    }

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
