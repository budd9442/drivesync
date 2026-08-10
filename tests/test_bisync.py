import pathlib
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from drivesync.config import Config
from drivesync.rclone_client import RcloneClient
from drivesync.storage import Storage


class TestBisyncIntegration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_base = pathlib.Path(self.temp_dir.name).resolve()
        self.config = Config(base_dir=self.test_base)
        self.storage = Storage(self.config.db_path)

        self.rclone_client = RcloneClient(
            rclone_binary=r"d:\dev\drivesync\rclone.exe",
            rclone_conf=r"d:\dev\drivesync\rclone.conf",
        )

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_resync_state_tracking(self):
        folder_path = str(self.test_base / "test_folder")

        # Initial state should be False
        self.assertFalse(self.storage.is_resync_completed(folder_path))

        # Set completed
        self.storage.set_resync_completed(folder_path, True)
        self.assertTrue(self.storage.is_resync_completed(folder_path))

        # Set reset
        self.storage.set_resync_completed(folder_path, False)
        self.assertFalse(self.storage.is_resync_completed(folder_path))

    @patch("subprocess.run")
    def test_run_bisync_flags(self, mock_subprocess_run):
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = "bisync complete"
        mock_subprocess_run.return_value = mock_res

        local_p = str(self.test_base / "sync_folder")

        # Test normal delta bisync
        ok, msg = self.rclone_client.run_bisync(
            local_path=local_p,
            remote_name="gdrive",
            remote_path="DriveSync/Test",
            resync=False,
            conflict_resolve="newer",
            max_deletes=50,
        )

        self.assertTrue(ok)
        mock_subprocess_run.assert_called_once()
        cmd = mock_subprocess_run.call_args[0][0]

        self.assertIn("bisync", cmd)
        self.assertIn("gdrive:DriveSync/Test", cmd)
        self.assertIn("--conflict-resolve=newer", cmd)
        self.assertIn("--max-delete=50", cmd)
        self.assertNotIn("--resync", cmd)

        # Reset mock and test --resync flag
        mock_subprocess_run.reset_mock()
        ok_resync, _ = self.rclone_client.run_bisync(
            local_path=local_p,
            remote_name="gdrive",
            remote_path="DriveSync/Test",
            resync=True,
        )
        self.assertTrue(ok_resync)
        cmd_resync = mock_subprocess_run.call_args[0][0]
        self.assertIn("--resync", cmd_resync)


if __name__ == "__main__":
    unittest.main()
