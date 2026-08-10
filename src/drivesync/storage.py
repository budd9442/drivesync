import contextlib
import hashlib
import os
import pathlib
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple

from drivesync.path_utils import get_relative_path, is_ignored, normalize_local_path


def compute_file_hash(file_path: str | pathlib.Path) -> Optional[str]:
    """Compute SHA-256 hash of a local file."""
    p = pathlib.Path(file_path)
    if not p.is_file():
        return None
    hasher = hashlib.sha256()
    try:
        with open(p, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        return hasher.hexdigest()
    except OSError:
        return None


class Storage:
    """SQLite WAL-mode persistence layer for events queue and sync manifest."""

    def __init__(self, db_path: str | pathlib.Path):
        self.db_path = str(pathlib.Path(db_path).resolve())
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.init_db()

    @contextlib.contextmanager
    def get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
            yield conn
        finally:
            conn.close()

    def init_db(self) -> None:
        with self.get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    full_path TEXT NOT NULL,
                    local_root TEXT NOT NULL,
                    rel_path TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    retry_count INTEGER DEFAULT 0,
                    max_retries INTEGER DEFAULT 5,
                    error_msg TEXT,
                    content_hash TEXT,
                    mtime REAL,
                    size INTEGER,
                    created_at REAL DEFAULT (strftime('%s', 'now')),
                    updated_at REAL DEFAULT (strftime('%s', 'now'))
                );
            """)

            conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_events_full_path_pending 
                ON events (full_path) WHERE status IN ('PENDING', 'PROCESSING');
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS manifest (
                    full_path TEXT PRIMARY KEY,
                    local_root TEXT NOT NULL,
                    rel_path TEXT NOT NULL,
                    content_hash TEXT,
                    mtime REAL,
                    size INTEGER,
                    remote_id TEXT,
                    last_synced_at REAL
                );
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS system_state (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
            """)
            conn.commit()

    def startup_recovery(self) -> int:
        """Reset any rows left in PROCESSING from a prior crash to PENDING or FAILED."""
        with self.get_connection() as conn:
            conn.execute("""
                UPDATE events 
                SET status = 'FAILED', 
                    error_msg = 'Interrupted by crash; retries exhausted',
                    updated_at = strftime('%s', 'now')
                WHERE status = 'PROCESSING' AND retry_count >= max_retries
            """)
            cursor = conn.execute("""
                UPDATE events 
                SET status = 'PENDING', 
                    updated_at = strftime('%s', 'now')
                WHERE status = 'PROCESSING' AND retry_count < max_retries
            """)
            conn.commit()
            return cursor.rowcount

    def enqueue_event(
        self,
        full_path: str,
        local_root: str,
        rel_path: str,
        operation: str,
        mtime: Optional[float] = None,
        size: Optional[int] = None,
        content_hash: Optional[str] = None,
    ) -> bool:
        norm_full = normalize_local_path(full_path)
        norm_root = normalize_local_path(local_root)

        if operation in ("ADD", "MODIFY"):
            if content_hash is None and os.path.isfile(norm_full):
                content_hash = compute_file_hash(norm_full)
            if mtime is None and os.path.exists(norm_full):
                mtime = os.path.getmtime(norm_full)
            if size is None and os.path.exists(norm_full):
                size = os.path.getsize(norm_full)

            manifest = self.get_manifest_entry(norm_full)
            if manifest and manifest["content_hash"] == content_hash and content_hash is not None:
                return False

        with self.get_connection() as conn:
            conn.execute("""
                INSERT INTO events (full_path, local_root, rel_path, operation, status, content_hash, mtime, size, updated_at)
                VALUES (?, ?, ?, ?, 'PENDING', ?, ?, ?, strftime('%s', 'now'))
                ON CONFLICT(full_path) WHERE status IN ('PENDING', 'PROCESSING') DO UPDATE SET
                    operation = excluded.operation,
                    status = 'PENDING',
                    content_hash = excluded.content_hash,
                    mtime = excluded.mtime,
                    size = excluded.size,
                    updated_at = strftime('%s', 'now')
            """, (norm_full, norm_root, rel_path, operation, content_hash, mtime, size))
            conn.commit()
            return True

    def get_pending_events(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM events 
                WHERE status = 'PENDING' 
                ORDER BY id ASC 
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def count_pending_deletions(self) -> int:
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as count FROM events WHERE status = 'PENDING' AND operation = 'DELETE'"
            ).fetchone()
            return row["count"] if row else 0

    def mark_event_processing(self, event_id: int) -> None:
        with self.get_connection() as conn:
            conn.execute(
                "UPDATE events SET status = 'PROCESSING', updated_at = strftime('%s', 'now') WHERE id = ?",
                (event_id,),
            )
            conn.commit()

    def mark_event_completed(self, event_id: int, remote_id: Optional[str] = None) -> None:
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
            if not row:
                return

            event = dict(row)
            now = time.time()

            conn.execute(
                "UPDATE events SET status = 'COMPLETED', updated_at = strftime('%s', 'now') WHERE id = ?",
                (event_id,),
            )

            if event["operation"] == "DELETE":
                conn.execute("DELETE FROM manifest WHERE full_path = ?", (event["full_path"],))
            else:
                conn.execute(
                    """
                    INSERT INTO manifest (full_path, local_root, rel_path, content_hash, mtime, size, remote_id, last_synced_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(full_path) DO UPDATE SET
                        content_hash = excluded.content_hash,
                        mtime = excluded.mtime,
                        size = excluded.size,
                        remote_id = COALESCE(excluded.remote_id, manifest.remote_id),
                        last_synced_at = excluded.last_synced_at
                    """,
                    (
                        event["full_path"],
                        event["local_root"],
                        event["rel_path"],
                        event["content_hash"],
                        event["mtime"],
                        event["size"],
                        remote_id,
                        now,
                    ),
                )
            conn.commit()

    def mark_event_failed(self, event_id: int, error_msg: str, permanent: bool = False) -> None:
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
            if not row:
                return

            event = dict(row)
            new_retry_count = event["retry_count"] + 1
            max_retries = event["max_retries"]

            if permanent or new_retry_count >= max_retries:
                conn.execute(
                    """
                    UPDATE events 
                    SET status = 'FAILED', retry_count = ?, error_msg = ?, updated_at = strftime('%s', 'now')
                    WHERE id = ?
                    """,
                    (new_retry_count, error_msg, event_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE events 
                    SET status = 'PENDING', retry_count = ?, error_msg = ?, updated_at = strftime('%s', 'now')
                    WHERE id = ?
                    """,
                    (new_retry_count, error_msg, event_id),
                )
            conn.commit()

    def get_manifest_entry(self, full_path: str) -> Optional[Dict[str, Any]]:
        norm_full = normalize_local_path(full_path)
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM manifest WHERE full_path = ?", (norm_full,)).fetchone()
            return dict(row) if row else None

    def get_manifest_count(self) -> int:
        with self.get_connection() as conn:
            row = conn.execute("SELECT COUNT(*) as count FROM manifest").fetchone()
            return row["count"] if row else 0

    def purge_completed_events(self, retention_days: int) -> int:
        cutoff = time.time() - (retention_days * 86400)
        with self.get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM events WHERE status = 'COMPLETED' AND updated_at < ?",
                (cutoff,),
            )
            conn.commit()
            return cursor.rowcount

    def reconcile_folder(
        self, local_root: str, ignore_patterns: List[str]
    ) -> Tuple[int, int, int]:
        norm_root = normalize_local_path(local_root)
        if not os.path.exists(norm_root):
            return (0, 0, 0)

        disk_files: Dict[str, Tuple[float, int]] = {}
        for root, dirs, files in os.walk(norm_root):
            for file_name in files:
                full_p = os.path.join(root, file_name)
                norm_p = normalize_local_path(full_p)
                if is_ignored(norm_p, ignore_patterns):
                    continue
                try:
                    stat = os.stat(norm_p)
                    disk_files[norm_p] = (stat.st_mtime, stat.st_size)
                except OSError:
                    continue

        added, modified, deleted = 0, 0, 0

        with self.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM manifest WHERE local_root = ?", (norm_root,)
            ).fetchall()
            manifest_map = {r["full_path"]: dict(r) for r in rows}

        for full_p, (mtime, size) in disk_files.items():
            rel_p = get_relative_path(full_p, norm_root)
            if not rel_p:
                continue

            if full_p not in manifest_map:
                if self.enqueue_event(full_p, norm_root, rel_p, "ADD", mtime=mtime, size=size):
                    added += 1
            else:
                m = manifest_map[full_p]
                if abs((m["mtime"] or 0) - mtime) > 1.0 or m["size"] != size:
                    chash = compute_file_hash(full_p)
                    if chash != m["content_hash"]:
                        if self.enqueue_event(
                            full_p, norm_root, rel_p, "MODIFY", mtime=mtime, size=size, content_hash=chash
                        ):
                            modified += 1

        for full_p, m in manifest_map.items():
            if full_p not in disk_files and not os.path.exists(full_p):
                rel_p = m["rel_path"]
                if self.enqueue_event(full_p, norm_root, rel_p, "DELETE"):
                    deleted += 1

        return (added, modified, deleted)

    def is_circuit_breaker_tripped(self) -> bool:
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT value FROM system_state WHERE key = 'circuit_breaker_tripped'"
            ).fetchone()
            return row["value"] == "true" if row else False

    def set_circuit_breaker_tripped(self, tripped: bool) -> None:
        with self.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO system_state (key, value) VALUES ('circuit_breaker_tripped', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                ("true" if tripped else "false",),
            )
            conn.commit()
