# StudioSync AI architecture and implementation contract

The coordination server is authoritative for metadata. The bridge owns local filesystem access.
The MCP adapter calls the backend over HTTP and optionally the bridge for controlled writes.
Studio polls its local bridge; no Python runs in Studio. Git + Rojo is the implemented source mode.
Team Create mode provides advisory reservations and observable edit warnings only.

## Technology assessment

Python 3.12, FastAPI/Pydantic, SQLAlchemy 2 and Alembic implement a synchronous transactional
core. PostgreSQL is required for multi-worker/team deployments. SQLite is supported for a
single server process. The maintained official MCP Python SDK v1 provides stdio and tool
schemas. HTTPX serves component HTTP calls. Watchfiles observes local edits; filelock
serializes cooperating bridge processes on the same workspace. Git and Rojo remain external
tools. No LLM, paid services, browser UI framework or message broker is required.

## Database schema

Users have password hashes and expiring hashed access tokens. Projects have memberships
(OWNER/DEVELOPER/VIEWER), workspaces, persistent agents and expiring sessions. Tasks reference
agents and workspaces indirectly, carry resources/criteria, and have normalized dependency edges.
Resources have project-unique canonical keys and revisions. Leases reference resource, session,
task, token hash, expiry and a project-wide increasing fencing generation. Contracts have
immutable version rows and per-consumer approved versions. Messages support threads and
per-session receipts. Submissions, conflicts and ordered events preserve history. Pending write
permits prevent lease transfer between central authorization and local replacement.

Every project mutation first increments its project fencing counter in the transaction. This
obtains a PostgreSQL row lock (or SQLite write lock) before conflict checks. Commit occurs before
success is returned. This intentionally serializes project mutations; split resource locks if
measured project throughput requires it. Resource aliases are canonicalized before checks.

## API and MCP contract

`/auth/register`, `/auth/login`, `/projects`, and project membership management establish identity.
All project operations use `POST /projects/{id}/actions/{operation}` with typed JSON input and
an `{ok,data,error}` response. Reads also require membership. Errors contain code, message and
structured details. OpenAPI and `packages/shared-schemas` expose actual schemas. MCP discovers
the same typed operations as `coordination.<operation>`, using normal tools/list and tools/call.
Sessions are developer-bound and workspace-bound; another developer cannot act as that session.
Events use an increasing cursor, bounded pages, and project-authorized WebSocket delivery.

The bridge binds 127.0.0.1, rejects browser Origins and unexpected Hosts, and requires a separate
local token. Pairing gives Studio a short-lived credential kept only in plugin memory. Backend
tokens never enter the plugin. File contents stay local. Controlled writes require a write lease,
token, generation, matching local hash and registered workspace. A pending central permit pins
ownership until commit/abort. An uncertain crash after replacement requires explicit observed-hash
reconciliation; it cannot silently be assumed safe. Ordinary crashed agents' leases expire.

## Milestones and acceptance

1. Foundation: schema migration, authentication, project/workspace/session registration; persistence tests.
2. Coordination: task claims, atomic hierarchical leases, fences and audit; threaded contention tests.
3. Collaboration: dependency cycles, messages, contracts, conflict records; handoff tests.
4. MCP: official stdio adapter with typed schemas; real client discovery/call tests.
5. Bridge: local authentication, watcher, guarded writes, Git inspection; traversal/revision tests.
6. Studio: real data dashboard and ownership/message actions; Rojo build and Luau validation.
7. Integration: submissions, reports, Git hook and mapping inspection; readiness tests.
8. End to end: five identities, five stdio clients and five bridges against a real server.

Test results and remaining manual checks are recorded in `docs/implementation-status.md`.
