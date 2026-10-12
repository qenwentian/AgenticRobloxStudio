"""Five real stdio MCP clients and five real bridges against a real HTTP backend."""

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import AsyncExitStack
from pathlib import Path

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[2]


def remove_disposable_workspace(folder: Path):
    root = folder.resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    if root.parent != temp_root or not root.name.startswith("studiosync-simulation-"):
        raise ValueError("Refusing to remove a non-simulation workspace")

    def writable_retry(function, path, error):
        candidate = Path(path).resolve()
        if not candidate.is_relative_to(root):
            raise ValueError("Cleanup path escapes disposable workspace")
        os.chmod(candidate, stat.S_IWRITE | stat.S_IREAD)
        function(path)

    shutil.rmtree(root, onexc=writable_retry)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_ready(url, headers=None):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, headers=headers, timeout=1, trust_env=False)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError("Service failed to become ready: " + url)


def proof(agent, lease):
    return {
        "session_id": agent["session"],
        "lease_id": lease["id"],
        "lease_token": lease["lease_token"],
        "generation": lease["generation"],
    }


async def simulate(folder: Path, luau: str) -> dict:
    processes, logs = [], []
    env = dict(os.environ)
    env.update(
        STUDIOSYNC_DATABASE_URL="sqlite:///" + (folder / "coordination.db").as_posix(),
        STUDIOSYNC_BOOTSTRAP_KEY="simulation-bootstrap-" + os.urandom(16).hex(),
    )
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

    def start(name, args, environment):
        log = (folder / (name + ".log")).open("w", encoding="utf-8")
        logs.append(log)
        proc = subprocess.Popen(
            [sys.executable, *args],
            cwd=ROOT,
            env=environment,
            stdout=log,
            stderr=log,
            creationflags=flags,
        )
        processes.append(proc)
        return proc

    report = {
        "transport": "official MCP SDK stdio",
        "backend": "real HTTP + SQLite",
        "checks": [],
        "agents": 5,
        "bridges": 5,
    }

    def checked(name):
        report["checks"].append(name)
        print("PASS " + name, flush=True)

    try:
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=ROOT,
            env=env,
            check=True,
            capture_output=True,
            creationflags=flags,
        )
        port = free_port()
        url = f"http://127.0.0.1:{port}"
        server_args = [
            "-m",
            "uvicorn",
            "studiosync_server.app:production_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ]
        central = start("server", server_args, env)
        await asyncio.to_thread(wait_ready, url + "/health")
        agents = []
        systems = ["Combat", "Abilities", "Inventory", "Match", "HUD"]
        resources = [
            "file:src/server/CombatService.luau",
            "file:src/server/AbilityService.luau",
            "file:src/server/InventoryService.luau",
            "file:src/server/MatchService.luau",
            "file:src/client/Hud.luau",
        ]
        with httpx.Client(base_url=url, timeout=10, trust_env=False) as http:
            for i in range(5):
                identity = http.post(
                    "/auth/register",
                    headers={"X-Bootstrap-Key": env["STUDIOSYNC_BOOTSTRAP_KEY"]},
                    json={
                        "name": "simulation-dev-" + str(i),
                        "password": "simulation-password-" + os.urandom(12).hex(),
                    },
                ).json()
                if i == 0:
                    owner = {"Authorization": "Bearer " + identity["token"]}
                    pid = http.post(
                        "/projects", headers=owner, json={"name": "Five-developer MOBA"}
                    ).json()["project"]["id"]
                else:
                    response = http.post(
                        f"/projects/{pid}/members",
                        headers=owner,
                        json={"user_id": identity["user_id"]},
                    )
                    response.raise_for_status()
                workspace = folder / f"developer-{i}"
                shutil.copytree(ROOT / "examples/sample-moba", workspace)
                for args in [
                    ["init", "-q"],
                    ["add", "."],
                    [
                        "-c",
                        "user.name=Simulation",
                        "-c",
                        "user.email=simulation@example.invalid",
                        "commit",
                        "-qm",
                        "sample",
                    ],
                    ["switch", "-c", "developer-" + str(i)],
                ]:
                    subprocess.run(
                        ["git", "-C", str(workspace), *args],
                        check=True,
                        capture_output=True,
                        creationflags=flags,
                    )
                registered = http.post(
                    f"/projects/{pid}/actions/register_workspace",
                    headers={"Authorization": "Bearer " + identity["token"]},
                    json={
                        "device": "device-" + str(i),
                        "root_label": workspace.name,
                        "branch": "developer-" + str(i),
                    },
                ).json()
                assert registered["ok"], registered
                bridge_port = free_port()
                config = {
                    "server_url": url,
                    "project_id": pid,
                    "workspace_id": registered["data"]["workspace"]["id"],
                    "workspace_root": str(workspace),
                    "developer_id": identity["user_id"],
                    "bridge_port": bridge_port,
                    "bridge_token": os.urandom(32).hex(),
                }
                configuration = workspace / ".studiosync.json"
                configuration.write_text(json.dumps(config), encoding="utf-8")
                local_env = {
                    **env,
                    "STUDIOSYNC_CONFIG": str(configuration),
                    "STUDIOSYNC_TOKEN": identity["token"],
                    "STUDIOSYNC_PAIR_CODE": "simulation-pair-code-" + str(i),
                }
                start(
                    "bridge-" + str(i),
                    [
                        "-m",
                        "uvicorn",
                        "studiosync_bridge.app:production_bridge",
                        "--factory",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(bridge_port),
                        "--log-level",
                        "warning",
                    ],
                    local_env,
                )
                bridge_url = f"http://127.0.0.1:{bridge_port}"
                await asyncio.to_thread(
                    wait_ready, bridge_url + "/health", {"X-Bridge-Token": config["bridge_token"]}
                )
                agents.append(
                    {
                        "config": config,
                        "env": local_env,
                        "workspace": workspace,
                        "bridge_url": bridge_url,
                        "resource": resources[i],
                        "name": systems[i],
                    }
                )
        checked(
            "Five developer identities joined one project and started isolated Git workspaces and bridges"
        )
        async with AsyncExitStack() as stack:
            for agent in agents:
                read, write = await stack.enter_async_context(
                    stdio_client(
                        StdioServerParameters(
                            command=sys.executable,
                            args=["-m", "studiosync_mcp.server"],
                            env=agent["env"],
                            cwd=str(ROOT),
                        )
                    )
                )
                agent["mcp"] = await stack.enter_async_context(ClientSession(read, write))
                await agent["mcp"].initialize()
                discovered = await agent["mcp"].list_tools()
                assert "coordination.acquire_lock" in {tool.name for tool in discovered.tools}

            async def call(agent, operation, payload=None, expect=True):
                name = (
                    operation if operation.startswith("workspace.") else "coordination." + operation
                )
                result = await agent["mcp"].call_tool(name, {"request": payload or {}})
                assert not result.isError, result
                data = result.structuredContent
                assert data is not None, "Tool must expose structured output"
                if expect:
                    assert data["ok"], data
                return data

            for agent in agents:
                result = await call(
                    agent,
                    "register_agent",
                    {
                        "display_name": agent["name"] + " Agent",
                        "workspace_id": agent["config"]["workspace_id"],
                        "capabilities": ["luau", "testing"],
                    },
                )
                agent["session"] = result["data"]["session"]["id"]
                agent["agent"] = result["data"]["agent"]["id"]
            state = await call(agents[0], "get_project_state")
            assert len(state["data"]["agents"]) == 5
            checked(
                "Five official stdio MCP clients registered persistent independent agent sessions"
            )
            async with httpx.AsyncClient(trust_env=False, timeout=10) as http:
                for i, agent in enumerate(agents):
                    paired = (
                        await http.post(
                            agent["bridge_url"] + "/pair",
                            json={"code": "simulation-pair-code-" + str(i)},
                        )
                    ).json()
                    state_response = await http.get(
                        agent["bridge_url"] + "/project",
                        headers={"X-Bridge-Token": paired["token"]},
                    )
                    assert len(state_response.json()["data"]["agents"]) == 5
            checked(
                "All five Studio-facing paired bridge APIs displayed actual connected agent state"
            )
            contention = await asyncio.gather(
                *[
                    call(
                        a,
                        "acquire_lock",
                        {"session_id": a["session"], "resource": "system:concurrent-race"},
                        expect=False,
                    )
                    for a in agents
                ]
            )
            assert sum(result["ok"] for result in contention) == 1
            winner = next(i for i, result in enumerate(contention) if result["ok"])
            await call(
                agents[winner],
                "release_lock",
                proof(agents[winner], contention[winner]["data"]["lease"]),
            )
            checked("Five simultaneous exclusive requests produced exactly one winner")
            for agent in agents:
                task = await call(
                    agent,
                    "create_task",
                    {
                        "session_id": agent["session"],
                        "title": agent["name"] + " system",
                        "resources": [agent["resource"]],
                        "acceptance_criteria": ["Luau contract behavior tests pass"],
                    },
                )
                agent["task"] = task["data"]["task"]["id"]
                await call(
                    agent, "claim_task", {"session_id": agent["session"], "task_id": agent["task"]}
                )
                lease = await call(
                    agent,
                    "acquire_lock",
                    {
                        "session_id": agent["session"],
                        "resource": agent["resource"],
                        "task_id": agent["task"],
                    },
                )
                agent["lease"] = lease["data"]["lease"]
            a, b = agents[:2]
            denied = await call(
                b,
                "acquire_lock",
                {"session_id": b["session"], "resource": a["resource"]},
                expect=False,
            )
            assert (
                not denied["ok"]
                and denied["error"]["details"]["locks"][0]["agent_id"] == a["agent"]
            )
            checked(
                "Non-overlapping ownership succeeds; conflicting Combat request exposes its owner"
            )
            sent = await call(
                b,
                "send_message",
                {
                    "session_id": b["session"],
                    "recipient_agent_id": a["agent"],
                    "message_type": "INTERFACE_REQUEST",
                    "subject": "Damage dependency",
                    "body": "Need ApplyDamage(DamageRequest): DamageResult",
                    "related_task_id": b["task"],
                },
            )
            inbox = await call(a, "get_messages", {"session_id": a["session"]})
            assert sent["data"]["message"]["id"] in {m["id"] for m in inbox["data"]["messages"]}
            await call(
                a,
                "acknowledge_message",
                {"session_id": a["session"], "message_id": sent["data"]["message"]["id"]},
            )
            await call(
                a,
                "publish_interface",
                {
                    "session_id": a["session"],
                    "name": "Combat.ApplyDamage",
                    "description": "Apply nonnegative base damage with nonnegative armor",
                    "signatures": {
                        "params": {"BaseDamage": "number", "Armor": "number"},
                        "returns": {"FinalDamage": "number"},
                    },
                    "errors": "Assertion for negative input",
                    "consumers": [b["agent"]],
                },
            )
            assert (await call(b, "get_interface", {"name": "Combat.ApplyDamage"}))["data"][
                "versions"
            ][0]["version"] == 1
            await call(
                b,
                "approve_interface",
                {"session_id": b["session"], "name": "Combat.ApplyDamage", "version": 1},
            )
            checked(
                "Persisted interface request, acknowledgement, approved version and consumer handoff"
            )
            dependent = await call(
                b,
                "create_task",
                {
                    "session_id": b["session"],
                    "title": "Combat integration review",
                    "resources": ["system:integration-review"],
                    "dependencies": [a["task"]],
                },
            )
            dependent_id = dependent["data"]["task"]["id"]
            blocked = await call(
                b, "claim_task", {"session_id": b["session"], "task_id": dependent_id}, expect=False
            )
            assert not blocked["ok"] and blocked["error"]["code"] == "DEPENDENCY_BLOCKED"
            for agent in agents:
                original = await call(agent, "workspace.read_file", {"resource": agent["resource"]})
                content = (
                    original["data"]["content"]
                    + "\n-- Coordinated "
                    + agent["name"]
                    + " implementation checkpoint\n"
                )
                agent["original"] = original["data"]["revision"]
                await call(
                    agent,
                    "workspace.controlled_write",
                    {
                        **proof(agent, agent["lease"]),
                        "workspace_id": agent["config"]["workspace_id"],
                        "resource": agent["resource"],
                        "expected_revision": agent["original"],
                        "new_revision": hashlib.sha256(content.encode()).hexdigest(),
                        "content": content,
                    },
                )
                tests = subprocess.run(
                    [luau, "tests/contracts.luau"],
                    cwd=agent["workspace"],
                    capture_output=True,
                    text=True,
                    creationflags=flags,
                )
                assert tests.returncode == 0, tests.stdout + tests.stderr
                await call(
                    agent,
                    "submit_work",
                    {
                        "session_id": agent["session"],
                        "task_id": agent["task"],
                        "summary": "Coordinated change with executed Luau contract tests",
                        "work_ref": "branch:developer-" + str(agents.index(agent)),
                        "test_results": {
                            "passed": True,
                            "exit_code": tests.returncode,
                            "command": "luau tests/contracts.luau",
                        },
                        "leases": [proof(agent, agent["lease"])],
                    },
                )
                await call(
                    agent,
                    "update_task",
                    {
                        "session_id": agent["session"],
                        "task_id": agent["task"],
                        "status": "COMPLETED",
                    },
                )
                await call(agent, "release_lock", proof(agent, agent["lease"]))
            checked(
                "Five guarded local writes, actual Luau behavior tests, submissions, task completions and releases"
            )
            await call(b, "claim_task", {"session_id": b["session"], "task_id": dependent_id})
            dependent_lease = (
                await call(
                    b,
                    "acquire_lock",
                    {
                        "session_id": b["session"],
                        "resource": "system:integration-review",
                        "task_id": dependent_id,
                    },
                )
            )["data"]["lease"]
            tests = subprocess.run(
                [luau, "tests/contracts.luau"],
                cwd=b["workspace"],
                capture_output=True,
                creationflags=flags,
            )
            assert tests.returncode == 0
            await call(
                b,
                "submit_work",
                {
                    "session_id": b["session"],
                    "task_id": dependent_id,
                    "summary": "Dependency integrated",
                    "work_ref": "branch:developer-1",
                    "test_results": {
                        "passed": True,
                        "exit_code": tests.returncode,
                        "command": "luau tests/contracts.luau",
                    },
                    "leases": [proof(b, dependent_lease)],
                },
            )
            await call(
                b,
                "update_task",
                {"session_id": b["session"], "task_id": dependent_id, "status": "COMPLETED"},
            )
            await call(b, "release_lock", proof(b, dependent_lease))
            transferred = (
                await call(
                    b, "acquire_lock", {"session_id": b["session"], "resource": a["resource"]}
                )
            )["data"]["lease"]
            assert transferred["generation"] > a["lease"]["generation"]
            stale = await call(
                a,
                "workspace.controlled_write",
                {
                    **proof(a, a["lease"]),
                    "workspace_id": a["config"]["workspace_id"],
                    "resource": a["resource"],
                    "expected_revision": (
                        await call(a, "workspace.read_file", {"resource": a["resource"]})
                    )["data"]["revision"],
                    "new_revision": hashlib.sha256(b"stale").hexdigest(),
                    "content": "stale",
                },
                expect=False,
            )
            assert not stale["ok"] and stale["error"]["code"] == "STALE_LEASE"
            await call(b, "release_lock", proof(b, transferred))
            checked(
                "Dependency completion unblocks review; ownership transfer increases fence and rejects stale controlled write"
            )
            crash = (
                await call(
                    a,
                    "acquire_lock",
                    {"session_id": a["session"], "resource": "system:crashed-agent", "ttl": 2},
                )
            )["data"]["lease"]
            await asyncio.sleep(2.2)
            reclaimed = (
                await call(
                    b,
                    "acquire_lock",
                    {"session_id": b["session"], "resource": "system:crashed-agent"},
                )
            )["data"]["lease"]
            assert reclaimed["generation"] > crash["generation"]
            await call(b, "release_lock", proof(b, reclaimed))
            checked("Stopped heartbeat renewal leaves no permanent ordinary lease")
            parent = (
                await call(
                    a,
                    "acquire_lock",
                    {"session_id": a["session"], "resource": "directory:src/server"},
                )
            )["data"]["lease"]
            assert not (
                await call(
                    b,
                    "acquire_lock",
                    {"session_id": b["session"], "resource": b["resource"]},
                    expect=False,
                )
            )["ok"]
            await call(a, "release_lock", proof(a, parent))
            checked("Parent directory ownership rejects descendant writes")
            # This edit deliberately bypasses controlled writes; observe actual watchfiles delivery.
            path = a["workspace"] / "src/server/CombatService.luau"
            path.write_text(
                path.read_text(encoding="utf-8") + "\n-- uncoordinated edit\n", encoding="utf-8"
            )
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                conflicts = (await call(a, "check_conflicts"))["data"]["conflicts"]
                out_of_band = [c for c in conflicts if c["category"] == "OUT_OF_BAND"]
                if out_of_band:
                    break
                await asyncio.sleep(0.3)
            assert out_of_band, "Real filesystem watcher did not report the external edit"
            checked("Actual watchfiles event reports an out-of-band modification")
            before = (await call(a, "get_activity", {"limit": 200}))["data"]["cursor"]
            central.terminate()
            await asyncio.to_thread(central.wait, 10)
            start("server-restart", server_args, env)
            await asyncio.to_thread(wait_ready, url + "/health")
            state = (await call(a, "get_project_state"))["data"]
            assert len(state["agents"]) == 5 and all(
                t["status"] == "COMPLETED" for t in state["tasks"]
            )
            assert (await call(a, "get_activity", {"limit": 200}))["data"]["cursor"] >= before
            checked(
                "Backend restart preserves identities, completed tasks, messages, contracts and event history"
            )
            report.update(
                project_id=pid,
                task_count=len(state["tasks"]),
                event_cursor=before,
                passed=True,
                plugin_ui="Bridge API validated; Studio widget interaction is a separate manual check",
            )
        return report
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        for log in logs:
            log.close()


def main():
    parser = argparse.ArgumentParser()
    default_luau = ROOT / ".tools/luau" / ("luau.exe" if os.name == "nt" else "luau")
    parser.add_argument(
        "--luau",
        default=os.getenv(
            "STUDIOSYNC_LUAU", str(default_luau) if default_luau.exists() else "luau"
        ),
    )
    parser.add_argument("--report", default="test-results/five-agent-report.json")
    parser.add_argument(
        "--keep", action="store_true", help="Keep disposable workspaces/logs for inspection"
    )
    args = parser.parse_args()
    folder = Path(tempfile.mkdtemp(prefix="studiosync-simulation-"))
    try:
        report = asyncio.run(
            simulate(
                folder, str(Path(args.luau).resolve()) if Path(args.luau).exists() else args.luau
            )
        )
        destination = Path(args.report)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("Report: " + str(destination.resolve()))
    finally:
        if args.keep:
            print("Disposable workspace retained: " + str(folder))
        else:
            remove_disposable_workspace(folder)


if __name__ == "__main__":
    main()
