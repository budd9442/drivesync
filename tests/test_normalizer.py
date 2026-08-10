import pathlib
import tempfile
import time
import unittest
import watchfiles

from drivesync.normalizer import EventNormalizer
from drivesync.storage import Storage


class TestEventNormalizer(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp_dir.name) / "test.db"
        self.storage = Storage(self.db_path)
        self.watched_folder = pathlib.Path(self.temp_dir.name) / "watched"
        self.watched_folder.mkdir()

        self.normalizer = EventNormalizer(
            storage=self.storage,
            watched_folders=[{"local_path": str(self.watched_folder), "remote_path": "remote"}],
            ignore_patterns=["~$*.docx", "*.tmp"],
            stability_seconds=0.5,
        )

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_ignore_filtering(self):
        now = time.time()
        temp_file = self.watched_folder / "test.tmp"
        self.normalizer.process_raw_event(watchfiles.Change.added, str(temp_file), now)
        self.assertEqual(len(self.normalizer.candidates), 0)

    def test_atomic_save_coalescing_delete_create(self):
        now = time.time()
        file_p = self.watched_folder / "doc.txt"
        file_p.write_text("Hello", encoding="utf-8")

        # Simulate DELETE followed by ADD within debounce window
        self.normalizer.process_raw_event(watchfiles.Change.deleted, str(file_p), now)
        norm_path = str(file_p.resolve())
        self.assertEqual(self.normalizer.candidates[norm_path].op, "DELETE")

        self.normalizer.process_raw_event(watchfiles.Change.added, str(file_p), now + 0.1)
        # Should be coalesced into single MODIFY
        self.assertEqual(self.normalizer.candidates[norm_path].op, "MODIFY")

    def test_atomic_save_coalescing_create_delete(self):
        now = time.time()
        file_p = self.watched_folder / "temp_create.txt"

        self.normalizer.process_raw_event(watchfiles.Change.added, str(file_p), now)
        norm_path = str(file_p.resolve())
        self.assertEqual(self.normalizer.candidates[norm_path].op, "ADD")

        self.normalizer.process_raw_event(watchfiles.Change.deleted, str(file_p), now + 0.1)
        # Should be dropped entirely
        self.assertNotIn(norm_path, self.normalizer.candidates)


if __name__ == "__main__":
    unittest.main()
