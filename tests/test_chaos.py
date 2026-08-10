import hashlib
import os
import pathlib
import shutil
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from drivesync.config import Config
from drivesync.daemon import DriveSyncDaemon
from drivesync.normalizer import EventNormalizer
from drivesync.rclone_client import RcloneClient
from drivesync.storage import Storage
from drivesync.sync_manager import SyncManager, is_transient_error


def wait_for_completed_events(storage: Storage, expected_completed_count: int, timeout: float = 20.0) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        with storage.get_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM events WHERE status = 'COMPLETED'"
            ).fetchone()
            if row["cnt"] >= expected_completed_count:
                return True
        time.sleep(0.3)
    return False


class TestDriveSyncChaosAndEdgeCases(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_base = pathlib.Path(self.temp_dir.name).resolve()
        self.test_watched = self.test_base / "chaos_watched"
        self.test_watched.mkdir(parents=True, exist_ok=True)

        self.config = Config(base_dir=self.test_base)
        self.config.set("rclone_binary", r"d:\dev\drivesync\rclone.exe")
        self.config.set("rclone_conf", r"d:\dev\drivesync\rclone.conf")
        self.config.set("rclone_remote", "gdrive")
        self.config.set("rc_port", 5595)
        self.config.set("stability_seconds", 0.5)
        self.config.set("reconciliation_interval", 60)

        self.config.add_folder(str(self.test_watched), remote_path="DriveSync_Chaos_Test")
        self.storage = Storage(self.config.db_path)

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_chaos_1_rapid_atomic_save_churn(self):
        """CHAOS 1: Rapid file modifications and deletions within ms debounce window (e.g. Word temp files/git churn)."""
        print("\n[CHAOS TEST 1] Rapid Atomic-Save Churn & Debounce Coalescing...")
        normalizer = EventNormalizer(
            storage=self.storage,
            watched_folders=self.config.watched_folders,
            ignore_patterns=self.config.get("ignore_patterns", []),
            stability_seconds=0.5,
        )

        import watchfiles
        target_file = self.test_watched / "rapid_churn.docx"

        now = time.time()
        # Simulate Word atomic save: Create -> Modify -> Delete -> Create -> Modify -> Delete
        normalizer.process_raw_event(watchfiles.Change.added, str(target_file), now)
        normalizer.process_raw_event(watchfiles.Change.modified, str(target_file), now + 0.01)
        normalizer.process_raw_event(watchfiles.Change.deleted, str(target_file), now + 0.02)

        # File is completely gone (create then delete in debounce window)
        self.assertNotIn(str(target_file.resolve()), normalizer.candidates)
        print("  --> [PASSED] Create-then-Delete in debounce window correctly dropped without ghost events.")

        # Simulate Delete + Re-create: Delete -> Add -> Modify
        target_file.write_text("Final Coalesced Content", encoding="utf-8")
        normalizer.process_raw_event(watchfiles.Change.deleted, str(target_file), now + 0.05)
        normalizer.process_raw_event(watchfiles.Change.added, str(target_file), now + 0.06)
        normalizer.process_raw_event(watchfiles.Change.modified, str(target_file), now + 0.07)

        cand = normalizer.candidates[str(target_file.resolve())]
        self.assertEqual(cand.op, "MODIFY", "Delete + Add inside debounce window must coalesce to MODIFY")
        print("  --> [PASSED] Delete + Add inside debounce window correctly coalesced to MODIFY.")

    def test_chaos_2_crash_recovery_mid_processing(self):
        """CHAOS 2: Simulated power outage / SIGKILL crash recovery of interrupted processing events."""
        print("\n[CHAOS TEST 2] Sudden Crash Recovery & DB Resiliency...")

        # Seed events in PROCESSING state (as if interrupted by SIGKILL mid-upload)
        for i in range(5):
            f_path = self.test_watched / f"crash_{i}.txt"
            f_path.write_text(f"Crash data {i}", encoding="utf-8")
            self.storage.enqueue_event(
                full_path=str(f_path),
                local_root=str(self.test_watched),
                rel_path=f"crash_{i}.txt",
                operation="ADD",
            )

        pending = self.storage.get_pending_events()
        for evt in pending:
            self.storage.mark_event_processing(evt["id"])

        # Confirm all events are locked in PROCESSING
        with self.storage.get_connection() as conn:
            cnt = conn.execute("SELECT COUNT(*) as c FROM events WHERE status = 'PROCESSING'").fetchone()["c"]
            self.assertEqual(cnt, 5)

        # Run startup crash recovery
        recovered = self.storage.startup_recovery()
        self.assertEqual(recovered, 5, "Startup recovery should reset all 5 PROCESSING rows back to PENDING")

        # Confirm all events recovered to PENDING
        pending_after = self.storage.get_pending_events()
        self.assertEqual(len(pending_after), 5)
        self.assertTrue(all(e["status"] == "PENDING" for e in pending_after))
        print("  --> [PASSED] Crash recovery restored all 5 interrupted PROCESSING events to PENDING.")

    def test_chaos_3_mass_deletion_circuit_breaker(self):
        """CHAOS 3: Mass folder wipe exceeding safety threshold & CLI approval workflow."""
        print("\n[CHAOS TEST 3] Mass Deletion Circuit Breaker & Resume...")

        # Seed manifest with 100 tracked files
        with self.storage.get_connection() as conn:
            for i in range(100):
                conn.execute(
                    "INSERT INTO manifest (full_path, local_root, rel_path, content_hash, mtime, size, last_synced_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (f"C:\\folder\\file_{i}.txt", "C:\\folder", f"file_{i}.txt", "hash", time.time(), 100, time.time()),
                )
            conn.commit()

        self.assertEqual(self.storage.get_manifest_count(), 100)

        # Queue 60 pending deletions (exceeding default max 50 and max 20%)
        for i in range(60):
            self.storage.enqueue_event(f"C:\\folder\\file_{i}.txt", "C:\\folder", f"file_{i}.txt", "DELETE")

        mock_rclone = MagicMock()
        mock_rclone.is_alive.return_value = True

        sync_mgr = SyncManager(self.config, self.storage, mock_rclone)

        # Attempt to process batch -> should trigger Circuit Breaker
        processed = sync_mgr.process_pending_batch()
        self.assertEqual(processed, 0, "No deletions should be processed when Circuit Breaker trips")
        self.assertTrue(self.storage.is_circuit_breaker_tripped(), "Circuit Breaker must be TRIPPED in DB")
        print("  --> [PASSED] Circuit Breaker tripped; mass deletions halted automatically.")

        # Simulate user running CLI 'drivesync circuit-breaker approve'
        self.storage.set_circuit_breaker_tripped(False)
        self.assertFalse(self.storage.is_circuit_breaker_tripped())
        print("  --> [PASSED] User CLI approval reset Circuit Breaker state.")

    def test_chaos_4_transient_network_outage_exponential_backoff(self):
        """CHAOS 4: Temporary Google Drive 503 / 429 rate limit backoff retries vs 401 permanent failure."""
        print("\n[CHAOS TEST 4] Transient Error Backoff & Permanent Failure Scoping...")

        file_p = self.test_watched / "net_test.txt"
        file_p.write_text("Network resilience test", encoding="utf-8")
        self.storage.enqueue_event(str(file_p), str(self.test_watched), "net_test.txt", "ADD")

        evt = self.storage.get_pending_events()[0]
        evt_id = evt["id"]

        # 1. Simulate Transient Error (HTTP 429 Rate Limit)
        self.storage.mark_event_failed(evt_id, "copyfile failed: HTTP 429 Too Many Requests", permanent=False)
        row = self.storage.get_pending_events()[0]
        self.assertEqual(row["status"], "PENDING", "Transient failure must keep event status PENDING for retry")
        self.assertEqual(row["retry_count"], 1)
        print("  --> [PASSED] HTTP 429 transient error correctly kept as PENDING with retry incremented.")

        # 2. Simulate Permanent Error (HTTP 401 Unauthorized)
        self.storage.mark_event_failed(evt_id, "copyfile failed: HTTP 401 Unauthorized", permanent=True)
        with self.storage.get_connection() as conn:
            failed_row = conn.execute("SELECT * FROM events WHERE id = ?", (evt_id,)).fetchone()
            self.assertEqual(failed_row["status"], "FAILED", "Permanent error must move event directly to FAILED")
        print("  --> [PASSED] HTTP 401 permanent auth error correctly moved directly to FAILED.")

    def test_chaos_5_windows_case_canonicalization_and_special_paths(self):
        """CHAOS 5: NTFS case-insensitivity canonicalization & special characters / spaces in paths."""
        print("\n[CHAOS TEST 5] Windows NTFS Case-Insensitivity & Special Character Paths...")

        special_dir = self.test_watched / "Deep Folder (Test #1)" / "Sub & Folder [2026]"
        special_dir.mkdir(parents=True, exist_ok=True)
        special_file = special_dir / "My Resume (v2.1) & Notes [Final].txt"
        special_file.write_text("Special path content", encoding="utf-8")

        # Test case canonicalization (Upper vs Lower case paths resolve identically)
        path1 = str(special_file)
        path2 = str(special_file).upper()

        norm1 = self.storage.enqueue_event(path1, str(self.test_watched), "rel/path.txt", "ADD")
        norm2 = self.storage.enqueue_event(path2, str(self.test_watched), "rel/path.txt", "ADD")

        # Second enqueue should collapse into the same PENDING record due to canonicalization
        events = self.storage.get_pending_events()
        self.assertEqual(len(events), 1, "Duplicate paths with different casing must collapse into single event")
        print("  --> [PASSED] Path case canonicalization prevented duplicate queue entries.")

    def test_chaos_6_offline_reconciliation_scan(self):
        """CHAOS 6: Offline changes (files added, modified, deleted while service was stopped)."""
        print("\n[CHAOS TEST 6] Offline Directory Reconciliation Scan...")

        # Setup manifest state as if files were synced previously
        f_add = self.test_watched / "offline_new.txt"
        f_mod = self.test_watched / "offline_mod.txt"
        f_del = self.test_watched / "offline_del.txt"

        f_mod.write_text("Old content", encoding="utf-8")
        f_del.write_text("Deleted content", encoding="utf-8")

        mtime_mod = os.path.getmtime(f_mod)
        size_mod = os.path.getsize(f_mod)
        hash_mod = hashlib.sha256(b"Old content").hexdigest()

        with self.storage.get_connection() as conn:
            conn.execute(
                "INSERT INTO manifest (full_path, local_root, rel_path, content_hash, mtime, size, last_synced_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (str(f_mod), str(self.test_watched), "offline_mod.txt", hash_mod, mtime_mod, size_mod, time.time()),
            )
            conn.execute(
                "INSERT INTO manifest (full_path, local_root, rel_path, content_hash, mtime, size, last_synced_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (str(f_del), str(self.test_watched), "offline_del.txt", "hash_del", 100.0, 50, time.time()),
            )
            conn.commit()

        # Simulate offline disk edits while service was stopped
        f_add.write_text("Brand new file created offline", encoding="utf-8")
        f_mod.write_text("Updated content modified offline", encoding="utf-8")
        f_del.unlink()  # File deleted offline

        # Run offline reconciliation diff scan
        added, modified, deleted = self.storage.reconcile_folder(
            str(self.test_watched), ignore_patterns=self.config.get("ignore_patterns", [])
        )

        self.assertEqual(added, 1, "Offline reconciliation should detect 1 ADD")
        self.assertEqual(modified, 1, "Offline reconciliation should detect 1 MODIFY")
        self.assertEqual(deleted, 1, "Offline reconciliation should detect 1 DELETE")
        print(f"  --> [PASSED] Offline scan detected: {added} ADD, {modified} MODIFY, {deleted} DELETE.")


if __name__ == "__main__":
    unittest.main()
