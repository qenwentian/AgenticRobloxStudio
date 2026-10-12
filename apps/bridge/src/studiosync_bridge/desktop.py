"""Small console installer and portable team client; no administrator rights required."""

import argparse
import asyncio
import getpass
import hashlib
import json
import multiprocessing
import os
import secrets
import shutil
import socket
import sys
from pathlib import Path
from xml.etree import ElementTree

import httpx
from studiosync_shared.client import Backend
from studiosync_shared.config import Config, backend_token, load_config
from studiosync_shared.credentials import save_login

from .workspace import Workspace


def plugin_asset() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "StudioSyncAI.rbxmx"
    return Path(__file__).resolve().parents[4] / "build/StudioSyncAI.rbxmx"


def install_plugin(destination: Path | None = None) -> Path:
    folder = destination or Path(os.environ["LOCALAPPDATA"]) / "Roblox/Plugins"
    folder.mkdir(parents=True, exist_ok=True)
    source = plugin_asset()
    ElementTree.parse(source)
    installed = folder / source.name
    new_hash = hashlib.sha256(source.read_bytes()).digest()
    if installed.exists() and hashlib.sha256(installed.read_bytes()).digest() != new_hash:
        backup = installed.with_suffix(".rbxmx.previous")
        shutil.copy2(installed, backup)
    temporary = folder / "StudioSyncAI.installing"
    shutil.copyfile(source, temporary)
    os.replace(temporary, installed)
    if hashlib.sha256(installed.read_bytes()).digest() != new_hash:
        raise ValueError("Installed plugin failed its integrity check")
    return installed


def client_settings_path() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "StudioSyncAI/client.json"


def connect_workspace(
    server_url: str, name: str, password: str, project_id: str, root: str, port=8766
) -> tuple[Config, Path]:
    server_url = Config.secure_url(server_url)
    folder = Workspace(root).root
    with httpx.Client(base_url=server_url, trust_env=False, timeout=15) as client:
        response = client.post("/auth/login", json={"name": name, "password": password})
        if response.is_error:
            raise ValueError(response.json().get("error", {}).get("message", "Login failed"))
        login = response.json()
    backend = Backend(server_url, login["token"], project_id)
    try:
        state = backend.action("get_project_state")
        if not state.ok:
            raise ValueError(state.error.message)
        path = folder / ".studiosync.json"
        if path.exists():
            config = load_config(str(path))
            if (
                config.server_url,
                config.project_id,
                config.developer_id,
                Path(config.workspace_root).resolve(),
            ) != (server_url, project_id, login["user_id"], folder):
                raise ValueError(
                    "This folder is configured for another project/account. Choose its matching account or another workspace."
                )
        else:
            workspace = backend.action(
                "register_workspace",
                {
                    "device": socket.gethostname(),
                    "root_label": folder.name,
                    "branch": Workspace(root).git().get("branch", ""),
                    "mode": state.data["project"]["mode"],
                },
            )
            if not workspace.ok:
                raise ValueError(workspace.error.message)
            config = Config(
                server_url=server_url,
                project_id=project_id,
                workspace_id=workspace.data["workspace"]["id"],
                workspace_root=str(folder),
                developer_id=login["user_id"],
                mode=state.data["project"]["mode"],
                bridge_port=port,
                bridge_token=secrets.token_urlsafe(32),
            )
            path.write_text(config.model_dump_json(indent=2), encoding="utf-8")
            os.chmod(path, 0o600)
        save_login(config, login)
        os.environ["STUDIOSYNC_CONFIG"] = str(path)
        os.environ["STUDIOSYNC_TOKEN"] = login["token"]
        return config, path
    finally:
        backend.close()


def start_bridge(config):
    import uvicorn

    from .app import create_bridge

    with socket.socket() as listener:
        try:
            listener.bind(("127.0.0.1", config.bridge_port))
        except OSError as exc:
            raise ValueError(
                f"Port {config.bridge_port} is in use. Close your previous StudioSync bridge first."
            ) from exc
    app = create_bridge(config)
    print(f"\nBridge: http://127.0.0.1:{config.bridge_port}", flush=True)
    print("Studio pairing code: " + app.state.pairing["code"], flush=True)
    print("Open StudioSync Settings and pair using this code. Keep this window open.\n", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=config.bridge_port, log_level="warning")


