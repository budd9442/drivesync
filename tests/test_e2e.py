import os
import pathlib
import shutil
import tempfile
import time
import unittest

from drivesync.config import Config
from drivesync.daemon import DriveSyncDaemon
from drivesync.rclone_client import RcloneClient
from drivesync.storage import Storage


def get_completed_count(storage: Storage) -> int:
    with storage.get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) as cnt FROM events WHERE status = 'COMPLETED'").fetchone()
        return row["cnt"] if row else 0


def wait_for_new_completed_events(storage: Storage, initial_count: int, min_new_events: int = 1, timeout: float = 90.0) -> bool:
    """Poll DB until completed events count increases by at least min_new_events beyond initial_count."""
    start = time.time()
    while time.time() - start < timeout:
        if (get_completed_count(storage) - initial_count) >= min_new_events:
            return True
        time.sleep(0.5)
    return False


class TestDriveSyncE2E(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_base = pathlib.Path(self.temp_dir.name).resolve()
        self.test_watched = self.test_base / "watched_folder"
        self.test_watched.mkdir(parents=True, exist_ok=True)

        self.config = Config(base_dir=self.test_base)
        self.config.set("rclone_binary", r"d:\dev\drivesync\rclone.exe")
        self.config.set("rclone_conf", r"d:\dev\drivesync\rclone.conf")
        self.config.set("rclone_remote", "gdrive")
        self.config.set("rc_port", 5589)  # Isolated port
        self.config.set("stability_seconds", 1.0)
        self.config.set("reconciliation_interval", 60)
        self.config.set("bisync_enabled", False)
        self.config.set("circuit_breaker_max_percent", 60.0)

        self.config.add_folder(str(self.test_watched), remote_path="DriveSync_E2E_Test")

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_full_e2e_lifecycle(self):
        print("\n=======================================================")
        print("  STARTING DRIVESYNC END-TO-END SYSTEM INTEGRATION TEST ")
        print("=======================================================\n")

        storage = Storage(self.config.db_path)
        rclone_client = RcloneClient(
            rclone_binary=self.config.get("rclone_binary"),
            rclone_conf=self.config.get("rclone_conf"),
            rc_host="127.0.0.1",
            rc_port=5589,
            rc_user=self.config.get("rc_user"),
            rc_pass=self.config.get("rc_pass"),
        )

        daemon = DriveSyncDaemon(config=self.config)

        import threading
        daemon_thread = threading.Thread(target=daemon.start, daemon=True)
        daemon_thread.start()

        print("--> Step 1: Daemon & rclone RC process launching...")
        alive = False
        for _ in range(20):
            if rclone_client.is_alive():
                alive = True
                break
            time.sleep(0.5)
        self.assertTrue(alive, "rclone RC daemon should be active")
        print("    [OK] Daemon & rclone RC process responding on port 5589.")

        # --- Phase 1: ADD EVENTS ---
        print("\n--> Step 2: Testing ADD File Event Normalization & Sync...")
        file1 = self.test_watched / "e2e_doc1.txt"
        file2 = self.test_watched / "e2e_doc2.txt"

        c0 = get_completed_count(storage)
        file1.write_text("Hello DriveSync E2E Test 1", encoding="utf-8")
        file2.write_text("Hello DriveSync E2E Test 2", encoding="utf-8")

        success = wait_for_new_completed_events(storage, initial_count=c0, min_new_events=2, timeout=90.0)
        self.assertTrue(success, "ADD events should process to COMPLETED within timeout")

        # Check DB Manifest
        m1 = storage.get_manifest_entry(str(file1))
        m2 = storage.get_manifest_entry(str(file2))
        self.assertIsNotNone(m1, "e2e_doc1.txt must be recorded in manifest")
        self.assertIsNotNone(m2, "e2e_doc2.txt must be recorded in manifest")
        print(f"    [OK] Added files synced & recorded in SQLite manifest: {m1['rel_path']}")

        # --- Phase 2: MODIFY EVENT ---
        print("\n--> Step 3: Testing MODIFY Event Sync...")
        c1 = get_completed_count(storage)
        file1.write_text("Updated Content for DriveSync E2E Test 1", encoding="utf-8")
        
        success = wait_for_new_completed_events(storage, initial_count=c1, timeout=90.0)
        self.assertTrue(success, "MODIFY event should reach COMPLETED within timeout")

        m1_updated = storage.get_manifest_entry(str(file1))
        self.assertIsNotNone(m1_updated)
        self.assertNotEqual(m1["content_hash"], m1_updated["content_hash"])
        print("    [OK] Modify event synced & hash updated in manifest.")

        # --- Phase 3: DELETE EVENT ---
        print("\n--> Step 4: Testing DELETE Event Sync...")
        c2 = get_completed_count(storage)
        file2.unlink()

        success = wait_for_new_completed_events(storage, initial_count=c2, timeout=90.0)
        self.assertTrue(success, "DELETE event should reach COMPLETED within timeout")

        m2_deleted = storage.get_manifest_entry(str(file2))
        self.assertIsNone(m2_deleted, "Deleted file should be removed from manifest")
        print("    [OK] Delete event synced & removed from manifest.")

        # --- Phase 4: Stop Daemon Cleanly ---
        print("\n--> Step 5: Testing Clean Daemon Shutdown...")
        daemon.stop()
        time.sleep(1.0)
        self.assertFalse(rclone_client.is_alive(), "rclone RC daemon should be stopped after daemon shutdown")
        print("    [OK] Daemon cleanly stopped.")

        print("\n=======================================================")
        print("  DRIVESYNC END-TO-END TEST PASSED SUCCESSFULLY! ")
        print("=======================================================\n")


if __name__ == "__main__":
    unittest.main()
