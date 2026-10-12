"""Build a self-contained client using the selected system Python, without creating a venv."""

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    if sys.platform != "win32":
        raise SystemExit("Build the Windows executable on Windows")
    args = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--console",
        "--name",
        "StudioSyncAI",
        "--distpath",
        str(ROOT / "dist"),
        "--workpath",
        str(ROOT / "build/windows"),
        "--specpath",
        str(ROOT / "build"),
        "--add-data",
        str(ROOT / "build/StudioSyncAI.rbxmx") + ":.",
        "--collect-submodules",
        "uvicorn",
        "--copy-metadata",
        "mcp",
    ]
    for directory in ["apps/bridge/src", "apps/mcp/src", "packages/shared-schemas"]:
        args += ["--paths", str(ROOT / directory)]
    for module in [
        "torch",
        "torchvision",
        "tensorflow",
        "keras",
        "numpy",
        "pandas",
        "matplotlib",
        "PySide6",
        "IPython",
        "pytest",
        "scipy",
        "cv2",
        "PIL",
        "tkinter",
    ]:
        args += ["--exclude-module", module]
    args.append(str(ROOT / "scripts/windows_entry.py"))
    with (ROOT / "build/windows-build.log").open("w", encoding="utf-8") as log:
        subprocess.run(args, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)
    exe = ROOT / "dist/StudioSyncAI.exe"
    result = subprocess.run([str(exe), "--self-test"], capture_output=True, text=True, check=True)
    print(result.stdout, end="")
    files = [
        (exe, "StudioSyncAI.exe"),
        (ROOT / "build/StudioSyncAI.rbxmx", "StudioSyncAI.rbxmx"),
        (ROOT / "docs/friends-quickstart.txt", "READ-ME-FIRST.txt"),
    ]
    hashes = {name: hashlib.sha256(path.read_bytes()).hexdigest() for path, name in files}
    with zipfile.ZipFile(
        ROOT / "dist/StudioSyncAI-Windows.zip", "w", zipfile.ZIP_DEFLATED
    ) as archive:
        for path, name in files:
            archive.write(path, name)
        archive.writestr("SHA256SUMS.json", json.dumps(hashes, indent=2))
    print("Created " + str(ROOT / "dist/StudioSyncAI-Windows.zip"))


if __name__ == "__main__":
    main()
