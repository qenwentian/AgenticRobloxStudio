import hashlib

from conftest import lease_input


def task(user, title, resource, dependencies=None):
    response = user["act"](
        "create_task",
        {
            "session_id": user["session"],
            "title": title,
            "resources": [resource],
            "dependencies": dependencies or [],
        },
    )
    assert response.json()["ok"], response.text
    return response.json()["data"]["task"]


def test_dependency_cycle_claim_and_completion(team):
    a, b = team["users"][:2]
    combat = task(a, "Combat", "file:src/Combat.luau")
    abilities = task(b, "Abilities", "file:src/Abilities.luau", [combat["id"]])
    assert not b["act"](
        "claim_task", {"session_id": b["session"], "task_id": abilities["id"]}
    ).json()["ok"]
    assert (
        a["act"](
            "add_dependency",
            {"session_id": a["session"], "task_id": combat["id"], "depends_on": abilities["id"]},
        ).json()["error"]["code"]
        == "DEPENDENCY_CYCLE"
    )
    assert a["act"]("claim_task", {"session_id": a["session"], "task_id": combat["id"]}).json()[
        "ok"
    ]
    assert not b["act"]("claim_task", {"session_id": b["session"], "task_id": combat["id"]}).json()[
        "ok"
    ]
    lease = a["act"](
        "acquire_lock",
        {"session_id": a["session"], "resource": combat["resources"][0], "task_id": combat["id"]},
    ).json()["data"]["lease"]
    assert a["act"](
        "submit_work",
        {
            "session_id": a["session"],
            "task_id": combat["id"],
            "summary": "Tests executed by pytest",
            "work_ref": "branch:combat",
            "test_results": {
                "passed": True,
                "exit_code": 0,
                "command": "pytest test_collaboration.py",
            },
            "leases": [lease_input(a, lease)],
        },
    ).json()["ok"]
    assert a["act"](
        "update_task", {"session_id": a["session"], "task_id": combat["id"], "status": "COMPLETED"}
    ).json()["ok"]
    assert b["act"]("claim_task", {"session_id": b["session"], "task_id": abilities["id"]}).json()[
        "ok"
    ]


def test_private_messages_receipts_and_restart_visibility(team):
    a, b, c = team["users"][:3]
    sent = b["act"](
        "send_message",
        {
            "session_id": b["session"],
            "recipient_agent_id": a["agent"],
            "message_type": "INTERFACE_REQUEST",
            "subject": "Damage API",
            "body": "Need ApplyDamage",
        },
    ).json()["data"]["message"]
    inbox = a["act"]("get_messages", {"session_id": a["session"]}).json()["data"]["messages"]
    assert inbox[0]["id"] == sent["id"]
    assert a["act"](
        "acknowledge_message", {"session_id": a["session"], "message_id": sent["id"]}
    ).json()["ok"]
    assert a["act"]("get_messages", {"session_id": a["session"]}).json()["data"]["messages"][0][
        "acknowledged"
    ]
    assert c["act"]("get_messages", {"session_id": c["session"]}).json()["data"]["messages"] == []
    assert c["act"]("get_project_state").json()["data"]["messages"] == []
    assert not c["act"](
        "send_message",
        {"session_id": c["session"], "subject": "Peek", "body": "Hidden", "reply_to": sent["id"]},
    ).json()["ok"]


def test_contract_changes_require_owner_and_consumer_approval(team):
    a, b = team["users"][:2]
    payload = {
        "session_id": a["session"],
        "name": "Combat.ApplyDamage",
        "description": "Damage service",
        "signatures": {"ApplyDamage": {"params": ["DamageRequest"], "returns": "DamageResult"}},
        "consumers": [b["agent"]],
    }
    assert a["act"]("publish_interface", payload).json()["ok"]
    assert b["act"](
        "approve_interface", {"session_id": b["session"], "name": payload["name"], "version": 1}
    ).json()["ok"]
    change = a["act"](
        "propose_interface_change", {**payload, "errors": "May fail for invalid target"}
    ).json()["data"]
    assert change["version"]["version"] == 2 and change["affected_agents"] == [b["agent"]]
    assert not b["act"](
        "approve_interface", {"session_id": b["session"], "name": payload["name"], "version": 2}
    ).json()["ok"]
    assert a["act"](
        "approve_interface", {"session_id": a["session"], "name": payload["name"], "version": 2}
    ).json()["ok"]
    assert b["act"](
        "approve_interface", {"session_id": b["session"], "name": payload["name"], "version": 2}
    ).json()["ok"]
    assert b["act"]("check_conflicts").json()["data"]["conflicts"][0]["status"] == "RESOLVED"


def test_write_permit_pins_ownership_and_stale_revision(team):
    a, b = team["users"][:2]
    lease = a["act"](
        "acquire_lock", {"session_id": a["session"], "resource": "file:src/a.luau"}
    ).json()["data"]["lease"]
    old, new = hashlib.sha256(b"old").hexdigest(), hashlib.sha256(b"new").hexdigest()
    request = {
        **lease_input(a, lease),
        "workspace_id": a["workspace"]["id"],
        "resource": "file:src/a.luau",
        "expected_revision": old,
        "new_revision": new,
    }
    permit = a["act"]("prepare_write", request).json()["data"]["permit"]
    assert (
        a["act"]("release_lock", lease_input(a, lease)).json()["error"]["code"] == "WRITE_PENDING"
    )
    assert (
        b["act"](
            "acquire_lock", {"session_id": b["session"], "resource": "file:src/a.luau"}
        ).json()["error"]["code"]
        == "WRITE_PENDING"
    )
    assert a["act"]("finish_write", {"permit_id": permit["id"], "observed_revision": new}).json()[
        "ok"
    ]
    assert a["act"]("prepare_write", request).json()["error"]["code"] == "REVISION_MISMATCH"
    assert a["act"]("release_lock", lease_input(a, lease)).json()["ok"]


def test_event_cursor_and_bounded_pages(team):
    a = team["users"][0]
    first = a["act"]("get_activity", {"limit": 3}).json()["data"]
    second = a["act"]("get_activity", {"limit": 3, "cursor": first["cursor"]}).json()["data"]
    assert len(first["events"]) == 3
    assert second["events"][0]["id"] > first["cursor"]
