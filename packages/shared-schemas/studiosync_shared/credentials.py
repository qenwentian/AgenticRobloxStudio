"""Optional Windows account-bound storage for the portable client, never passwords."""

import json
import os
import time
from pathlib import Path


def save_login(config, login: dict):
    if os.name != "nt":
        raise ValueError("Saved logins are supported only with Windows account encryption")
    import win32crypt

    metadata = {
        "token": login["token"],
        "expires_at": login["expires_at"],
        "server_url": config.server_url,
        "developer_id": config.developer_id,
        "workspace_id": config.workspace_id,
    }
    encrypted = win32crypt.CryptProtectData(
        json.dumps(metadata).encode(), "StudioSync login", None, None, None, 1
    )
    folder = Path(config.workspace_root) / ".studiosync"
    folder.mkdir(exist_ok=True)
    destination = folder / "login.dpapi"
    temporary = folder / "login.dpapi.tmp"
    temporary.write_bytes(encrypted)
    os.replace(temporary, destination)


def load_login(config) -> str:
    if os.name != "nt":
        raise ValueError("Set STUDIOSYNC_TOKEN to your expiring developer credential")
    import win32crypt

    try:
        encrypted = (Path(config.workspace_root) / ".studiosync/login.dpapi").read_bytes()
        _, plaintext = win32crypt.CryptUnprotectData(encrypted, None, None, None, 1)
        metadata = json.loads(plaintext)
    except Exception as exc:
        raise ValueError("No usable saved login; open StudioSyncAI.exe and sign in") from exc
    if any(
        metadata.get(key) != getattr(config, key)
        for key in ("server_url", "developer_id", "workspace_id")
    ):
        raise ValueError("Saved login does not match this workspace/server; sign in again")
    if metadata["expires_at"] <= time.time():
        raise ValueError("Saved login expired; open StudioSyncAI.exe and sign in again")
    return metadata["token"]
