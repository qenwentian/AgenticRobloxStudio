"""Exercise the actual portable EXE against an isolated backend, with no plaintext token env."""

import asyncio
import hashlib
import importlib.util
import json
import os
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from studiosync_bridge.desktop import connect_workspace

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "simulation", ROOT / "examples/five-agent-simulation/run.py"
)
simulation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(simulation)


async def check_mcp(exe, path, env):
    params = StdioServerParameters(command=str(exe), args=["--mcp", "--config", str(path)], env=env)
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = await session.list_tools()
        assert any(tool.name == "workspace.controlled_write" for tool in tools.tools)
        result = await session.call_tool("coordination.get_workspace_context", {})
        assert not result.isError, result
        result = await session.call_tool("coordination.get_project_state", {"request": {}})
        assert not result.isError, result
        payload = result.structuredContent
        assert payload["ok"], payload


def main():
    exe = ROOT / "dist/StudioSyncAI.exe"
    checks = []
    with tempfile.TemporaryDirectory(prefix="studiosync-windows-") as temporary:
        folder = Path(temporary)
        env = {
            **os.environ,
            "STUDIOSYNC_DATABASE_URL": "sqlite:///" + (folder / "db.sqlite").as_posix(),
            "STUDIOSYNC_BOOTSTRAP_KEY": secrets.token_urlsafe(32),
        }
        env.pop("STUDIOSYNC_TOKEN", None)
        flags = subprocess.CREATE_NO_WINDOW
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=ROOT,
            env=env,
            check=True,
            capture_output=True,
            creationflags=flags,
        )
        port = simulation.free_port()
        url = f"http://127.0.0.1:{port}"
        processes = []
        with (folder / "services.log").open("w", encoding="utf-8") as log:
            try:
                processes.append(
                    subprocess.Popen(
                        [
                            sys.executable,
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
                        ],
                        cwd=ROOT,
                        env=env,
                        stdout=log,
                        stderr=log,
                        creationflags=flags,
                    )
                )
                simulation.wait_ready(url + "/health")
                password = secrets.token_urlsafe(32)
                with httpx.Client(base_url=url, trust_env=False) as client:
                    identity = client.post(
                        "/auth/register",
                        headers={"X-Bootstrap-Key": env["STUDIOSYNC_BOOTSTRAP_KEY"]},
                        json={"name": "portable-smoke", "password": password},
                    ).json()
                    client.headers["Authorization"] = "Bearer " + identity["token"]
                    project = client.post("/projects", json={"name": "Portable EXE smoke"}).json()[
                        "project"
                    ]
                workspace = folder / "workspace"
                workspace.mkdir()
                bridge_port = simulation.free_port()
                config, path = connect_workspace(
                    url, "portable-smoke", password, project["id"], str(workspace), bridge_port
                )
                checks.append(
                    "real login, membership and workspace onboarding with DPAPI saved credential"
                )
                # Force the frozen executable to decrypt the saved login, rather than inherit a token.
                env.pop("STUDIOSYNC_TOKEN", None)
                env["STUDIOSYNC_CONFIG"] = str(path)
                env["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
                for key in ("PYTHONPATH", "PYTHONHOME"):
                    env.pop(key, None)
                result = subprocess.run(
                    [str(exe), "--install-only", "--plugin-dir", str(folder / "plugins")],
                    env=env,
                    capture_output=True,
                    text=True,
                    check=True,
                    creationflags=flags,
                )
                assert "Installed:" in result.stdout
                assert (
                    hashlib.sha256((folder / "plugins/StudioSyncAI.rbxmx").read_bytes()).digest()
                    == hashlib.sha256((ROOT / "build/StudioSyncAI.rbxmx").read_bytes()).digest()
                )
                checks.append("EXE plugin installation and matching SHA-256")
                processes.append(
                    subprocess.Popen(
                        [str(exe), "--bridge", "--config", str(path)],
                        cwd=folder,
                        env=env,
                        stdout=log,
                        stderr=log,
                        creationflags=flags,
                    )
                )
                bridge_url = f"http://127.0.0.1:{bridge_port}"
                headers = {"X-Bridge-Token": config.bridge_token}
                simulation.wait_ready(bridge_url + "/health", headers)
                with httpx.Client(base_url=bridge_url, headers=headers, trust_env=False) as client:
                    assert client.get("/project").json()["ok"]
                    code = client.post("/pairing/code").json()["code"]
                    token = client.post("/pair", json={"code": code}).json()["token"]
                    assert client.get("/project", headers={"X-Bridge-Token": token}).json()["ok"]
                checks.append("EXE bridge, real backend polling and Studio pairing protocol")
                asyncio.run(check_mcp(exe, path, env))
                checks.append("EXE official MCP stdio discovery and real project/context calls")
                closed = subprocess.run(
                    [str(exe), "--mcp", "--config", str(path)],
                    input="",
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=20,
                    creationflags=flags,
                )
                assert closed.returncode == 0, closed.stderr
                assert "Traceback" not in closed.stderr, closed.stderr
                checks.append("EXE MCP clean shutdown after stdin closes")
            finally:
                for process in reversed(processes):
                    if process.poll() is None:
                        # A PyInstaller onefile launcher owns a child process running the bridge.
                        subprocess.run(
                            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                            capture_output=True,
                            check=False,
                            creationflags=flags,
                        )
                    process.wait(timeout=10)
        report = {
            "ok": True,
            "python": sys.version.split()[0],
            "checks": checks,
            "runtime": "portable EXE with Python removed from PATH and no token environment variable",
        }
        output = ROOT / "test-results/windows-client-report.json"
        output.parent.mkdir(exist_ok=True)
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
