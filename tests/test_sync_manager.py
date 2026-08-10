import pathlib
import tempfile
import unittest
from unittest.mock import MagicMock

from drivesync.config import Config
from drivesync.storage import Storage
from drivesync.sync_manager import SyncManager, is_transient_error


class TestSyncManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config = Config(base_dir=pathlib.Path(self.temp_dir.name))
        self.storage = Storage(self.config.db_path)
        self.mock_rclone = MagicMock()

        self.sync_manager = SyncManager(self.config, self.storage, self.mock_rclone)

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_retry_classification(self):
        self.assertTrue(is_transient_error("HTTP 429 Too Many Requests"))
        self.assertTrue(is_transient_error("connection refused by host"))
        self.assertFalse(is_transient_error("HTTP 401 Unauthorized"))
        self.assertFalse(is_transient_error("File does not exist"))

    def test_circuit_breaker_tripping(self):
        # Seed 60 pending deletions (exceeding default max 50)
        for i in range(60):
            p = f"C:\\data\\file_{i}.txt"
            self.storage.enqueue_event(p, "C:\\data", f"file_{i}.txt", "DELETE")

        tripped = self.sync_manager.evaluate_circuit_breaker()
        self.assertTrue(tripped)
        self.assertTrue(self.storage.is_circuit_breaker_tripped())


if __name__ == "__main__":
    unittest.main()
