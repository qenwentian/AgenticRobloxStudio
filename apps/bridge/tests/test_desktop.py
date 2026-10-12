import os
import time

import pytest
from studiosync_bridge import desktop
from studiosync_shared.config import Config, backend_token
from studiosync_shared.credentials import load_login, save_login


def test_plugin_install_and_previous_version(tmp_path, monkeypatch):
    source = tmp_path / "StudioSyncAI.rbxmx"
    source.write_text('<roblox><Item class="Script"/></roblox>')
    monkeypatch.setattr(desktop, "plugin_asset", lambda: source)
    folder = tmp_path / "plugins"
    installed = desktop.install_plugin(folder)
    assert installed.read_bytes() == source.read_bytes()
    installed.write_text("previous plugin")
    desktop.install_plugin(folder)
    assert installed.with_suffix(".rbxmx.previous").read_text() == "previous plugin"
    desktop.install_plugin(folder)
    assert installed.read_bytes() == source.read_bytes()


@pytest.mark.skipif(os.name != "nt", reason="Windows account encryption")
def test_saved_login_is_encrypted_expiring_and_scoped(tmp_path, monkeypatch):
    config = Config(
        project_id="project",
        workspace_id="workspace",
        developer_id="developer",
        workspace_root=str(tmp_path),
        bridge_token="x" * 32,
    )
    login = {"token": "secret-credential", "expires_at": time.time() + 300}
    save_login(config, login)
    assert b"secret-credential" not in (tmp_path / ".studiosync/login.dpapi").read_bytes()
    assert load_login(config) == login["token"]
    path = tmp_path / ".studiosync.json"
    path.write_text(config.model_dump_json())
    monkeypatch.setenv("STUDIOSYNC_CONFIG", str(path))
    monkeypatch.delenv("STUDIOSYNC_TOKEN", raising=False)
    assert backend_token() == login["token"]
    monkeypatch.setenv("STUDIOSYNC_TOKEN", "explicit-token")
    assert backend_token() == "explicit-token"
    with pytest.raises(ValueError, match="does not match"):
        load_login(config.model_copy(update={"workspace_id": "other-workspace"}))
    save_login(config, {**login, "expires_at": time.time() - 1})
    with pytest.raises(ValueError, match="expired"):
        load_login(config)
