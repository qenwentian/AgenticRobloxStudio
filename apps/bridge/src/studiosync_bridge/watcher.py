import asyncio
from pathlib import Path

from studiosync_shared.resources import canonical, overlaps
from watchfiles import awatch

from .workspace import revision


def relevant(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    ignored = {
        ".git",
        ".studiosync",
        ".venv",
        "node_modules",
        "build",
        "dist",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
    }
    return (
        not any(p.casefold() in ignored for p in relative.parts)
        and not path.name.startswith((".env", ".studiosync", ".~"))
        and path.suffix not in {".tmp", ".swp", ".pyc"}
    )


class Watcher:
    def __init__(self, workspace, backend, ledger, config):
        self.workspace, self.backend, self.ledger, self.config = workspace, backend, ledger, config
        self.health = {"running": False, "last_error": None}

    def observe(self, paths):
        state = self.backend.action("list_locks", {"limit": 200})
        locks = state.data.get("locks", []) if state.ok else []
        for path in sorted(set(paths)):
            path = Path(path)
            if not relevant(path, self.workspace.root):
                continue
            resource = canonical("file:" + path.relative_to(self.workspace.root).as_posix())
            try:
                safe = self.workspace.path(resource)
                content_revision = revision(self.workspace.bytes(safe))
            except (OSError, ValueError):
                continue
            if self.ledger.controlled(resource, content_revision):
                continue
            affected = [
                lease
                for lease in locks
                if lease["mode"] == "WRITE" and overlaps(lease["resource"], resource)
            ]
            self.ledger.queue(
                {
                    "category": "OUT_OF_BAND",
                    "resources": [resource],
                    "agents": list({lease["agent_id"] for lease in affected}),
                    "description": f"Uncoordinated filesystem change in workspace {self.config.workspace_id}; hash {content_revision}. Editor identity is unknown.",
                }
            )
        self.ledger.flush(self.backend)

    async def run(self):
        self.health["running"] = True
        try:
            async for changes in awatch(
                self.workspace.root,
                debounce=500,
                step=100,
                watch_filter=lambda _, p: relevant(Path(p), self.workspace.root),
            ):
                try:
                    await asyncio.to_thread(self.observe, [p for _, p in changes])
                    self.health["last_error"] = None
                except (OSError, ValueError) as exc:
                    self.health["last_error"] = str(exc)
        finally:
            self.health["running"] = False
