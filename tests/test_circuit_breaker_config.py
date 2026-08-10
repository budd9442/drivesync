import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from drivesync.config import Config
from drivesync.storage import Storage
from drivesync.sync_manager import SyncManager
from drivesync.cli import cmd_circuit_breaker, cmd_auth_set_credentials, cmd_auth_login


class TestCircuitBreakerAndAuthConfig(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.config_path = self.base / "config.json"
        self.db_path = self.base / "drivesync.db"
        self.rclone_conf = self.base / "rclone.conf"

        self.config = Config(self.config_path)
        self.config.set("rclone_conf", str(self.rclone_conf))
        self.storage = Storage(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_circuit_breaker_disabled_by_default(self):
        """Verify circuit breaker is disabled by default in config and sync manager."""
        self.assertFalse(self.config.get("circuit_breaker_enabled"))

        # Setup mock sync manager
        rclone_client = MagicMock()
        sync_mgr = SyncManager(self.config, self.storage, rclone_client)

        # Trip storage breaker manually
        self.storage.set_circuit_breaker_tripped(True)

        # evaluate_circuit_breaker should return False because circuit_breaker_enabled is False
        self.assertFalse(sync_mgr.evaluate_circuit_breaker())

    def test_circuit_breaker_enable_disable_cli(self):
        """Verify enabling and disabling circuit breaker via CLI cmd_circuit_breaker."""
        args_enable = argparse.Namespace(cb_action="enable")
        cmd_circuit_breaker(args_enable, self.config)
        self.assertTrue(self.config.get("circuit_breaker_enabled"))

        args_disable = argparse.Namespace(cb_action="disable")
        cmd_circuit_breaker(args_disable, self.config)
        self.assertFalse(self.config.get("circuit_breaker_enabled"))

    def test_circuit_breaker_threshold_configuration(self):
        """Verify setting threshold percent and max count via CLI."""
        args_pct = argparse.Namespace(cb_action="set-percent", value=35.0)
        cmd_circuit_breaker(args_pct, self.config)
        self.assertEqual(self.config.get("circuit_breaker_max_percent"), 35.0)

        args_max = argparse.Namespace(cb_action="set-max", value=150)
        cmd_circuit_breaker(args_max, self.config)
        self.assertEqual(self.config.get("circuit_breaker_max_deletes"), 150)

    def test_auth_set_credentials_and_invalidation(self):
        """Verify setting custom OAuth credentials stores config and invalidates rclone.conf token."""
        # Create dummy rclone.conf with existing token
        with open(self.rclone_conf, "w", encoding="utf-8") as f:
            f.write("[gdrive]\ntype = drive\ntoken = {\"access_token\": \"old_token\"}\n")

        args = argparse.Namespace(
            client_id="my-client-id.apps.googleusercontent.com",
            client_secret="my-client-secret-12345",
        )
        cmd_auth_set_credentials(args, self.config)

        self.assertEqual(self.config.get("client_id"), "my-client-id.apps.googleusercontent.com")
        self.assertEqual(self.config.get("client_secret"), "my-client-secret-12345")

        # Verify rclone.conf updated and token removed
        import configparser
        cp = configparser.ConfigParser()
        cp.read(self.rclone_conf, encoding="utf-8")
        self.assertTrue(cp.has_section("gdrive"))
        self.assertEqual(cp.get("gdrive", "client_id"), "my-client-id.apps.googleusercontent.com")
        self.assertEqual(cp.get("gdrive", "client_secret"), "my-client-secret-12345")
        self.assertFalse(cp.has_option("gdrive", "token"))


if __name__ == "__main__":
    unittest.main()
