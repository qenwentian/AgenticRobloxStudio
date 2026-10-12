# Shared team deployment

## Local Docker setup

Copy `.env.example` to `.env`, choose random bootstrap and PostgreSQL password values, then:

```sh
docker compose up --build -d
```

Only `127.0.0.1:8765` is published. PostgreSQL uses a named persistent volume and has no
published public port. The server container runs as a non-root user and applies migrations
on startup. Docker configuration is supplied; local Docker execution is not implied by this
document. See implementation status for executed checks.

Use alphanumeric PostgreSQL passwords with the provided Compose URL; percent-encode other
URL-sensitive password characters if constructing a database URL directly. Never use
`docker compose down -v` on a database you want to preserve. Back up with `pg_dump`, test
restores on a separate database, and keep backups out of source control.

## Reachable HTTPS backend

Place the backend behind a TLS reverse proxy such as Caddy or your existing ingress. A minimal
Caddy route is:

```caddy
coordination.example.com {
    request_body {
        max_size 2MB
    }
    reverse_proxy 127.0.0.1:8765
}
```

Use a real hostname/DNS setup and restrict network access to the team where appropriate.
Configure deployment-wide rate limiting at your existing ingress. Account provisioning still
requires the bootstrap credential; all coordination access requires membership and Bearer auth.
Do not expose SQLite deployments with multiple workers. For multiple application workers,
run Alembic once as a release step and start Uvicorn directly with PostgreSQL thereafter.

## Five computers

1. Administrator creates five developers with `studiosync auth register --server-url HTTPS_URL`.
   Each developer chooses/receives their own credentials; no shared developer token.
2. Owner creates the shared project and adds the other existing users using owner-side
   `studiosync project join --project-id PROJECT --user-id DEVELOPER --server-url HTTPS_URL`.
3. Each developer logs in, sets their expiring `STUDIOSYNC_TOKEN`, and checks out their own
   branch/worktree of the game repo. Shared reservations apply across branches.
4. Each runs `studiosync init --server-url HTTPS_URL --project-id PROJECT --root WORKSPACE`.
   Exclude `.studiosync.json` and `.studiosync/` from the game repo.
5. Each runs their local bridge, pairs their Studio plugin, and configures their MCP client
   with that workspace configuration and developer environment.
6. Query state and register a unique named agent session per cooperating coding agent.
   For multiple coding agents on one computer, share the bridge but use different sessions.

One integration owner controls the Rojo session updating the integration Studio project.
Other developers may sync isolated Studio projects. Pull requests/Git review integrate changes;
StudioSync supplies ownership and compatibility evidence rather than replacing Git.

## Operations

`STUDIOSYNC_DATABASE_URL`, `STUDIOSYNC_BOOTSTRAP_KEY`, `STUDIOSYNC_SESSION_TTL` and
`STUDIOSYNC_EVENT_RETENTION_DAYS` configure the backend. `.studiosync.json` configures each
bridge's workspace/project/port/identity. Restart bridge/MCP processes after refreshing the
developer token. Polling backs off on failures. Metadata observations queue locally; guarded
writes fail closed when central authorization is unavailable.

Per-project mutations are serialized. This favors correctness over high throughput and suits
small teams; benchmark before scaling. Split lock domains only with new concurrency tests.
The server currently delivers persistent events through polling/WebSockets, without a broker.
