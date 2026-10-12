import argparse
import getpass
import json
import os
import secrets
import shutil
import sys
from pathlib import Path

import httpx
from alembic import command
from alembic.config import Config as AlembicConfig
from studiosync_shared.client import Backend
from studiosync_shared.config import Config, backend_token, load_config
from studiosync_shared.resources import overlaps

from .workspace import Workspace


def output(value):
    print(json.dumps(value, indent=2, ensure_ascii=False))


def migrate():
    root = Path(__file__).resolve().parents[4]
    configuration = AlembicConfig(str(root / "alembic.ini"))
    configuration.set_main_option("script_location", str(root / "apps/server/migrations"))
    command.upgrade(configuration, "head")


def local_request(config, path, payload=None):
    with httpx.Client(
        base_url=f"http://127.0.0.1:{config.bridge_port}",
        headers={"X-Bridge-Token": config.bridge_token},
        timeout=15,
        trust_env=False,
    ) as client:
        result = client.get(path) if payload is None else client.post(path, json=payload)
        result.raise_for_status()
        return result.json()


def precommit(config, backend):
    workspace = Workspace(config.workspace_root)
    git = workspace.git()
    if not git["available"]:
        raise ValueError("Git repository unavailable")
    locks = backend.action("list_locks", {"limit": 200})
    if not locks.ok:
        raise ValueError(locks.error.message)
    state = backend.action("get_project_state", {"limit": 200})
    if not state.ok:
        raise ValueError(state.error.message)
    owned = {a["id"] for a in state.data["agents"] if a["user_id"] == config.developer_id}
    issues = ["Unresolved Git merge conflict: " + p for p in git["merge_conflicts"]]
    for file in git["files"]:
        resource = "file:" + file["path"].replace("\\", "/").casefold()
        for lease in locks.data["locks"]:
            if (
                lease["agent_id"] not in owned
                and lease["mode"] == "WRITE"
                and overlaps(resource, lease["resource"])
            ):
                issues.append(f"{file['path']} is reserved by {lease['agent_id']}")
    if local_request(config, "/health")["pending_writes"]:
        issues.append("Uncertain controlled writes require reconciliation")
    return {
        "ready": not issues,
        "issues": issues,
        "git": git,
        "coordination": backend.action("get_integration_status", {"limit": 200}).model_dump(),
    }


def doctor(config, backend):
    checks = []

    def check(name, function):
        try:
            detail = function()
            checks.append({"check": name, "ok": True, "detail": detail})
        except Exception as exc:
            checks.append({"check": name, "ok": False, "detail": str(exc)})

    def state():
        result = backend.action("get_project_state", {"limit": 1})
        if not result.ok:
            raise ValueError(result.error.message)
        if result.data["developer_id"] != config.developer_id:
            raise ValueError("Configured developer differs from credential")
        return result.data["project"]["name"]

    check("Python dependencies", lambda: __import__("mcp").__name__)
    check("Server/database/project membership", state)
    check("Workspace", lambda: str(Workspace(config.workspace_root).root))

    def executable(name):
        value = shutil.which(name)
        if not value:
            raise ValueError(name + " is not on PATH")
        return value

    check("Git", lambda: executable("git"))
    if config.mode == "GIT_ROJO":
        check("Rojo", lambda: executable("rojo"))
    check("MCP adapter", lambda: executable("studiosync-mcp"))
    check("Bridge", lambda: local_request(config, "/health"))

    def plugin():
        health = local_request(config, "/health")
        if not health["last_studio_poll"]:
            raise ValueError("No paired Studio plugin poll observed; open and pair the plugin")
        return health["last_studio_poll"]

    check("Studio plugin communication", plugin)
    return {"ok": all(c["ok"] for c in checks), "checks": checks}


