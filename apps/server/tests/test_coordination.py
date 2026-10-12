import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from studiosync_server.models import AgentSession, Lease

from conftest import lease_input


def race(team, resources):
    def acquire(i):
        user = team["users"][i]
        return user["act"](
            "acquire_lock", {"session_id": user["session"], "resource": resources[i]}
        ).json()

    with ThreadPoolExecutor(max_workers=5) as pool:
        return list(pool.map(acquire, range(5)))


def test_five_exclusive_requests_one_winner(team):
    results = race(team, ["file:src/Combat.luau"] * 5)
    assert sum(r["ok"] for r in results) == 1
    assert sum(bool(r["error"] and r["error"]["code"] == "LOCK_CONFLICT") for r in results) == 4


@pytest.mark.postgres
def test_postgres_five_exclusive_requests_one_winner(team):
    results = race(team, ["file:src/Combat.luau"] * 5)
    assert sum(r["ok"] for r in results) == 1


@pytest.mark.postgres
def test_postgres_independent_and_hierarchical_leases(team):
    test_independent_hierarchy_and_reader_policy(team)


@pytest.mark.postgres
def test_postgres_expiration_and_fencing(team):
    test_crash_expiration_fencing_and_idempotence(team)


def test_independent_hierarchy_and_reader_policy(team):
    assert all(r["ok"] for r in race(team, [f"file:src/system{i}.luau" for i in range(5)]))
    a, b = team["users"][:2]
    assert a["act"](
        "acquire_lock", {"session_id": a["session"], "resource": "directory:src/server/"}
    ).json()["ok"]
    denied = b["act"](
        "acquire_lock", {"session_id": b["session"], "resource": "file:SRC\\server\\Combat.luau"}
    ).json()
    assert not denied["ok"]
    read = {"session_id": a["session"], "resource": "file:src/shared.luau", "mode": "READ"}
    assert a["act"]("acquire_lock", read).json()["ok"]
    assert b["act"]("acquire_lock", {**read, "session_id": b["session"]}).json()["ok"]
    assert not b["act"](
        "acquire_lock", {**read, "session_id": b["session"], "mode": "WRITE"}
    ).json()["ok"]
    assert b["act"]("acquire_lock", {**read, "session_id": b["session"], "mode": "INTENT"}).json()[
        "ok"
    ]
    assert a["act"](
        "acquire_lock", {"session_id": a["session"], "resource": "subtree:Workspace.Map"}
    ).json()["ok"]
    assert not b["act"](
        "acquire_lock", {"session_id": b["session"], "resource": "instance:Workspace.Map.Lanes.Top"}
    ).json()["ok"]


def test_crash_expiration_fencing_and_idempotence(team):
    a, b = team["users"][:2]
    lease = a["act"](
        "acquire_lock", {"session_id": a["session"], "resource": "file:src/a.luau"}
    ).json()["data"]["lease"]
    with team["app"].state.sessions.begin() as db:
        db.get(Lease, lease["id"]).expires_at = time.time() - 1
        db.get(AgentSession, a["session"]).expires_at = time.time() - 1
    replacement = b["act"](
        "acquire_lock", {"session_id": b["session"], "resource": "file:src/a.luau"}
    ).json()["data"]["lease"]
    assert replacement["generation"] > lease["generation"]
    assert not a["act"]("renew_lock", lease_input(a, lease)).json()["ok"]
    for _ in range(2):
        assert b["act"]("release_lock", lease_input(b, replacement)).json()["ok"]
    agents = b["act"]("list_agents").json()["data"]["agents"]
    assert (
        next(agent for agent in agents if agent["id"] == a["agent"])["sessions"][0]["status"]
        == "OFFLINE"
    )


def test_project_isolation_and_viewer(team):
    a, b = team["users"][:2]
    project = (
        team["client"]
        .post("/projects", headers=a["headers"], json={"name": "Private"})
        .json()["project"]
    )
    response = team["client"].post(
        f"/projects/{project['id']}/actions/list_agents", headers=b["headers"], json={}
    )
    assert response.status_code == 403
    assert not b["act"]("heartbeat", {"session_id": a["session"]}).json()["ok"]
    assert (
        team["client"]
        .post(
            f"/projects/{team['project']}/members",
            headers=a["headers"],
            json={"user_id": b["user_id"], "role": "VIEWER"},
        )
        .status_code
        == 200
    )
    assert (
        b["act"](
            "acquire_lock", {"session_id": b["session"], "resource": "file:src/view.luau"}
        ).status_code
        == 403
    )


@pytest.mark.parametrize(
    "resource",
    [
        "file:../outside",
        "file:C:\\outside",
        "file:/outside",
        "file:src/A.luau:stream",
        "file:src/a. ",
        "file:src/CON.luau",
        "file:.git/config",
    ],
)
def test_resource_alias_rejection(team, resource):
    a = team["users"][0]
    assert (
        a["act"]("acquire_lock", {"session_id": a["session"], "resource": resource}).status_code
        == 422
    )
