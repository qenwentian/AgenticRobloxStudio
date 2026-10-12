# Troubleshooting

Run `studiosync doctor` using your installed Python with your current token and configuration.

| Symptom | Action |
|---|---|
| Database tables missing | Run `studiosync server migrate`; verify STUDIOSYNC_DATABASE_URL |
| Unauthenticated backend | Log in again; refresh STUDIOSYNC_TOKEN and restart bridge/MCP |
| Forbidden project | Ask the owner to add the exact developer user ID to the project |
| Pairing fails/expires | Run `studiosync bridge pair`; enter its fresh single-use code |
| Studio credential stopped working | The bridge restarted or Studio disconnected; pair again with a fresh code |
| Studio cannot reach bridge | Confirm process/port, plugin HTTP permission and localhost URL |
| LOCAL_ORIGIN | Use 127.0.0.1/localhost and the plugin; browser Origin requests are rejected |
| Session expired | Register a new agent session; reconnect task ownership explicitly |
| LOCK_CONFLICT | Inspect returned covering owner; message them or wait for expiry/release |
| STALE_LEASE | Discard the old token/fence; reread state and acquire a new lease |
| REVISION_MISMATCH | Reread local file; review unexpected changes and synchronize branch revisions |
| WRITE_PENDING / UNCERTAIN_WRITE | Inspect file and pending journal; owner runs `studiosync reconcile file:PATH` |
| Contract prevents submission | Owner approves proposed version, then affected consumers confirm compatibility |
| Dependency blocked | Complete required parent submissions/tasks; cycles are rejected |
| Git unavailable | Initialize repository and create an initial commit; inspect current branch |
| Rojo missing | Install official Rojo and put it on PATH; run the supplied plugin build command |
| Watcher reports external edits | Inspect actual changes and active ownership; acknowledge/resolve with an audit reason |

If a pending write exists centrally but its local journal was lost, inspect the returned permit
metadata in project state. The owner can call `finish_write` with outcome RECONCILE and a
verified observed hash through the central administrative action API. Never infer the outcome
from lease expiry. A third hash is intentionally rejected and requires manual investigation.

The plugin reads only one bridge/project at a time. Switch its local bridge configuration to
change projects. An existing StudioSyncId attribute is informational; display path ownership
must be checked again after rename/reparent. External edits are not automatically reverted.

The Windows sandbox may block asyncio loopback sockets and Rojo canonicalization even for
local tests. Run the checks in an environment that permits loopback networking. CI includes
a separate PostgreSQL service; a skipped PostgreSQL test is not a successful PostgreSQL test.
