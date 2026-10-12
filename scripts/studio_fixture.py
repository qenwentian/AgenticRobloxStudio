"""Temporary real backend/bridge fixture for nondestructive Studio plugin smoke checks."""

import importlib.util
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "simulation", ROOT / "examples/five-agent-simulation/run.py"
)
simulation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(simulation)


def main():
    folder = Path(tempfile.mkdtemp(prefix="studiosync-studio-"))
    env = {
        **os.environ,
        "STUDIOSYNC_DATABASE_URL": "sqlite:///" + (folder / "db.sqlite").as_posix(),
        "STUDIOSYNC_BOOTSTRAP_KEY": secrets.token_urlsafe(32),
    }
    processes, logs = [], []
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

    def start(component, target, port, environment):
        log = (folder / (component + ".log")).open("w", encoding="utf-8")
        logs.append(log)
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                target,
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            cwd=ROOT,
            env=environment,
            stdout=log,
            stderr=log,
            creationflags=flags,
        )
        processes.append(process)

    try:
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
        start("server", "studiosync_server.app:production_app", port, env)
        simulation.wait_ready(url + "/health")
        with httpx.Client(base_url=url, trust_env=False, timeout=10) as client:
            for i, name in enumerate(["Combat", "Abilities", "Inventory", "Match", "HUD"]):
                identity = client.post(
                    "/auth/register",
                    headers={"X-Bootstrap-Key": env["STUDIOSYNC_BOOTSTRAP_KEY"]},
                    json={
                        "name": "studio-dev-" + str(i),
                        "password": "test-" + secrets.token_urlsafe(24),
                    },
                ).json()
                headers = {"Authorization": "Bearer " + identity["token"]}
                if i == 0:
                    owner = identity
                    pid = client.post(
                        "/projects", headers=headers, json={"name": "StudioSync live smoke test"}
                    ).json()["project"]["id"]
                else:
                    client.post(
                        f"/projects/{pid}/members",
                        headers={"Authorization": "Bearer " + owner["token"]},
                        json={"user_id": identity["user_id"]},
                    ).raise_for_status()

                def action(operation, payload, pid=pid, headers=headers):
                    result = client.post(
                        f"/projects/{pid}/actions/{operation}", headers=headers, json=payload
                    ).json()
                    assert result["ok"], result
                    return result["data"]

                workspace = action(
                    "register_workspace", {"device": f"pc-{i}", "root_label": f"smoke-{i}"}
                )["workspace"]
                registered = action(
                    "register_agent",
                    {"display_name": name + " Agent", "workspace_id": workspace["id"]},
                )
                session = registered["session"]["id"]
                task = action(
                    "create_task",
                    {
                        "session_id": session,
                        "title": name + " implementation",
                        "resources": [f"system:{name}"],
                    },
                )["task"]
                action("claim_task", {"session_id": session, "task_id": task["id"]})
                action(
                    "acquire_lock",
                    {"session_id": session, "task_id": task["id"], "resource": f"system:{name}"},
                )
                if i == 0:
                    owner_workspace = workspace["id"]
        root = folder / "workspace"
        root.mkdir()
        bridge_port = simulation.free_port()
        code = secrets.token_urlsafe(16)
        config = {
            "server_url": url,
            "project_id": pid,
            "workspace_id": owner_workspace,
            "workspace_root": str(root),
            "developer_id": owner["user_id"],
            "bridge_port": bridge_port,
            "bridge_token": secrets.token_urlsafe(32),
        }
        path = root / ".studiosync.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        start(
            "bridge",
            "studiosync_bridge.app:production_bridge",
            bridge_port,
            {
                **env,
                "STUDIOSYNC_TOKEN": owner["token"],
                "STUDIOSYNC_CONFIG": str(path),
                "STUDIOSYNC_PAIR_CODE": code,
            },
        )
        bridge_url = f"http://127.0.0.1:{bridge_port}"
        simulation.wait_ready(bridge_url + "/health", {"X-Bridge-Token": config["bridge_token"]})
        info = ROOT / "test-results/studio-fixture.json"
        info.parent.mkdir(exist_ok=True)
        info.write_text(json.dumps({"url": bridge_url, "pair_code": code}), encoding="utf-8")
        print("STUDIO_FIXTURE_READY", flush=True)
        while True:
            time.sleep(1)
    finally:
        for process in reversed(processes):
            process.terminate()
            process.wait(timeout=10)
        for log in logs:
            log.close()
        shutil.rmtree(folder)


if __name__ == "__main__":
    main()
