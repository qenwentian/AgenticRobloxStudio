# API and MCP contracts

FastAPI exposes interactive `/docs` and machine-readable `/openapi.json`. The generated
`packages/shared-schemas/contracts.json` lists the actual Pydantic inputs and output envelope.
All timestamps are UTC Unix seconds. Developer access tokens expire eight hours after login.

Authentication: `POST /auth/register` uses `X-Bootstrap-Key` (administrator-only provisioning).
`POST /auth/login` takes a name/password and returns token, developer ID and expiry.
Use `Authorization: Bearer TOKEN` for all project and action APIs.

Projects: `GET /projects`, `POST /projects`, `POST /projects/{id}/members` and
`DELETE /projects/{id}/members/{user_id}` (owner-only access management). Removal disconnects
that developer's sessions and revokes ordinary leases; pending writes still need reconciliation.
Modes are `GIT_ROJO` and `TEAM_CREATE`. Roles are `OWNER`, `DEVELOPER`, `VIEWER`.

Actions: `POST /projects/{id}/actions/{operation}` takes a raw typed JSON body. Success is
`{ok:true,data:{...},error:null}`. Rejection is `{ok:false,data:{},error:{code,message,details}}`.
Most rejections also use an HTTP error status; contention returns a normal typed negative result
so the denial and conflict audit can be committed in the same transaction. Clients must inspect
`ok` for every response. Errors never authorize a file operation.

MCP names are `coordination.<operation>`, with the same input under `request`. Tools publish
JSON input/output schemas through the official SDK. The adapter exposes all coordination
operations except the internal `prepare_write` and `finish_write` protocol. Local source access
uses `workspace.read_file` and `workspace.controlled_write` and requires the configured bridge.

| Area | Operations | Policy |
|---|---|---|
| State | get_project_state, list_agents, list_tasks, list_locks, check_lock, get_resource_owner, get_integration_status | Any member |
| Identity | register_workspace, register_agent, heartbeat | Developer; session/workspace bound |
| Tasks | create_task, claim_task, update_task, get_task_dependencies, add_dependency, report_blocker | Developer; assigned-owner transitions; owner reassignment with reason |
| Leases | acquire_lock, renew_lock, release_lock | Developer; session, opaque token and fence |
| Overrides | override_lock | Owner; mandatory reason, pending-write barrier |
| Messages | send_message, get_messages, acknowledge_message, request_resource_transfer | Member reads their session inbox; developer sends/acks; scoped replies |
| Interfaces | publish_interface, get_interface, propose_interface_change, approve_interface, get_affected_agents | Developer proposals; owning agent approval, then consumers confirm |
| Submissions | submit_work | Assigned agent, task-scoped lease proofs, passing test evidence, dependencies/contracts/conflicts checked |
| Events | get_activity, get_recent_changes | Project membership; cursor pagination |
| Conflicts | check_conflicts, report_conflict, resolve_conflict | Member reads; developer reports; owner records resolution |

Paged lists use offset `cursor` and `limit` (1–200). Activity uses an event-ID cursor with
strictly increasing ordering. Direct-message bodies are shown only to sender/recipient
developers, not unrelated project members. Audit events contain metadata only. Recent event
summaries stay compact and do not automatically inject conversations into agent context.

External clients can connect `ws(s)://SERVER/projects/{id}/events` with an Authorization header
and send `{"cursor":0}`. The server sends bounded event pages every two seconds and rechecks
authorization/token expiry. Studio uses REST polling through its bridge. Events are retained
for 90 days by default; tasks, messages, lease history, submissions and conflicts persist.

## Bridge API

Bind: `127.0.0.1:8766` by default, configurable per workspace. Every endpoint rejects browser
Origins and unexpected Hosts. `X-Bridge-Token` carries either the local master token or the
short-lived paired Studio token. `/pair` alone accepts a one-time code without that header.
No cookies, CORS wildcard, arbitrary shell endpoints or public file endpoints are provided.

`GET /project` returns the dashboard state; `/agents`, `/tasks`, `/locks`, `/activity`,
`/conflicts` are compact reads. `/workspace` shows local Git and Rojo metadata. `/health`
reports backend health, watcher status, queued events, uncertain writes and last Studio poll.
`POST /actions/{operation}` forwards approved typed actions. Studio may not invoke internal
write-permit operations or register arbitrary workspaces. Source endpoints are master-only:
`POST /files/read`, `/files/write`, `/files/reconcile`. `POST /pairing/code` issues a new code.

Read resources use `{"resource":"file:src/server/CombatService.luau"}`. Write inputs include
session_id, lease_id, lease_token, generation, workspace_id, resource, expected_revision,
new_revision, content. Reconciliation obtains the observed local hash itself and requires
owner authorization centrally. All file contents stay on the local machine.
