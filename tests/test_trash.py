import pathlib
import tempfile
import unittest

from drivesync.trash import TrashManager


class TestTrashManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_base = pathlib.Path(self.temp_dir.name).resolve()
        self.trash_dir = self.test_base / "Trash"
        self.trash_mgr = TrashManager(base_trash_dir=self.trash_dir)

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_move_local_to_trash_and_clear(self):
        sample_file = self.test_base / "file_to_delete.txt"
        sample_file.write_text("Secret content to preserve in trash", encoding="utf-8")

        self.assertTrue(sample_file.exists())
        trashed_path = self.trash_mgr.move_local_to_trash(sample_file)

        self.assertIsNotNone(trashed_path)
        self.assertFalse(sample_file.exists(), "Original file should no longer exist at original path")
        self.assertTrue(pathlib.Path(trashed_path).exists(), "File should now exist inside trash folder")

        items = self.trash_mgr.list_trash_items()
        self.assertEqual(len(items), 1)

        count = self.trash_mgr.clear_trash()
        self.assertEqual(count, 1)
        self.assertEqual(len(self.trash_mgr.list_trash_items()), 0)


if __name__ == "__main__":
    unittest.main()
