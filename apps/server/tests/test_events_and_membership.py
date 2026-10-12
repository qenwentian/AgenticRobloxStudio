from sqlalchemy import delete
from studiosync_server.models import Event


def test_event_ids_do_not_reuse_after_retention(team):
    a = team["users"][0]
    cursor = a["act"]("get_activity", {"limit": 200}).json()["data"]["cursor"]
    with team["app"].state.sessions.begin() as db:
        db.execute(delete(Event))
    assert a["act"](
        "register_agent", {"display_name": "After retention", "workspace_id": a["workspace"]["id"]}
    ).json()["ok"]
    events = a["act"]("get_activity", {"cursor": cursor}).json()["data"]["events"]
    assert events and events[0]["id"] > cursor


def test_removal_revokes_membership_and_last_owner_is_retained(team):
    a, b = team["users"][:2]
    path = f"/projects/{team['project']}/members/"
    assert team["client"].delete(path + b["user_id"], headers=a["headers"]).status_code == 200
    assert b["act"]("get_project_state").status_code == 403
    assert team["client"].delete(path + a["user_id"], headers=a["headers"]).status_code == 409
