"""Private, bounded SQLite history shared by the worker and local dashboard."""
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class History:
    def __init__(self, directory, limit=500, photo_budget=200_000_000):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.directory.chmod(0o700)
        self.photos = self.directory / "photos"
        self.photos.mkdir(mode=0o700, exist_ok=True)
        self.lock = threading.RLock()
        self.limit = max(1, int(limit))
        self.photo_budget = max(0, int(photo_budget))
        self.started = time.monotonic()
        self.runtime = {"stage": "starting", "detail": "Starting the assistant", "updated_at": timestamp()}
        self.db = sqlite3.connect(self.directory / "history.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
                source TEXT NOT NULL, command TEXT NOT NULL, status TEXT NOT NULL,
                response TEXT, error TEXT, photo INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT,
                created_at TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS events_run ON events(run_id,id);
        """)
        self.db.execute("UPDATE runs SET status='interrupted',finished_at=? WHERE finished_at IS NULL", (timestamp(),))
        self.db.commit()
        os.chmod(self.directory / "history.sqlite3", 0o600)
        self.prune()

    def close(self):
        with self.lock:
            self.db.close()

    def state(self, stage, detail):
        with self.lock:
            self.runtime = {"stage": stage, "detail": detail, "updated_at": timestamp()}

    def start(self, command, source="voice", status="running"):
        run_id = str(uuid.uuid4())
        with self.lock, self.db:
            self.db.execute("INSERT INTO runs(id,started_at,source,command,status) VALUES(?,?,?,?,?)",
                            (run_id, timestamp(), source, command[:200], status))
            self._event(run_id, "user", command[:200])
        return run_id

    def _event(self, run_id, kind, text):
        self.db.execute("INSERT INTO events(run_id,created_at,kind,text) VALUES(?,?,?,?)",
                        (run_id, timestamp(), kind, str(text)[:3000]))

    def event(self, run_id, kind, text):
        with self.lock, self.db:
            self._event(run_id, kind, text)
            # Keep system events bounded; per-command events are removed with their run.
            self.db.execute("DELETE FROM events WHERE run_id IS NULL AND id NOT IN "
                            "(SELECT id FROM events WHERE run_id IS NULL ORDER BY id DESC LIMIT 300)")

    def stage(self, run_id, stage, detail):
        with self.lock, self.db:
            self.db.execute("UPDATE runs SET status=? WHERE id=?", (stage, run_id))
            self._event(run_id, "stage", detail)
            self.state(stage, detail)

    def photo_path(self, run_id):
        # Canonical UUID filenames only, never a path supplied by a browser.
        return self.photos / (str(uuid.UUID(str(run_id))) + ".jpg")

    def save_photo(self, run_id, source):
        target = self.photo_path(run_id)
        data = Path(source).read_bytes()
        if len(data) > 4_000_000 or not data.startswith(b"\xff\xd8\xff"):
            raise ValueError("Invalid camera photograph")
        temporary = target.with_suffix(".part")
        with temporary.open("wb") as output:
            output.write(data)
        temporary.chmod(0o600)
        temporary.replace(target)
        with self.lock, self.db:
            self.db.execute("UPDATE runs SET photo=1 WHERE id=?", (run_id,))

    def finish(self, run_id, response=None, error=None):
        with self.lock, self.db:
            self.db.execute("UPDATE runs SET status=?,finished_at=?,response=?,error=? WHERE id=?",
                            ("failed" if error else "done", timestamp(), response, error, run_id))
        self.prune()

    def get(self, run_id):
        with self.lock:
            row = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["events"] = [dict(e) for e in self.db.execute("SELECT * FROM events WHERE run_id=? ORDER BY id", (run_id,))]
            return result

    def last_description(self):
        with self.lock:
            row = self.db.execute("SELECT response FROM runs WHERE response IS NOT NULL AND photo=1 ORDER BY rowid DESC LIMIT 1").fetchone()
            return row[0] if row else None

    def snapshot(self, before=None):
        with self.lock:
            if before:
                runs = self.db.execute("SELECT rowid AS cursor,* FROM runs WHERE rowid<? ORDER BY rowid DESC LIMIT 50", (int(before),))
            else:
                runs = self.db.execute("SELECT rowid AS cursor,* FROM runs ORDER BY rowid DESC LIMIT 50")
            items = [dict(row) for row in runs]
            counts = dict(self.db.execute("SELECT status,count(*) FROM runs GROUP BY status").fetchall())
            events = [dict(row) for row in self.db.execute("SELECT * FROM events ORDER BY id DESC LIMIT 60")]
            active = self.db.execute("SELECT id,status FROM runs WHERE finished_at IS NULL ORDER BY (status='queued'),rowid LIMIT 1").fetchone()
            return {"runtime": dict(self.runtime), "runs": items, "counts": counts, "events": events,
                    "active": dict(active) if active else None,
                    "uptime_seconds": round(time.monotonic() - self.started),
                    "retention": {"sessions": self.limit, "photo_bytes": self.photo_budget},
                    "next_cursor": items[-1]["cursor"] if len(items) == 50 else None}

    def delete(self, run_id):
        with self.lock, self.db:
            row = self.db.execute("SELECT finished_at FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                return False
            if row[0] is None:
                raise ValueError("A running session cannot be deleted.")
            self.db.execute("DELETE FROM runs WHERE id=?", (run_id,))
            self.photo_path(run_id).unlink(missing_ok=True)
            return True

    def prune(self):
        with self.lock, self.db:
            old = self.db.execute("SELECT id FROM runs WHERE finished_at IS NOT NULL ORDER BY rowid DESC LIMIT -1 OFFSET ?", (self.limit,)).fetchall()
            for row in old:
                self.photo_path(row[0]).unlink(missing_ok=True)
                self.db.execute("DELETE FROM runs WHERE id=?", (row[0],))
            remaining = self.db.execute("SELECT id FROM runs WHERE photo=1 ORDER BY rowid DESC").fetchall()
            used = 0
            keep = set()
            for row in remaining:
                path = self.photo_path(row[0])
                size = path.stat().st_size if path.exists() else 0
                if size and used + size <= self.photo_budget:
                    used += size
                    keep.add(path.name)
                else:
                    path.unlink(missing_ok=True)
                    self.db.execute("UPDATE runs SET photo=0 WHERE id=?", (row[0],))
            # Remove files left by an interrupted transaction or forced power loss.
            for path in self.photos.iterdir():
                if path.name not in keep:
                    path.unlink(missing_ok=True)
