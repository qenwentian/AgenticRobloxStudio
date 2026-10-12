import hashlib
import json
import os
import subprocess
from pathlib import Path

from studiosync_shared.resources import canonical


def revision(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class Workspace:
    def __init__(self, root: str):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("Workspace root must be a directory")

    def path(self, resource: str) -> Path:
        key = canonical(resource)
        if not key.startswith("file:"):
            raise ValueError("Only file resources can be written or read")
        # Preserve real filesystem spelling while central keys conservatively case-fold.
        raw = resource.split(":", 1)[1].replace("\\", "/")
        components = [p for p in raw.split("/") if p not in {"", "."}]
        path = self.root
        for component in components:
            if path.is_dir():
                matches = [p for p in path.iterdir() if p.name.casefold() == component.casefold()]
                if len(matches) > 1:
                    raise ValueError("Ambiguous case aliases in workspace")
                path = matches[0] if matches else path / component
            else:
                path = path / component
            # Reject symlinks/reparse points, including in-workspace aliases.
            if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                raise ValueError("Symlink and junction file access is forbidden")
            resolved = path.resolve()
            if not resolved.is_relative_to(self.root):
                raise ValueError("File escapes workspace boundary")
        if path.is_dir():
            raise ValueError("Expected a file, received a directory")
        return path

    def bytes(self, path: Path) -> bytes:
        if not path.exists():
            return b""
        if path.stat().st_size > 1_000_000:
            raise ValueError("File exceeds controlled editing size limit")
        return path.read_bytes()

    def git(self) -> dict:
        def run(*args):
            result = subprocess.run(
                ["git", "-C", str(self.root), *args],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if result.returncode:
                raise ValueError("Workspace is not a readable Git repository")
            return result.stdout.strip()

        try:
            status = run("status", "--porcelain=v1", "-z")
            records = status.split("\x00")
            files = []
            idx = 0
            while idx < len(records):
                entry = records[idx]
                idx += 1
                if len(entry) < 4:
                    continue
                code, path = entry[:2], entry[3:]
                files.append(
                    {
                        "status": code,
                        "path": path,
                        "merge_conflict": code in {"DD", "AU", "UD", "UA", "DU", "AA", "UU"},
                    }
                )
                if "R" in code or "C" in code:
                    idx += 1
            return {
                "available": True,
                "root": run("rev-parse", "--show-toplevel"),
                "branch": run("branch", "--show-current"),
                "commit": run("rev-parse", "HEAD"),
                "files": files,
                "merge_conflicts": [f["path"] for f in files if f["merge_conflict"]],
            }
        except (ValueError, FileNotFoundError, subprocess.TimeoutExpired):
            return {
                "available": False,
                "message": "Initialize Git and create an initial commit to inspect repository state",
            }

    def rojo(self) -> dict:
        paths = sorted(self.root.glob("*.project.json"))
        mappings = []
        errors = []

        def visit(node, name, base):
            if not isinstance(node, dict):
                return
            if "$path" in node:
                source = (base / node["$path"]).resolve()
                if not source.is_relative_to(self.root):
                    raise ValueError("Rojo mapping escapes registered workspace")
                mappings.append(
                    {
                        "instance": name,
                        "path": source.relative_to(self.root).as_posix(),
                        "kind": "directory" if source.is_dir() else "file",
                    }
                )
            for key, value in node.items():
                if not key.startswith("$"):
                    visit(value, name + "." + key if name else key, base)

        for project in paths:
            try:
                data = json.loads(project.read_text(encoding="utf-8"))
                visit(data.get("tree", {}), "", project.parent)
            except (ValueError, OSError, TypeError) as exc:
                errors.append({"project": project.name, "error": str(exc)})
        return {
            "projects": [p.name for p in paths],
            "mappings": mappings,
            "errors": errors,
            "note": "Directory mappings are roots, not proof of exact live Studio instance identity",
        }


def atomic_replace(path: Path, content: bytes):
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".studiosync-write-", dir=path.parent)
    try:
        if path.exists():
            os.chmod(temporary, path.stat().st_mode)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if descriptor != -1:
            os.close(descriptor)
        if os.path.exists(temporary):
            os.unlink(temporary)
