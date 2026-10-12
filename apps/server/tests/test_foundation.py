from fastapi.testclient import TestClient
from studiosync_server.app import create_app
from studiosync_server.models import Base


def test_registration_membership_and_restart(tmp_path):
    url = "sqlite:///" + (tmp_path / "server.db").as_posix()
    app = create_app(url, "test-bootstrap")
    Base.metadata.create_all(app.state.engine)
    client = TestClient(app)
    assert client.get("/health").status_code == 200
    users = []
    for i in range(5):
        result = client.post(
            "/auth/register",
            headers={"X-Bootstrap-Key": "test-bootstrap"},
            json={"name": f"developer-{i}", "password": "correct-horse-test-password"},
        )
        assert result.status_code == 200, result.text
        users.append(result.json())
    project = client.post(
        "/projects", headers={"Authorization": "Bearer " + users[0]["token"]}, json={"name": "MOBA"}
    ).json()["project"]
    pid = project["id"]
    for i, user in enumerate(users):
        headers = {"Authorization": "Bearer " + user["token"]}
        if i:
            assert (
                client.post(
                    f"/projects/{pid}/members",
                    json={"user_id": user["user_id"]},
                    headers={"Authorization": "Bearer " + users[0]["token"]},
                ).status_code
                == 200
            )
        workspace = client.post(
            f"/projects/{pid}/actions/register_workspace",
            headers=headers,
            json={"device": f"device-{i}", "root_label": f"workspace-{i}"},
        ).json()["data"]["workspace"]
        result = client.post(
            f"/projects/{pid}/actions/register_agent",
            headers=headers,
            json={"display_name": f"Agent {i}", "workspace_id": workspace["id"]},
        )
        assert result.json()["ok"], result.text
        assert result.json()["data"]["session"]["last_heartbeat"] > 0
    app.state.engine.dispose()
    restarted = TestClient(create_app(url, "test-bootstrap"))
    response = restarted.post(
        f"/projects/{pid}/actions/list_agents",
        json={},
        headers={"Authorization": "Bearer " + users[0]["token"]},
    )
    assert len(response.json()["data"]["agents"]) == 5
    assert restarted.post(f"/projects/{pid}/actions/list_agents", json={}).status_code == 401
