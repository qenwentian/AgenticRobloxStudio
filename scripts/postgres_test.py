"""Optional Windows portable PostgreSQL test runner; creates a disposable localhost-only cluster."""

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    if os.name != "nt":
        raise SystemExit(
            "Use docker-compose.yml or GitHub Actions PostgreSQL service on other OSes"
        )
    tools = ROOT / ".tools/postgres-supported"
    binary = tools / "pgsql/bin/pg_ctl.exe"
    if "--links" in sys.argv:
        page = (
            urllib.request.urlopen(
                "https://www.enterprisedb.com/download-postgresql-binaries", timeout=30
            )
            .read()
            .decode()
        )
        (ROOT / ".tools/postgres-download-page.html").write_text(page, encoding="utf-8")
        print("Saved official download page")
        return
    if binary.exists():
        existing_version = subprocess.check_output(
            [str(binary), "--version"], text=True, creationflags=subprocess.CREATE_NO_WINDOW
        )
        if int(re.search(r"\d+", existing_version).group()) < 14:
            raise SystemExit(
                "Unsupported existing PostgreSQL tools; select a supported official archive"
            )
    if not binary.exists():
        page = (
            urllib.request.urlopen(
                "https://www.enterprisedb.com/download-postgresql-binaries", timeout=30
            )
            .read()
            .decode()
        )

        class Downloads(HTMLParser):
            def __init__(self):
                super().__init__()
                self.href = ""
                self.version = (0,)
                self.choices = []

            def handle_data(self, data):
                if re.fullmatch(r"\d+\.\d+", data.strip()):
                    self.version = tuple(map(int, data.strip().split(".")))

            def handle_starttag(self, tag, attrs):
                attributes = dict(attrs)
                if tag == "a":
                    self.href = attributes.get("href", "")
                if (
                    tag == "img"
                    and attributes.get("alt") == "Windows x86-64"
                    and self.version[0] >= 14
                ):
                    self.choices.append((self.version, self.href))

        downloads = Downloads()
        downloads.feed(page)
        if not downloads.choices:
            raise SystemExit(
                "No official Windows archive found; install PostgreSQL or use CI service"
            )
        url = max(downloads.choices)[1]
        tools.mkdir(parents=True, exist_ok=True)
        archive = tools / "postgres.zip"
        print("Downloading official portable archive: " + url, flush=True)
        urllib.request.urlretrieve(url, archive)
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                assert (tools / member.filename).resolve().is_relative_to(tools.resolve())
            bundle.extractall(tools)
        archive.unlink()
    folder = Path(tempfile.mkdtemp(prefix="test-cluster-", dir=tools))
    flags = subprocess.CREATE_NO_WINDOW

    def run(args):
        # pg_ctl's server child can inherit pipes; use files so communicate() cannot wait on it.
        output_path = folder.parent / (folder.name + "-" + args[0] + ".log")
        with output_path.open("w", encoding="utf-8") as stream:
            result = subprocess.run(
                [str(tools / "pgsql/bin" / args[0]), *args[1:]],
                stdin=subprocess.DEVNULL,
                stdout=stream,
                stderr=subprocess.STDOUT,
                creationflags=flags,
                timeout=60,
            )
        output = output_path.read_text(encoding="utf-8", errors="replace")
        if result.returncode:
            raise RuntimeError(output)
        return output

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    started = False
    try:
        run(
            [
                "initdb.exe",
                "-D",
                str(folder),
                "-U",
                "postgres",
                "-A",
                "trust",
                "--no-locale",
                "-E",
                "UTF8",
            ]
        )
        run(
            [
                "pg_ctl.exe",
                "-D",
                str(folder),
                "-l",
                str(folder / "server.log"),
                "-o",
                f"-h 127.0.0.1 -p {port}",
                "-w",
                "start",
            ]
        )
        started = True
        run(
            [
                "createdb.exe",
                "-h",
                "127.0.0.1",
                "-p",
                str(port),
                "-U",
                "postgres",
                "studiosync_test",
            ]
        )
        url = f"postgresql+psycopg://postgres@127.0.0.1:{port}/studiosync_test"
        env = {**os.environ, "STUDIOSYNC_TEST_POSTGRES_URL": url, "STUDIOSYNC_DATABASE_URL": url}
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env, check=True
        )
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "apps/server/tests", "-m", "postgres", "-q"],
            cwd=ROOT,
            env=env,
            creationflags=flags,
            capture_output=True,
            text=True,
        )
        print(result.stdout + result.stderr, flush=True)
        report = {
            "postgres": run(["postgres.exe", "--version"]).strip(),
            "exit_code": result.returncode,
            "scope": "PostgreSQL migrations and five-client transactional contention",
            "passed": result.returncode == 0,
        }
        target = ROOT / "test-results/postgres-report.json"
        target.parent.mkdir(exist_ok=True)
        target.write_text(json.dumps(report, indent=2), encoding="utf-8")
        raise SystemExit(result.returncode)
    finally:
        if started:
            run(["pg_ctl.exe", "-D", str(folder), "-m", "fast", "-w", "stop"])
        # Retain the isolated cluster and logs for review; no global services are installed.
        print("Disposable PostgreSQL cluster stopped; logs retained at " + str(folder))


if __name__ == "__main__":
    main()
