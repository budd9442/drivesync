import logging
import os
import pathlib
import queue
import threading
import time
from typing import Dict, List, Optional, Tuple

import watchfiles
from drivesync.path_utils import get_relative_path, is_ignored, normalize_local_path
from drivesync.storage import Storage

logger = logging.getLogger(__name__)


class PendingCandidate:
    def __init__(
        self,
        full_path: str,
        local_root: str,
        rel_path: str,
        initial_op: str,
        first_seen: float,
    ):
        self.full_path = full_path
        self.local_root = local_root
        self.rel_path = rel_path
        self.op = initial_op  # 'ADD', 'MODIFY', 'DELETE'
        self.first_seen = first_seen
        self.last_seen = first_seen
        self.last_mtime: Optional[float] = None
        self.last_size: Optional[int] = None
        self.stable_since: Optional[float] = None


class EventNormalizer:
    """
    Normalizes raw watchfiles change events.
    Applies path canonicalization, ignore filtering, stability checks (~2s debounce),
    and atomic-save coalescing before persisting events into SQLite Storage.
    """

    def __init__(
        self,
        storage: Storage,
        watched_folders: List[Dict[str, str]],
        ignore_patterns: List[str],
        stability_seconds: float = 2.0,
    ):
        self.storage = storage
        self.watched_folders = [
            {
                "local_path": normalize_local_path(f["local_path"]),
                "remote_path": f["remote_path"],
            }
            for f in watched_folders
        ]
        self.ignore_patterns = ignore_patterns
        self.stability_seconds = stability_seconds

        self.candidates: Dict[str, PendingCandidate] = {}
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def update_folders_and_patterns(
        self, watched_folders: List[Dict[str, str]], ignore_patterns: List[str]
    ) -> None:
        self.watched_folders = [
            {
                "local_path": normalize_local_path(f["local_path"]),
                "remote_path": f["remote_path"],
            }
            for f in watched_folders
        ]
        self.ignore_patterns = ignore_patterns

    def find_local_root(self, full_path: str) -> Optional[Tuple[str, str]]:
        """Find matching local root and relative path for given file path."""
        norm_full = normalize_local_path(full_path)
        for folder in self.watched_folders:
            root = folder["local_path"]
            rel = get_relative_path(norm_full, root)
            if rel is not None:
                return root, rel
        return None

    def process_raw_event(self, change_type: watchfiles.Change, raw_path: str, now: float) -> None:
        norm_path = normalize_local_path(raw_path)

        if is_ignored(norm_path, self.ignore_patterns):
            return

        match_info = self.find_local_root(norm_path)
        if not match_info:
            return

        local_root, rel_path = match_info

        # Convert Change enum to operation string
        if change_type == watchfiles.Change.added:
            op = "ADD"
        elif change_type == watchfiles.Change.modified:
            op = "MODIFY"
        elif change_type == watchfiles.Change.deleted:
            op = "DELETE"
        else:
            return

        cand = self.candidates.get(norm_path)
        if cand is None:
            self.candidates[norm_path] = PendingCandidate(
                norm_path, local_root, rel_path, op, now
            )
        else:
            cand.last_seen = now
            # Atomic-save coalescing rules:
            if cand.op == "DELETE" and op in ("ADD", "MODIFY"):
                # Delete followed by create within debounce window -> coalesced to MODIFY
                cand.op = "MODIFY"
                cand.stable_since = None
            elif cand.op in ("ADD", "MODIFY") and op == "DELETE":
                if cand.op == "ADD":
                    # Create followed by delete within debounce window -> dropped entirely
                    del self.candidates[norm_path]
                    return
                else:
                    cand.op = "DELETE"
                    cand.stable_since = None
            elif op == "MODIFY" and cand.op == "ADD":
                # Keep ADD operation
                cand.stable_since = None

    def tick_stability_checks(self, now: float) -> None:
        to_remove = []

        for path, cand in list(self.candidates.items()):
            if cand.op == "DELETE":
                # For DELETE: check if file really no longer exists and debounce time has passed
                if not os.path.exists(path):
                    if (now - cand.last_seen) >= self.stability_seconds:
                        self.storage.enqueue_event(
                            full_path=cand.full_path,
                            local_root=cand.local_root,
                            rel_path=cand.rel_path,
                            operation="DELETE",
                        )
                        to_remove.append(path)
                else:
                    # File exists again; candidate recovered
                    cand.op = "MODIFY"
                    cand.stable_since = None

            elif cand.op in ("ADD", "MODIFY"):
                if not os.path.exists(path):
                    # File was deleted before stability
                    cand.op = "DELETE"
                    cand.last_seen = now
                    cand.stable_since = None
                    continue

                try:
                    stat = os.stat(path)
                    current_mtime = stat.st_mtime
                    current_size = stat.st_size
                except OSError:
                    # File is currently locked/being written
                    cand.stable_since = None
                    continue

                if cand.last_mtime == current_mtime and cand.last_size == current_size:
                    if cand.stable_since is None:
                        cand.stable_since = now
                    elif (now - cand.stable_since) >= self.stability_seconds:
                        # File mtime & size have remained unchanged for stability window!
                        enqueued = self.storage.enqueue_event(
                            full_path=cand.full_path,
                            local_root=cand.local_root,
                            rel_path=cand.rel_path,
                            operation=cand.op,
                            mtime=current_mtime,
                            size=current_size,
                        )
                        if enqueued:
                            logger.info(
                                f"Normalized and enqueued {cand.op} event for {cand.rel_path}"
                            )
                        to_remove.append(path)
                else:
                    cand.last_mtime = current_mtime
                    cand.last_size = current_size
                    cand.stable_since = None

        for path in to_remove:
            self.candidates.pop(path, None)

    def run_loop(self, raw_queue: queue.Queue) -> None:
        logger.info("EventNormalizer thread started.")
        while not self._stop_event.is_set():
            now = time.time()
            try:
                # Drain incoming raw events from watcher queue
                while True:
                    change_type, raw_path, evt_time = raw_queue.get_nowait()
                    self.process_raw_event(change_type, raw_path, evt_time)
            except queue.Empty:
                pass

            # Tick stability & coalescing check
            self.tick_stability_checks(now)
            time.sleep(0.5)

        logger.info("EventNormalizer thread stopped.")

    def start(self, raw_queue: queue.Queue) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self.run_loop, args=(raw_queue,), daemon=True, name="EventNormalizerThread"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
