# Safety guarantees and limits

## What is enforced

The database transaction serializes project mutations before checks. Conflicting live write
leases cannot both be acquired. Reads conflict with writes; intent reservations authorize no
writes. Fencing numbers increase per project and are returned with opaque tokens. Tokens are
hashed in the database and omitted from list/state APIs. Expired leases cannot be renewed or
used for guarded writes. Another developer cannot impersonate an agent session.

Controlled bridge writes enforce registered workspace, exclusive covering lease, opaque token,
fence, local expected hash and central expected revision. Paths reject traversal, absolute
paths, streams, Windows device names, ambiguous case aliases, symlinks/junctions and internal
Git/bridge metadata. Temporary replacement occurs in the file's parent directory with fsync.
Cooperating local bridge processes serialize via a workspace FileLock.

The central write permit creates a durable transfer barrier before replacement. Pending permits
block overlapping leases and owner revocation. Successful replacement is followed by metadata
commit. If the bridge loses confirmation after replacing the file, it reports UNCERTAIN_WRITE
and preserves its recovery journal. A project owner must inspect/reconcile actual hashes.
Ordinary agent crashes have expiring leases; an uncertain filesystem commit intentionally
requires inspection rather than pretending the outcome is known. It is not auto-expired.

## What is observable or advisory

External editors, noncooperating processes and direct Git/Rojo/Studio writes are outside the
guarded API. A local external writer can race the final hash check/replacement. Neither
reservations nor file watching provide operating-system protection against those processes.
Watchfiles reports hashes and potential ownership violations; it cannot establish who edited.
An out-of-band revision is not silently adopted as central metadata. Synchronize/review Git
changes before acquiring a new controlled-write baseline; do not reset ownership to hide edits.

Studio reservations currently use display paths. The plugin reports an existing StudioSyncId
attribute if supplied by the project, but does not create one or claim stable distributed
identity. Rename/reparent operations invalidate path assumptions. Duplicate/period-containing
instance names need project conventions or a future stable-identity mapping before enforcement.
ScriptEditorService monitoring warns about observed edits. The plugin does not modify source
or game objects, so it does not need ChangeHistoryService recordings for DataModel changes.

Contracts record signatures and explicit consumer confirmations; semantic compatibility is
tested by actual project tests and reviewed by humans. Submission test results are client
attestations. Git readiness reports inspect status/conflicts but do not prove that a future
merge will be conflict-free. No staging, committing, pushing or merging is automatic.

## Credentials and deployment

Passwords use scrypt with random salts. Backend access tokens are random, hashed in the
database and expire after eight hours. Account creation requires the administrator bootstrap
key; remove/distribute it only through your administrative provisioning process. Role changes
take effect on the next request. The final owner cannot be demoted.

The portable Windows client saves its expiring access token in `.studiosync/login.dpapi`
using Windows account-bound DPAPI encryption. It binds the credential to the configured
server, developer and workspace and checks expiry before use. Passwords are never saved.
An explicit `STUDIOSYNC_TOKEN` still takes precedence. The distributed EXE/ZIP contains no
developer credentials, local configurations or database. Keep the private workspace files
out of source control; DPAPI does not isolate software running as the same Windows account.

The localhost bridge uses a separate master token. Pairing codes are random, single-use,
five-minute values with a five-attempt ceiling. A paired Studio token lives only as long as the
bridge process that issued it, and the plugin can end it explicitly with `/disconnect`. Studio
tokens may not read/write source files or retrieve master credentials. Tokens remain in plugin
memory. Restarting the bridge invalidates every paired Studio token, and each paired window is
labelled separately so two Studio windows stay distinguishable as agents.

Authority stays role-scoped: only the project owner dissolves another developer's lease, while
a developer may force-release a lease its own agent holds. Conflicts may be resolved by the
owner, by the developer who reported them, or by a developer whose agent the conflict names;
viewers read only. Every resolution stores its actor and reason in the conflict history.
Local master configuration is not an OS secret vault; protect it with account permissions.
Processes on the same account can read their own environment/files; localhost auth does not
isolate malicious software running as that account.

Use HTTPS for every non-loopback backend connection. Rate limits inside the server are bounded
process-local safeguards; a reverse proxy must enforce deployment-wide rate/body limits.
The default backend has no public filesystem API, arbitrary shell executor or mandatory LLM.
Back up PostgreSQL; use one migration runner during deployment, then start application workers.
Event retention defaults to 90 days. Other metadata retention is an administrative policy.
