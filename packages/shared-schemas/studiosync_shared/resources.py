import re
import unicodedata


def canonical(value: str) -> str:
    kind, separator, path = value.partition(":")
    kind = kind.lower()
    path = unicodedata.normalize("NFC", path)
    if not separator or kind not in {
        "file",
        "directory",
        "instance",
        "subtree",
        "interface",
        "system",
    }:
        raise ValueError("Resource must have a supported category prefix")
    if not path or any(ord(char) < 32 for char in path):
        raise ValueError("Empty or control-character resource")
    if kind in {"file", "directory"}:
        path = path.replace("\\", "/")
        if path.startswith("/") or ":" in path:
            raise ValueError("Absolute paths and alternate streams are forbidden")
        parts = [p for p in path.split("/") if p not in {"", "."}]
        for part in parts:
            if part == ".." or part.endswith((".", " ")) or any(c in part for c in '<>"|?*'):
                raise ValueError("Traversal or ambiguous Windows path")
            if re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?", part):
                raise ValueError("Windows device paths are forbidden")
        if not parts or any(p.casefold() in {".git", ".studiosync", ".venv"} for p in parts):
            raise ValueError("Protected metadata path")
        path = "/".join(parts).casefold()
    elif kind in {"instance", "subtree"}:
        if path.startswith("game."):
            path = path[5:]
        if not path or any(not p for p in path.split(".")):
            raise ValueError("Ambiguous Studio display path")
    else:
        path = path.casefold()
    return kind + ":" + path


def overlaps(left: str, right: str) -> bool:
    lk, lp = left.split(":", 1)
    rk, rp = right.split(":", 1)
    if lk in {"file", "directory"} and rk in {"file", "directory"}:
        return (
            lp == rp
            or (lk == "directory" and rp.startswith(lp + "/"))
            or (rk == "directory" and lp.startswith(rp + "/"))
        )
    if lk in {"instance", "subtree"} and rk in {"instance", "subtree"}:
        return (
            lp == rp
            or (lk == "subtree" and rp.startswith(lp + "."))
            or (rk == "subtree" and lp.startswith(rp + "."))
        )
    return left == right


def covers(reservation: str, resource: str) -> bool:
    kind, path = reservation.split(":", 1)
    rk, rp = resource.split(":", 1)
    if kind in {"file", "directory"} and rk in {"file", "directory"}:
        return path == rp or (kind == "directory" and rp.startswith(path + "/"))
    if kind in {"instance", "subtree"} and rk in {"instance", "subtree"}:
        return path == rp or (kind == "subtree" and rp.startswith(path + "."))
    return reservation == resource
