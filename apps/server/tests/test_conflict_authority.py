from studiosync_server.models import Conflict


def report(user, agents=None, description="Observed edit"):
    return user["act"](
        "report_conflict",
        {
            "category": "STUDIO_EDIT",
            "resources": ["instance:Workspace.Combat"],
            "agents": agents or [],
            "description": f"{description} by {user['user_id']}",
        },
    ).json()


def test_reporter_closes_their_own_conflict_and_others_cannot(team):
    owner, reporter, stranger = team["users"][:3]
    conflict = report(reporter, [owner["agent"]])["data"]["conflict"]
    assert conflict["reported_by"] == reporter["user_id"]
    rejected = stranger["act"](
        "resolve_conflict",
        {"conflict_id": conflict["id"], "status": "RESOLVED", "reason": "Not mine"},
    ).json()
    assert not rejected["ok"] and rejected["error"]["code"] == "FORBIDDEN"
    resolved = reporter["act"](
        "resolve_conflict",
        {"conflict_id": conflict["id"], "status": "RESOLVED", "reason": "Inspected the edit"},
    ).json()
    assert resolved["ok"] and resolved["data"]["conflict"]["status"] == "RESOLVED"
    assert resolved["data"]["conflict"]["history"][-1]["actor"] == reporter["user_id"]
    # The owner still arbitrates any conflict.
    assert owner["act"](
        "resolve_conflict",
        {"conflict_id": conflict["id"], "status": "ACKNOWLEDGED", "reason": "Reopened"},
    ).json()["ok"]


def test_involved_developer_can_close_a_conflict_they_did_not_report(team):
    holder, requester, stranger = team["users"][1:4]
    assert holder["act"](
        "acquire_lock", {"session_id": holder["session"], "resource": "file:src/Combat.luau"}
    ).json()["ok"]
    assert (
        requester["act"](
            "acquire_lock", {"session_id": requester["session"], "resource": "file:src/Combat.luau"}
        ).json()["error"]["code"]
        == "LOCK_CONFLICT"
    )
    conflicts = holder["act"]("check_conflicts").json()["data"]["conflicts"]
    resource_conflict = [c for c in conflicts if c["category"] == "RESOURCE"][-1]
    assert resource_conflict["reported_by"] == requester["user_id"]
    assert holder["agent"] in resource_conflict["agents"]
    assert holder["act"](
        "resolve_conflict",
        {"conflict_id": resource_conflict["id"], "status": "DISMISSED", "reason": "I released it"},
    ).json()["ok"]
    assert (
        stranger["act"](
            "resolve_conflict",
            {
                "conflict_id": resource_conflict["id"],
                "status": "ACKNOWLEDGED",
                "reason": "Not my resource",
            },
        ).json()["error"]["code"]
        == "FORBIDDEN"
    )


def test_conflict_without_reporter_stays_owner_only(team):
    owner, developer = team["users"][:2]
    with team["app"].state.sessions.begin() as db:
        legacy = Conflict(
            project_id=team["project"],
            category="OUT_OF_BAND",
            resources=["file:src/Legacy.luau"],
            agents=[],
            description="Recorded before reporters were stored",
            reported_by=None,
        )
        db.add(legacy)
        db.flush()
        legacy_id = legacy.id
    assert (
        developer["act"](
            "resolve_conflict",
            {"conflict_id": legacy_id, "status": "RESOLVED", "reason": "Legacy row"},
        ).json()["error"]["code"]
        == "FORBIDDEN"
    )
    assert owner["act"](
        "resolve_conflict",
        {"conflict_id": legacy_id, "status": "RESOLVED", "reason": "Owner arbitrates"},
    ).json()["ok"]


def test_developer_clears_own_lease_but_not_another_developers(team):
    owner, holder, other = team["users"][:3]
    owner_lease = owner["act"](
        "acquire_lock", {"session_id": owner["session"], "resource": "file:src/Combat.luau"}
    ).json()["data"]["lease"]
    assert (
        other["act"]("override_lock", {"lease_id": owner_lease["id"], "reason": "mine now"}).json()[
            "error"
        ]["code"]
        == "FORBIDDEN"
    )
    holder_lease = holder["act"](
        "acquire_lock", {"session_id": holder["session"], "resource": "subtree:src/economy"}
    ).json()["data"]["lease"]
    assert holder["act"](
        "override_lock", {"lease_id": holder_lease["id"], "reason": "My agent process stalled"}
    ).json()["ok"]
    assert (
        holder["act"]("check_lock", {"resource": "subtree:src/economy"}).json()["data"]["locks"]
        == []
    )
    assert (
        holder["act"]("check_lock", {"resource": "file:src/Combat.luau"}).json()["data"]["locks"]
        != []
    )
    assert (
        holder["act"]("override_lock", {"lease_id": owner_lease["id"], "reason": "no"}).json()[
            "error"
        ]["code"]
        == "FORBIDDEN"
    )


def test_viewer_cannot_override_or_resolve(team):
    owner, reporter, viewer = team["users"][:3]
    assert (
        team["client"]
        .post(
            f"/projects/{team['project']}/members",
            headers=owner["headers"],
            json={"user_id": viewer["user_id"], "role": "VIEWER"},
        )
        .status_code
        == 200
    )
    lease = owner["act"](
        "acquire_lock", {"session_id": owner["session"], "resource": "file:src/Combat.luau"}
    ).json()["data"]["lease"]
    conflict = report(reporter)["data"]["conflict"]
    assert (
        viewer["act"]("override_lock", {"lease_id": lease["id"], "reason": "curious"}).json()[
            "error"
        ]["code"]
        == "FORBIDDEN"
    )
    assert (
        viewer["act"](
            "resolve_conflict",
            {"conflict_id": conflict["id"], "status": "DISMISSED", "reason": "Read-only"},
        ).json()["error"]["code"]
        == "FORBIDDEN"
    )
