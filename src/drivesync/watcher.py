import logging
import pathlib
import queue
import threading
import time
from typing import List, Optional

import watchfiles

logger = logging.getLogger(__name__)


class FolderWatcher:
    """Monitors configured local directories using watchfiles in a background thread."""

    def __init__(self, folders: List[str], event_queue: queue.Queue):
        self.folders = [str(pathlib.Path(f).resolve()) for f in folders if pathlib.Path(f).exists()]
        self.event_queue = event_queue
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if not self.folders:
            logger.warning("No valid existing watched folders to watch.")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_watch, daemon=True, name="FolderWatcherThread")
        self._thread.start()
        logger.info(f"FolderWatcher started for folders: {self.folders}")

    def _run_watch(self) -> None:
        try:
            for changes in watchfiles.watch(*self.folders, stop_event=self._stop_event):
                if self._stop_event.is_set():
                    break
                if changes:
                    for change_type, path_str in changes:
                        self.event_queue.put((change_type, path_str, time.time()))
        except Exception as e:
            if not self._stop_event.is_set():
                logger.error(f"Error in watcher loop: {e}", exc_info=True)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        logger.info("FolderWatcher stopped.")
