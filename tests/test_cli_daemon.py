import argparse
import os
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from drivesync.autostart import _get_daemon_command
from drivesync.config import Config
from drivesync.cli import cmd_daemon_run, cmd_start, main


class TestCliDaemon(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = pathlib.Path(self.temp_dir.name)
        self.config = Config(base_dir=self.base_path)

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_log_file_created_on_config_init(self):
        """Verify that drivesync.log is created immediately on Config instantiation."""
        self.assertTrue(self.config.log_path.exists())
        with open(self.config.log_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("DriveSync log file initialized", content)

    def test_daemon_command_resolution(self):
        """Verify daemon command resolution in frozen vs non-frozen modes."""
        with patch.object(sys, "frozen", True, create=True):
            cmd_frozen = _get_daemon_command()
            self.assertIn("start", cmd_frozen)
            self.assertNotIn("-m", cmd_frozen)

        with patch.object(sys, "frozen", False, create=True):
            cmd_normal = _get_daemon_command()
            self.assertIn("start", cmd_normal)

    @patch("subprocess.Popen")
    def test_cmd_start_frozen(self, mock_popen):
        """Test cmd_start spawning command in frozen executable mode."""
        args = argparse.Namespace()
        mock_proc = MagicMock()
        mock_proc.pid = 1234
        mock_popen.return_value = mock_proc

        with patch.object(sys, "frozen", True, create=True):
            with patch("drivesync.cli.get_daemon_pid", return_value=None):
                cmd_start(args, self.config)

        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        self.assertEqual(cmd[1], "daemon")
        self.assertNotIn("-m", cmd)

    @patch("subprocess.Popen")
    def test_cmd_start_non_frozen(self, mock_popen):
        """Test cmd_start spawning command in python package mode."""
        args = argparse.Namespace()
        mock_proc = MagicMock()
        mock_proc.pid = 5678
        mock_popen.return_value = mock_proc

        with patch.object(sys, "frozen", False, create=True):
            with patch("drivesync.cli.get_daemon_pid", return_value=None):
                cmd_start(args, self.config)

        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        self.assertEqual(cmd[1], "-m")
        self.assertEqual(cmd[2], "drivesync.daemon")


if __name__ == "__main__":
    unittest.main()
