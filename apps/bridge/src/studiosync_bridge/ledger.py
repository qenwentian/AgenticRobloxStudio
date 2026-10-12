"""Durable local outbox and recovery journal, never authoritative coordination state."""

import json
import sqlite3
import time
from pathlib import Path


class Ledger:
    def __init__(self, root: Path):
        folder = root / ".studiosync"
        folder.mkdir(exist_ok=True)
        self.path = folder / "bridge.db"
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS writes (permit TEXT PRIMARY KEY, resource TEXT, revision TEXT, status TEXT, seen INTEGER DEFAULT 0)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS outbox (id INTEGER PRIMARY KEY, payload TEXT, created REAL)"
            )

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def write(self, permit, resource, revision, status):
        with self.connect() as db:
            db.execute(
                "INSERT INTO writes(permit,resource,revision,status) VALUES(?,?,?,?) ON CONFLICT(permit) DO UPDATE SET status=excluded.status",
                (permit, resource, revision, status),
            )

    def controlled(self, resource, revision):
        with self.connect() as db:
            row = db.execute(
                "SELECT permit FROM writes WHERE resource=? AND revision=? AND seen=0 AND status IN ('PENDING','COMMITTED') ORDER BY rowid DESC LIMIT 1",
                (resource, revision),
            ).fetchone()
            if row:
                db.execute("UPDATE writes SET seen=1 WHERE permit=?", row)
                return True
            return False

    def pending(self):
        with self.connect() as db:
            return [
                {"permit_id": p, "resource": r, "revision": h, "status": s}
                for p, r, h, s in db.execute(
                    "SELECT permit,resource,revision,status FROM writes WHERE status='PENDING'"
                )
            ]

    def queue(self, payload):
        with self.connect() as db:
            if db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] >= 10000:
                raise ValueError("Local outbox full; restore central connection before continuing")
            db.execute(
                "INSERT INTO outbox(payload,created) VALUES(?,?)",
                (json.dumps(payload), time.time()),
            )

    def flush(self, backend):
        with self.connect() as db:
            for row_id, payload in db.execute(
                "SELECT id,payload FROM outbox ORDER BY id LIMIT 100"
            ).fetchall():
                result = backend.action("report_conflict", json.loads(payload))
                if not result.ok:
                    break
                db.execute("DELETE FROM outbox WHERE id=?", (row_id,))
            db.execute(
                "DELETE FROM writes WHERE status != 'PENDING' AND rowid NOT IN (SELECT rowid FROM writes ORDER BY rowid DESC LIMIT 1000)"
            )

    def queue_size(self):
        with self.connect() as db:
            return db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
