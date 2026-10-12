from test_collaboration import task

from conftest import lease_input


def test_explicit_blocker_survives_dependency_completion(team):
    a, b = team["users"][:2]
    parent = task(a, "Parent", "file:src/parent.luau")
    child = task(b, "Child", "file:src/child.luau", [parent["id"]])
    assert (
        a["act"](
            "update_task",
            {
                "session_id": a["session"],
                "task_id": child["id"],
                "assigned_agent_id": b["agent"],
                "status": "BLOCKED",
                "reason": "Additional blocker",
            },
        ).json()["error"]["code"]
        == "TASK_TRANSITION"
    )
    assert a["act"]("claim_task", {"session_id": a["session"], "task_id": parent["id"]}).json()[
        "ok"
    ]
    assert a["act"](
        "update_task",
        {
            "session_id": a["session"],
            "task_id": child["id"],
            "status": "BLOCKED",
            "reason": "Manual investigation",
        },
    ).json()["ok"]
    lease = a["act"](
        "acquire_lock",
        {"session_id": a["session"], "resource": parent["resources"][0], "task_id": parent["id"]},
    ).json()["data"]["lease"]
    assert a["act"](
        "submit_work",
        {
            "session_id": a["session"],
            "task_id": parent["id"],
            "summary": "Parent tested",
            "work_ref": "branch:parent",
            "test_results": {
                "passed": True,
                "exit_code": 0,
                "command": "pytest test_task_edges.py",
            },
            "leases": [lease_input(a, lease)],
        },
    ).json()["ok"]
    assert a["act"](
        "update_task", {"session_id": a["session"], "task_id": parent["id"], "status": "COMPLETED"}
    ).json()["ok"]
    assert (
        b["act"]("claim_task", {"session_id": b["session"], "task_id": child["id"]}).json()[
            "error"
        ]["code"]
        == "TASK_TRANSITION"
    )