def wizard():
    print("StudioSync AI - Windows setup\n")
    installed = install_plugin()
    print("Plugin installed: " + str(installed))
    print("Restart Roblox Studio, then open Plugins > StudioSync AI > StudioSync.\n")
    preferences = client_settings_path()
    defaults = json.loads(preferences.read_text()) if preferences.exists() else {}
    default_server = defaults.get("server_url", "")
    server = (
        input(f"Shared server URL [{default_server}] (blank = finish installation): ").strip()
        or default_server
    )
    if not server:
        return
    server = Config.secure_url(server)
    name = input("Your developer username: ").strip()
    password = getpass.getpass("Your password: ")
    with httpx.Client(base_url=server, trust_env=False, timeout=15) as client:
        response = client.post("/auth/login", json={"name": name, "password": password})
        if response.is_error:
            raise ValueError(response.json().get("error", {}).get("message", "Login failed"))
        client.headers["Authorization"] = "Bearer " + response.json()["token"]
        response = client.get("/projects")
        response.raise_for_status()
        projects = response.json()["projects"]
    if not projects:
        raise ValueError(
            "No project membership. Ask your team owner to add your developer account."
        )
    for index, project in enumerate(projects, 1):
        print(f"{index}. {project['name']}")
    selected = int(input("Project number [1]: ").strip() or "1") - 1
    if selected < 0 or selected >= len(projects):
        raise ValueError("Choose a project number from the list")
    default_root = defaults.get("workspace_root", "")
    root = (input(f"Your game workspace folder [{default_root}]: ").strip() or default_root).strip(
        '"'
    )
    config, path = connect_workspace(server, name, password, projects[selected]["id"], root)
    preferences.parent.mkdir(parents=True, exist_ok=True)
    preferences.write_text(
        json.dumps(
            {
                "server_url": server,
                "workspace_root": str(config.workspace_root),
                "config_path": str(path),
            }
        ),
        encoding="utf-8",
    )
    executable = str(Path(sys.executable).resolve())
    mcp_config = {
        "mcpServers": {
            "studiosync": {"command": executable, "args": ["--mcp", "--config", str(path)]}
        }
    }
    if getattr(sys, "frozen", False):
        (Path(config.workspace_root) / ".studiosync/mcp-client.json").write_text(
            json.dumps(mcp_config, indent=2), encoding="utf-8"
        )
    print(
        "Workspace connected. Saved login expires in eight hours and is encrypted for this Windows account."
    )
    start_bridge(config)


def self_test():
    from studiosync_mcp.server import create_mcp

    from .app import create_bridge

    tree = ElementTree.parse(plugin_asset())
    assert tree.find(".//Item[@class='Script']") is not None
    backend = Backend("http://127.0.0.1:1", "self-test", "self-test")
    adapter = create_mcp(backend)
    tools = asyncio.run(adapter.list_tools())
    assert any(tool.name == "workspace.controlled_write" for tool in tools)
    backend.close()
    print(
        json.dumps(
            {
                "ok": True,
                "plugin_sha256": hashlib.sha256(plugin_asset().read_bytes()).hexdigest(),
                "mcp_tools": len(tools),
                "bridge_factory": create_bridge.__name__,
            }
        )
    )


def main():
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser(description="StudioSync AI portable Windows client")
    parser.add_argument("--install-only", action="store_true")
    parser.add_argument("--plugin-dir", type=Path, help="Optional custom Studio Plugins folder")
    parser.add_argument("--config")
    parser.add_argument("--bridge", action="store_true")
    parser.add_argument("--mcp", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.config:
        os.environ["STUDIOSYNC_CONFIG"] = str(Path(args.config).resolve())
    try:
        if args.self_test:
            self_test()
        elif args.mcp:
            from studiosync_mcp.server import main as mcp_main

            mcp_main()
        elif args.bridge:
            backend_token()  # Verify saved login before opening the listener.
            start_bridge(load_config())
        elif args.install_only:
            print("Installed: " + str(install_plugin(args.plugin_dir)))
        else:
            wizard()
            input("\nPress Enter to close. ")
    except (OSError, ValueError, KeyError, httpx.HTTPError) as exc:
        print("StudioSync: " + str(exc), file=sys.stderr)
        if not any([args.mcp, args.bridge, args.install_only, args.self_test]):
            input("\nPress Enter to close. ")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
