# Cooperating agent workflow

During initial installation before a workspace is onboarded with `.studiosync.json`, follow the
developer's authorized bootstrap work and report that coordination is not configured. Once a
workspace is onboarded, the following rules apply to every cooperating agent.

1. Git + Rojo repository files are the source of truth for code. The central service is the
   source of truth for tasks, leases, messages, events and contracts.
2. Query project state and relevant recent events before starting; never assume a resource
   is unowned. Discover current workspace identity through the MCP context tool.
3. Register a unique named agent session, claim the assigned task and declare affected scope.
   Do not silently take another agent's assigned task.
4. Acquire required exclusive leases before controlled modifications. Do not modify resources
   owned by another agent. READ/INTENT reservations do not authorize writes.
5. Use workspace.read_file and workspace.controlled_write with expected hash, token and fence.
   Never describe direct editor/Git/Rojo writes as protected by the bridge.
6. Heartbeat at least every 20 seconds while working; monitor inbox/events at checkpoints.
   Expired credentials/sessions require explicit reconnection and ownership checks.
7. Communicate new dependencies immediately. Publish significant decisions and structured
   interface requests. Never silently change an agreed contract; owner and consumers approve.
8. Avoid duplicating functionality owned by another task. Limit changes to assigned scope;
   do not rewrite unrelated files or delete another agent's work.
9. Report blockers and conflicts instead of bypassing ownership. A rejected action never
   permits a write. Owner overrides require an audit reason and cannot hide uncertain writes.
10. Run relevant actual tests before submitting. Never report unexecuted tests as passing.
    Supply concise summaries, command/exit status, lease proofs and branch/commit references.
11. Confirm dependencies/contracts/conflicts are eligible, submit work for review, complete
    validated tasks, notify affected agents and release leases idempotently.
12. If coordination is unavailable, reject controlled writes and communicate the limitation.
    Do not invent successful confirmations, restore stale fences or auto-overwrite conflicts.
13. In Team Create mode reservations and monitoring are advisory. Do not configure Rojo and
    Script Sync as competing writers for one subtree. One pipeline owns integration Studio sync.
14. Keep credentials out of source, messages, screenshots and logs. No arbitrary remote shell
    or filesystem endpoints. Do not send source files to the central backend.

Implementation checks: run the relevant Pytest suite after Python coordination changes;
Ruff check/format; compile changed Luau and check StyLua formatting; exercise PostgreSQL
contention when changing transaction boundaries. Keep migrations immutable after deployment
and add new revisions. Update executed-check status accurately; do not claim Studio UI
interaction or Docker deployment tests unless actually performed.