def main():
    parser = argparse.ArgumentParser(
        prog="studiosync", description="StudioSync AI team coordination"
    )
    parser.add_argument("--config", default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)
    server = sub.add_parser("server")
    server.add_argument("action", choices=["start", "migrate"])
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8765)
    auth = sub.add_parser("auth")
    auth.add_argument("action", choices=["register", "login"])
    auth.add_argument("--name", required=True)
    auth.add_argument("--server-url", default="http://127.0.0.1:8765")
    project = sub.add_parser("project")
    project.add_argument("action", choices=["create", "join", "remove", "list"])
    project.add_argument("--name", default="Roblox Project")
    project.add_argument("--project-id")
    project.add_argument("--user-id", help="Owner adds this existing developer for join")
    project.add_argument("--role", choices=["OWNER", "DEVELOPER", "VIEWER"], default="DEVELOPER")
    project.add_argument("--server-url", default="http://127.0.0.1:8765")
    init = sub.add_parser("init")
    init.add_argument("--project-id", required=True)
    init.add_argument("--server-url", default="http://127.0.0.1:8765")
    init.add_argument("--root", default=".")
    init.add_argument("--mode", choices=["GIT_ROJO", "TEAM_CREATE"], default="GIT_ROJO")
    init.add_argument("--port", type=int, default=8766)
    bridge = sub.add_parser("bridge")
    bridge.add_argument("action", choices=["start", "pair"])
    for name in ["status", "agents", "locks", "doctor", "precommit", "integration"]:
        sub.add_parser(name)
    reconcile = sub.add_parser("reconcile")
    reconcile.add_argument("resource")
    action = sub.add_parser("action")
    action.add_argument("operation")
    action.add_argument("--json", default="{}")
    action.add_argument("--file", help="JSON input file; use for multiline messages")
    args = parser.parse_args()
    try:
        if args.cmd == "server":
            migrate()
            if args.action == "start":
                import uvicorn

                uvicorn.run(
                    "studiosync_server.app:production_app",
                    factory=True,
                    host=args.host,
                    port=args.port,
                    log_level="warning",
                )
            return
        if args.cmd == "auth":
            args.server_url = Config.secure_url(args.server_url)
            password = getpass.getpass("Password (12+ characters): ")
            headers = {"X-Bootstrap-Key": os.getenv("STUDIOSYNC_BOOTSTRAP_KEY", "")}
            with httpx.Client(trust_env=False, timeout=15) as client:
                result = client.post(
                    args.server_url + "/auth/" + args.action,
                    headers=headers,
                    json={"name": args.name, "password": password},
                )
                if result.is_error:
                    raise ValueError(result.json()["error"]["message"])
                output(result.json())  # Expiring token explicitly requested by local user.
            return
        if args.cmd == "project":
            args.server_url = Config.secure_url(args.server_url)
            with httpx.Client(
                base_url=args.server_url,
                trust_env=False,
                timeout=15,
                headers={"Authorization": "Bearer " + backend_token()},
            ) as client:
                if args.action == "list":
                    result = client.get("/projects")
                elif args.action == "create":
                    result = client.post("/projects", json={"name": args.name})
                elif args.action == "remove":
                    if not args.project_id or not args.user_id:
                        raise ValueError("Owner-side removal requires --project-id and --user-id")
                    result = client.delete(f"/projects/{args.project_id}/members/{args.user_id}")
                else:
                    if not args.project_id or not args.user_id:
                        raise ValueError(
                            "Owner-side join requires --project-id and --user-id; log in as project owner"
                        )
                    result = client.post(
                        f"/projects/{args.project_id}/members",
                        json={"user_id": args.user_id, "role": args.role},
                    )
                result.raise_for_status()
                output(result.json())
            return
        if args.cmd == "init":
            root = Path(args.root).resolve(strict=True)
            destination = Path(args.config).resolve() if args.config else root / ".studiosync.json"
            if destination.exists():
                raise ValueError("Configuration already exists; edit its settings explicitly")
            token = backend_token()
            backend = Backend(args.server_url, token, args.project_id)
            state = backend.action("get_project_state")
            if not state.ok:
                raise ValueError(state.error.message)
            workspace = backend.action(
                "register_workspace",
                {
                    "device": os.getenv("COMPUTERNAME", "local-device"),
                    "root_label": root.name,
                    "branch": Workspace(str(root)).git().get("branch", ""),
                    "mode": args.mode,
                },
            )
            if not workspace.ok:
                raise ValueError(workspace.error.message)
            config = Config(
                server_url=args.server_url,
                project_id=args.project_id,
                workspace_id=workspace.data["workspace"]["id"],
                workspace_root=str(root),
                developer_id=state.data["developer_id"],
                mode=args.mode,
                bridge_port=args.port,
                bridge_token=secrets.token_urlsafe(32),
            )
            destination.write_text(config.model_dump_json(indent=2), encoding="utf-8")
            os.chmod(destination, 0o600)
            print(f"Workspace registered. Configuration: {destination}")
            backend.close()
            return
        config = load_config(args.config)
        backend = Backend(config.server_url, backend_token(), config.project_id)
        if args.cmd == "bridge":
            if args.action == "pair":
                output(local_request(config, "/pairing/code", {}))
            else:
                import uvicorn

                from .app import create_bridge

                app = create_bridge(config, backend)
                print("Studio one-time pairing code: " + app.state.pairing["code"])
                print(
                    f"Bridge listening on 127.0.0.1:{config.bridge_port}; pairing code expires in 5 minutes"
                )
                uvicorn.run(app, host="127.0.0.1", port=config.bridge_port, log_level="warning")
            return
        if args.cmd == "doctor":
            report = doctor(config, backend)
        elif args.cmd in {"precommit", "integration"}:
            report = precommit(config, backend)
        elif args.cmd == "reconcile":
            report = local_request(config, "/files/reconcile", {"resource": args.resource})
        elif args.cmd == "action":
            payload = json.loads(
                Path(args.file).read_text(encoding="utf-8") if args.file else args.json
            )
            report = backend.action(args.operation, payload).model_dump()
        else:
            report = backend.action(
                {"status": "get_project_state", "agents": "list_agents", "locks": "list_locks"}[
                    args.cmd
                ]
            ).model_dump()
        output(report)
        backend.close()
        if report.get("ok") is False or report.get("ready") is False:
            sys.exit(1)
    except (ValueError, OSError, httpx.HTTPError, KeyError) as exc:
        print("StudioSync: " + str(exc), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
