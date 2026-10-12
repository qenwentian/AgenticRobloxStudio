"""Download official release assets locally; does not install global tools."""

import hashlib
import io
import json
import platform
import urllib.request
import zipfile
from pathlib import Path


def fetch(repo, keyword, destination):
    headers = {"User-Agent": "StudioSync-tool-bootstrap"}
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/releases/latest", headers=headers
    )
    release = json.load(urllib.request.urlopen(request, timeout=30))
    asset = next(
        a for a in release["assets"] if keyword in a["name"] and a["name"].endswith(".zip")
    )
    data = urllib.request.urlopen(
        urllib.request.Request(asset["browser_download_url"], headers=headers), timeout=60
    ).read()
    expected = asset.get("digest")
    if expected and expected.startswith("sha256:"):
        assert hashlib.sha256(data).hexdigest() == expected[7:], "Release asset digest mismatch"
    root = destination.resolve()
    root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for member in archive.infolist():
            assert (root / member.filename).resolve().is_relative_to(root), "Unsafe archive path"
        archive.extractall(root)
    print(repo, release["tag_name"], "->", destination)


if __name__ == "__main__":
    if platform.system() != "Windows":
        raise SystemExit("This helper targets Windows; use official release tools for your OS")
    fetch("luau-lang/luau", "luau-windows", Path(".tools/luau"))
    fetch("JohnnyMorganz/StyLua", "windows-x86_64", Path(".tools/stylua"))
