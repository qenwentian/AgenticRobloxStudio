import os

import pytest
from fastapi.testclient import TestClient
from studiosync_server.app import create_app
from studiosync_server.coordination import Coordination
from studiosync_server.models import Base


@pytest.fixture
def team(tmp_path, request):
    url = (
        os.getenv("STUDIOSYNC_TEST_POSTGRES_URL")
        if request.node.get_closest_marker("postgres")
        else None
    )
    if request.node.get_closest_marker("postgres") and not url:
        pytest.skip("Set STUDIOSYNC_TEST_POSTGRES_URL to an isolated PostgreSQL test database")
    url = url or "sqlite:///" + (tmp_path / "test.db").as_posix()
    app = create_app(url, "test-bootstrap", Coordination)
    if url.startswith("postgres"):
        Base.metadata.drop_all(app.state.engine)
    Base.metadata.create_all(app.state.engine)
    client = TestClient(app)
    users = []
    for i in range(5):
        user = client.post(
            "/auth/register",
            headers={"X-Bootstrap-Key": "test-bootstrap"},
            json={"name": f"dev-{i}", "password": "correct-horse-test-password"},
        ).json()
        user["headers"] = {"Authorization": "Bearer " + user["token"]}
        users.append(user)
    pid = client.post("/projects", headers=users[0]["headers"], json={"name": "MOBA"}).json()[
        "project"
    ]["id"]
    for i, user in enumerate(users):
        if i:
            assert (
                client.post(
                    f"/projects/{pid}/members",
                    headers=users[0]["headers"],
                    json={"user_id": user["user_id"]},
                ).status_code
                == 200
            )

        def act(operation, payload=None, idx=i):
            return client.post(
                f"/projects/{pid}/actions/{operation}",
                headers=users[idx]["headers"],
                json=payload or {},
            )

        user["act"] = act
        user["workspace"] = act(
            "register_workspace", {"device": f"pc-{i}", "root_label": f"ws-{i}"}
        ).json()["data"]["workspace"]
        registered = act(
            "register_agent",
            {"display_name": f"Agent {i}", "workspace_id": user["workspace"]["id"]},
        ).json()["data"]
        user["session"] = registered["session"]["id"]
        user["agent"] = registered["agent"]["id"]
    yield {"app": app, "client": client, "users": users, "project": pid}
    app.state.engine.dispose()


def lease_input(user, lease):
    return {
        "session_id": user["session"],
        "lease_id": lease["id"],
        "lease_token": lease["lease_token"],
        "generation": lease["generation"],
    }
