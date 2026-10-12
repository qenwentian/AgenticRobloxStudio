# StudioSync AI

StudioSync coordinates developers and AI agents through persistent tasks, resource leases,
messages, versioned interfaces and work submissions. It includes a FastAPI backend, official
MCP stdio adapter, localhost bridge and a dockable Roblox Studio plugin. Source files remain
in your Git repositories. The server stores metadata and hashes, not source contents.

This is a tested development foundation. See [implementation status](docs/implementation-status.md)
for executed checks and limits. Controlled bridge writes enforce ownership. External editors,
Git commands, Rojo and Team Create can still write outside that mechanism.

## Install on Windows

Download the [Windows ZIP from GitHub Releases](https://github.com/qenwentian/AgenticRobloxStudio/releases/download/v0.1.0/StudioSyncAI-Windows.zip).

For friends, extract `dist/StudioSyncAI-Windows.zip` and run `StudioSyncAI.exe`.
It installs the Studio plugin and connects their workspace to your team's shared server.
See [the portable client instructions](docs/friends-quickstart.txt). Python is bundled in
the executable; your development checkout uses your existing system Python 3.12.

Use Python 3.12+, Git, Roblox Studio and Rojo. Run from this source checkout:

```powershell
python -m pip install --no-build-isolation -e '.[dev,bundle]'
$env:STUDIOSYNC_BOOTSTRAP_KEY = python -c "import secrets; print(secrets.token_urlsafe(32))"
studiosync server start
```

The server migrates its SQLite database and listens on `127.0.0.1:8765`. Keep the bootstrap
key available in the administrator's terminal for initial account provisioning. It is never
needed by the plugin. For macOS/Linux use ordinary shell exports.
The server/bridge/MCP Python components are portable; plugin tools depend on platform binaries.

Open a second terminal using the same Python installation and provision a developer:

```powershell
$env:STUDIOSYNC_BOOTSTRAP_KEY = 'the-key-from-the-server-terminal'
studiosync auth register --name DeveloperA
```

Enter a password when prompted. Copy the returned expiring token into the local process
environment (the command deliberately displays the login result):

```powershell
$env:STUDIOSYNC_TOKEN = 'returned-token'
studiosync project create --name 'MOBA Team'
studiosync init --project-id 'returned-project-id' --root 'C:\Projects\MyMoba'
$env:STUDIOSYNC_CONFIG = 'C:\Projects\MyMoba\.studiosync.json'
studiosync bridge start
```

`init` registers the exact workspace, device, developer and branch. It writes local bridge
configuration to `.studiosync.json`; exclude that file and `.studiosync/` from your game repo.
The backend token is inherited from the environment and expires after eight hours. Login again
and restart bridge/MCP processes to refresh it. File mode 0600 is set on Unix; restrict the
configuration's Windows ACL to your account if your checkout is shared with other OS users.

## Install and open the Studio plugin

Build the actual local plugin artifact:

```powershell
New-Item -ItemType Directory -Force build
rojo build apps/roblox-plugin/plugin.project.json -o build/StudioSyncAI.rbxmx
```

In Roblox Studio, open **Plugins → Plugins Folder**. Copy `build/StudioSyncAI.rbxmx` into that
folder, then restart Studio. Open the **StudioSync AI** toolbar and click **StudioSync**.
This local plugin model contains an executable Script. It is a plugin artifact, not a place
file; do not insert it into the game's script hierarchy. Rojo can also emit `.rbxm` using
`-o build/StudioSyncAI.rbxm` for distribution.

In the dashboard's **Settings** tab, enter the bridge address (default `http://127.0.0.1:8766`)
and the one-time pairing code printed by `studiosync bridge start`. Click **Pair and test
connection**. Allow the plugin's requested localhost HTTP permission if Studio prompts.
If Studio refuses HTTP, inspect its plugin permissions and the troubleshooting guide.

The Studio credential lasts 15 minutes and stays in plugin memory. Generate a fresh code using
`studiosync bridge pair` when it expires. The plugin never stores backend credentials or edits
game source. Overview inspects selection ownership; other tabs show agents, tasks, reservations,
messages, contracts and conflicts. Backend authorization applies to every action. Script edits
produce observable reservation warnings where ScriptEditorService emits changes.

## Connect AI agents

Start the bridge with your developer token, then configure a compatible MCP client to launch:

```text
python -m studiosync_mcp.server
```

The MCP process needs `STUDIOSYNC_CONFIG` and `STUDIOSYNC_TOKEN`, or the Windows account's
encrypted login saved by the portable client. Use your client's supported
environment/secret mechanism instead of committing credentials. The generic JSON and
client-specific TOML examples are in [example-agent-configs](packages/example-agent-configs).
The TOML example is illustrative; verify the current client schema before installing it.
The integration itself is exercised using the official SDK's actual stdio client.

Every MCP action uses a typed `request` object. Begin with
`coordination.get_workspace_context` (no arguments), then `coordination.get_project_state`
with `{"request":{}}`. Register a named agent using the returned workspace ID. Keep the
session ID and lease proofs returned by the server in agent state.

1. Read project state, task ownership, applicable contracts and recent events.
2. Create/claim the assigned task and declare its resource scope.
3. Acquire an exclusive lease for each modified resource, using the task ID.
4. Use `workspace.read_file` to obtain content and its SHA-256 revision.
5. Use `workspace.controlled_write` with session, lease token, generation, workspace ID,
   expected hash, new hash and content. Contents travel only to the local bridge.
6. Call heartbeat every 20 seconds or less during active work. It renews active leases for
   60 seconds; expired sessions cannot be resurrected. Check messages/events at checkpoints.
7. Execute relevant local tests. Submit their command, exit code and result with lease proofs.
8. Complete reviewed work, publish decisions/completion messages and release the leases.

Dependency cycles and unauthorized ownership changes are rejected. Interface consumers must
approve the latest owner-approved contract before their work can be submitted/completed.
Test evidence is supplied by the client; the central server does not execute repository code.

## Five developers and remote deployment

Run one shared PostgreSQL server/backend behind HTTPS. Each developer has a separate checkout
and branch/worktree, their own expiring identity, local bridge and MCP processes. The project
owner provisions membership with `studiosync project join --project-id ID --user-id ID` after
the administrator provisions that developer's account. The join command is owner-side access
management; it does not let strangers self-enroll.

Each developer uses `studiosync init --server-url https://your-coordinator.example
--project-id ID --root PATH`. Only the central backend is shared over the network; bridges stay
on localhost. See [deployment](docs/deployment.md) for Docker, TLS, backups and team setup.

In Git + Rojo mode, use one controlled Rojo synchronization pipeline for the integration Studio
hierarchy. Local Studio projects may use isolated Rojo sessions. Do not run competing Rojo
sessions against a shared hierarchy. Team Create mode supports awareness/warnings only; keep
Script Sync and Rojo from writing the same subtree.

## Checks and demonstration

```powershell
python -m pytest -q
python -m ruff check apps packages examples scripts conftest.py
python scripts/fetch_tools.py
.\.tools\stylua\stylua.exe --check apps/roblox-plugin/src examples/sample-moba
.\.tools\luau\luau-compile.exe --null apps/roblox-plugin/src/Main.plugin.luau
.\.tools\luau\luau.exe examples/sample-moba/tests/contracts.luau
python examples/five-agent-simulation/run.py
```

Build the friends' Windows package using the same system Python:

```powershell
python scripts/build_windows.py
python scripts/windows_smoke.py
```

The Windows helper downloads official Luau/StyLua release binaries to `.tools/`. On other
systems install their official binaries and pass `--luau /path/to/luau` to the simulation.
The simulation uses isolated temporary workspaces, five developers, five bridges and five
real MCP clients; it runs actual Luau tests and writes `test-results/five-agent-report.json`.
Use `--keep` to retain its disposable database/logs/workspaces. It stops its processes afterward.

PostgreSQL concurrency checks require an isolated test database:

```powershell
$env:STUDIOSYNC_TEST_POSTGRES_URL = 'postgresql+psycopg://user:password@127.0.0.1:5432/studiosync_test'
python -m pytest -m postgres -q
```

**The PostgreSQL fixture drops/recreates coordination tables; never point it at a real project
database.** Windows can alternatively use `python scripts/postgres_test.py`, which downloads
an official supported portable archive and starts/stops its own isolated localhost cluster.
CI includes a dedicated PostgreSQL service and the five-agent Windows simulation.

`studiosync doctor` checks dependencies, database/server membership, workspace, Git, Rojo,
MCP executable, bridge and observed Studio polling. `studiosync integration` combines local
Git state with central task/interface/conflict eligibility. Opt into `.githooks/pre-commit`
using `git config core.hooksPath .githooks`. The hook detects merge conflicts, uncertain writes
and modified files reserved by other developers. It never stages, merges, resets or overwrites.

Additional documentation: [architecture](docs/architecture.md), [API](docs/api.md),
[guarded writes and security](docs/security.md), [troubleshooting](docs/troubleshooting.md).
