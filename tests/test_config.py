import pathlib
import tempfile
import unittest

from drivesync.config import Config


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config = Config(base_dir=pathlib.Path(self.temp_dir.name))

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_default_values(self):
        self.assertEqual(self.config.get("rc_port"), 5572)
        self.assertEqual(self.config.get("stability_seconds"), 2.0)
        self.assertIn("~$*.docx", self.config.get("ignore_patterns"))

    def test_add_and_remove_folder(self):
        folder_dir = tempfile.TemporaryDirectory()
        local_path = folder_dir.name

        entry = self.config.add_folder(local_path, remote_path="Docs")
        self.assertEqual(len(self.config.watched_folders), 1)
        self.assertEqual(entry["remote_path"], "Docs")

        removed = self.config.remove_folder(local_path)
        self.assertTrue(removed)
        self.assertEqual(len(self.config.watched_folders), 0)
        try:
            folder_dir.cleanup()
        except Exception:
            pass


if __name__ == "__main__":
    unittest.main()
