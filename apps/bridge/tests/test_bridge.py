import hashlib
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from studiosync_bridge.app import create_bridge
from studiosync_server.models import Lease
from studiosync_shared.client import Backend
from studiosync_shared.config import Config

from conftest import lease_input


@pytest.fixture
def bridge(team, tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src" / "Combat.luau").write_text("return {}", encoding="utf-8")
    user = team["users"][0]
    client = TestClient(team["app"])
    client.headers.update(user["headers"])
    config = Config(
        project_id=team["project"],
        workspace_id=user["workspace"]["id"],
        developer_id=user["user_id"],
        workspace_root=str(root),
        bridge_token="local-test-token-that-is-32-characters",
    )
    backend = Backend("", "", team["project"], client)
    app = create_bridge(config, backend, pair_code="one-time-pairing-code", watch=False)
    local = TestClient(app, base_url="http://127.0.0.1:8766")
    local.headers["X-Bridge-Token"] = config.bridge_token
    return {"app": app, "client": local, "root": root, "config": config}


def test_guarded_write_revision_fence_and_watcher(team, bridge):
    a, b = team["users"][:2]
    local = bridge["client"]
    lease = a["act"](
        "acquire_lock", {"session_id": a["session"], "resource": "file:src/Combat.luau"}
    ).json()["data"]["lease"]
    read = local.post("/files/read", json={"resource": "file:src/Combat.luau"}).json()["data"]
    content = "return { ApplyDamage = function() return 10 end }"
    new = hashlib.sha256(content.encode()).hexdigest()
    request = {
        **lease_input(a, lease),
        "workspace_id": a["workspace"]["id"],
        "resource": "file:src/Combat.luau",
        "expected_revision": read["revision"],
        "new_revision": new,
        "content": content,
    }
    result = local.post("/files/write", json=request)
    assert result.json()["ok"], result.text
    assert (bridge["root"] / "src" / "Combat.luau").read_text() == content
    assert not local.post("/files/write", json=request).json()["ok"]
    watcher = bridge["app"].state.watcher
    watcher.observe([bridge["root"] / "src" / "Combat.luau"])
    assert a["act"]("check_conflicts").json()["data"]["conflicts"] == []
    (bridge["root"] / "src" / "Combat.luau").write_text("-- external change", encoding="utf-8")
    watcher.observe([bridge["root"] / "src" / "Combat.luau"])
    assert a["act"]("check_conflicts").json()["data"]["conflicts"][0]["category"] == "OUT_OF_BAND"
    with team["app"].state.sessions.begin() as db:
        db.get(Lease, lease["id"]).expires_at = time.time() - 1
    assert b["act"](
        "acquire_lock", {"session_id": b["session"], "resource": request["resource"]}
    ).json()["ok"]
    request["expected_revision"] = hashlib.sha256(b"-- external change").hexdigest()
    stale = local.post("/files/write", json=request).json()
    assert not stale["ok"] and stale["error"]["code"] == "STALE_LEASE"


def test_bridge_pairing_origins_and_scopes(bridge):
    client = bridge["client"]
    assert client.get("/health", headers={"Origin": "https://attacker.example"}).status_code == 403
    assert client.get("/health", headers={"Host": "attacker.example"}).status_code == 403
    result = client.post("/pair", json={"code": "one-time-pairing-code"})
    token = result.json()["token"]
    assert client.post("/pair", json={"code": "one-time-pairing-code"}).status_code == 403
    assert client.get("/project", headers={"X-Bridge-Token": token}).json()["ok"]
    assert (
        client.post(
            "/files/read",
            headers={"X-Bridge-Token": token},
            json={"resource": "file:src/Combat.luau"},
        ).status_code
        == 403
    )
    assert TestClient(bridge["app"], base_url="http://127.0.0.1").get("/project").status_code == 401


def test_paired_studio_credential_lasts_for_the_bridge_session(bridge, monkeypatch):
    client = bridge["client"]
    paired = client.post("/pair", json={"code": "one-time-pairing-code"}).json()
    token = paired["token"]
    assert paired["expires_at"] is None
    assert paired["studio_label"] == "Studio 1"
    # The code is burned on use, but the paired credential outlives it.
    assert client.post("/pair", json={"code": "one-time-pairing-code"}).status_code == 403
    # A second window pairs with a fresh code and is labelled separately.
    bridge["app"].state.pairing.update(
        code="second-pairing-code", expires=time.time() + 300, attempts=0
    )
    second = client.post("/pair", json={"code": "second-pairing-code"}).json()
    assert second["studio_label"] == "Studio 2" and second["token"] != token
    bridge["app"].state.pairing.update(code="unused-code", expires=time.time() + 300, attempts=0)
    later = time.time() + 3600 * 24
    monkeypatch.setattr("studiosync_bridge.app.time", SimpleNamespace(time=lambda: later))
    assert client.get("/project", headers={"X-Bridge-Token": token}).json()["ok"]
    # An unused code still expires, so a stale code cannot be reused a day later.
    bridge["app"].state.pairing.update(code="unused-code", expires=later - 1, attempts=0)
    assert client.post("/pair", json={"code": "unused-code"}).status_code == 403


def test_studio_disconnect_revokes_only_its_own_window(bridge):
    client = bridge["client"]
    first = client.post("/pair", json={"code": "one-time-pairing-code"}).json()["token"]
    bridge["app"].state.pairing.update(
        code="second-pairing-code", expires=time.time() + 300, attempts=0
    )
    second = client.post("/pair", json={"code": "second-pairing-code"}).json()["token"]
    headers = {"X-Bridge-Token": first}
    assert client.get("/project", headers=headers).json()["ok"]
    assert client.post("/disconnect", headers=headers).json()["ok"]
    assert client.get("/project", headers=headers).status_code == 401
    assert client.get("/project", headers={"X-Bridge-Token": second}).json()["ok"]
    assert client.get("/health").json()["status"] in {"ok", "disconnected"}


@pytest.mark.parametrize(
    "resource", ["file:../outside", "file:/outside", "file:src/file:stream", "file:.git/config"]
)
def test_bridge_traversal(bridge, resource):
    assert bridge["client"].post("/files/read", json={"resource": resource}).status_code == 422


def test_symlink_and_case_aliases(bridge, tmp_path):
    from studiosync_bridge.workspace import Workspace

    workspace = Workspace(str(bridge["root"]))
    assert workspace.path("file:SRC/combat.LUAU").name == "Combat.luau"
    outside = tmp_path / "outside"
    outside.mkdir()
    link = bridge["root"] / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Windows symlink creation requires Developer Mode or privilege")
    with pytest.raises(ValueError):
        workspace.path("file:link/escape.luau")


def test_central_unavailable_rejects_write(bridge, team):
    import httpx

    a = team["users"][0]
    lease = a["act"](
        "acquire_lock", {"session_id": a["session"], "resource": "file:src/Combat.luau"}
    ).json()["data"]["lease"]
    read = (
        bridge["client"]
        .post("/files/read", json={"resource": "file:src/Combat.luau"})
        .json()["data"]
    )
    backend = bridge["app"].state.watcher.backend
    backend.client = httpx.Client(base_url="http://127.0.0.1:1", timeout=1, trust_env=False)
    result = bridge["client"].post(
        "/files/write",
        json={
            **lease_input(a, lease),
            "workspace_id": a["workspace"]["id"],
            "resource": "file:src/Combat.luau",
            "expected_revision": read["revision"],
            "new_revision": hashlib.sha256(b"new").hexdigest(),
            "content": "new",
        },
    )
    assert not result.json()["ok"] and result.json()["error"]["code"] == "BACKEND_UNAVAILABLE"
    assert (bridge["root"] / "src" / "Combat.luau").read_text() == "return {}"
    backend.close()


def test_uncertain_write_reconciliation_pins_transfer(bridge, team, monkeypatch):
    from studiosync_shared.schemas import Error, Result

    a, b = team["users"][:2]
    lease = a["act"](
        "acquire_lock", {"session_id": a["session"], "resource": "file:src/Combat.luau"}
    ).json()["data"]["lease"]
    read = (
        bridge["client"]
        .post("/files/read", json={"resource": "file:src/Combat.luau"})
        .json()["data"]
    )
    backend = bridge["app"].state.watcher.backend
    actual = backend.action

    def disconnected_finish(operation, payload=None):
        if operation == "finish_write":
            return Result(
                ok=False,
                error=Error(
                    code="BACKEND_UNAVAILABLE", message="Injected post-replacement disconnect"
                ),
            )
        return actual(operation, payload)

    monkeypatch.setattr(backend, "action", disconnected_finish)
    result = bridge["client"].post(
        "/files/write",
        json={
            **lease_input(a, lease),
            "workspace_id": a["workspace"]["id"],
            "resource": "file:src/Combat.luau",
            "expected_revision": read["revision"],
            "new_revision": hashlib.sha256(b"new").hexdigest(),
            "content": "new",
        },
    )
    assert result.json()["error"]["code"] == "UNCERTAIN_WRITE"
    assert bridge["app"].state.ledger.pending()
    assert (
        b["act"](
            "acquire_lock", {"session_id": b["session"], "resource": "file:src/Combat.luau"}
        ).json()["error"]["code"]
        == "WRITE_PENDING"
    )
    monkeypatch.setattr(backend, "action", actual)
    assert (
        bridge["client"]
        .post("/files/reconcile", json={"resource": "file:src/Combat.luau"})
        .json()["ok"]
    )
    assert bridge["app"].state.ledger.pending() == []
