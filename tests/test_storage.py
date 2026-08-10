import os
import pathlib
import tempfile
import time
import unittest

from drivesync.storage import Storage


class TestStorage(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp_dir.name) / "test.db"
        self.storage = Storage(self.db_path)

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_wal_journal_mode(self):
        with self.storage.get_connection() as conn:
            row = conn.execute("PRAGMA journal_mode;").fetchone()
            self.assertEqual(row[0].lower(), "wal")

    def test_enqueue_and_dedup(self):
        file_path = pathlib.Path(self.temp_dir.name) / "sample.txt"
        file_path.write_text("Hello World", encoding="utf-8")

        enqueued = self.storage.enqueue_event(
            full_path=str(file_path),
            local_root=self.temp_dir.name,
            rel_path="sample.txt",
            operation="ADD",
            mtime=os.path.getmtime(file_path),
            size=os.path.getsize(file_path),
        )
        self.assertTrue(enqueued)

        events = self.storage.get_pending_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["operation"], "ADD")

    def test_startup_recovery(self):
        file_path = pathlib.Path(self.temp_dir.name) / "crash.txt"
        file_path.write_text("data", encoding="utf-8")

        self.storage.enqueue_event(
            full_path=str(file_path),
            local_root=self.temp_dir.name,
            rel_path="crash.txt",
            operation="MODIFY",
        )
        events = self.storage.get_pending_events()
        evt_id = events[0]["id"]

        self.storage.mark_event_processing(evt_id)
        # Process left in PROCESSING state from a prior crash
        recovered = self.storage.startup_recovery()
        self.assertEqual(recovered, 1)

        pending = self.storage.get_pending_events()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["status"], "PENDING")

    def test_reconciliation_scan(self):
        test_folder = pathlib.Path(self.temp_dir.name) / "watched"
        test_folder.mkdir()
        file1 = test_folder / "file1.txt"
        file1.write_text("content 1", encoding="utf-8")

        added, modified, deleted = self.storage.reconcile_folder(
            str(test_folder), ignore_patterns=[]
        )
        self.assertEqual(added, 1)
        self.assertEqual(modified, 0)
        self.assertEqual(deleted, 0)


if __name__ == "__main__":
    unittest.main()
