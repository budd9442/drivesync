import datetime
import logging
import os
import time

from drivesync.config import Config
from drivesync.path_utils import to_remote_path
from drivesync.rclone_client import RcloneClient
from drivesync.storage import Storage

logger = logging.getLogger(__name__)

# Transient error markers (lowercased)
TRANSIENT_ERRORS = (
    "http 429",
    "http 500",
    "http 502",
    "http 503",
    "http 504",
    "connection refused",
    "timed out",
    "reset by peer",
    "network is unreachable",
    "temporary failure",
)

# Permanent error markers (lowercased)
PERMANENT_ERRORS = (
    "http 400",
    "http 401",
    "http 403",
    "file does not exist",
    "no space left on device",
    "invalid credentials",
)


def is_transient_error(error_msg: str) -> bool:
    err_lower = error_msg.lower()
    for perm in PERMANENT_ERRORS:
        if perm in err_lower:
            return False
    for trans in TRANSIENT_ERRORS:
        if trans in err_lower:
            return True
    # Default to transient if uncertain to allow retries
    return True


class SyncManager:
    """
    Core sync execution engine.
    Processes pending SQLite events in per-path sequential order, enforces the Circuit Breaker,
    classifies retries (transient vs permanent), and supervises rclone RC daemon health.
    
    Conflict Policy: Last-Write-Wins by file mtime.
    """

    def __init__(self, config: Config, storage: Storage, rclone_client: RcloneClient):
        self.config = config
        self.storage = storage
        self.rclone_client = rclone_client
        self._last_rclone_health_check = 0.0

    def check_rclone_health(self) -> bool:
        """Periodic rclone daemon health check and auto-restart supervision."""
        now = time.time()
        if now - self._last_rclone_health_check < 30.0 and self.rclone_client.is_alive():
            return True

        self._last_rclone_health_check = now
        if not self.rclone_client.is_alive():
            logger.warning("rclone daemon is down. Attempting auto-restart...")
            restarted = self.rclone_client.start_daemon()
            if not restarted:
                logger.critical(
                    "rclone daemon is down and could NOT be restarted! Halting sync processing."
                )
                return False
            logger.info("rclone daemon successfully auto-restarted.")
        return True

    def evaluate_circuit_breaker(self) -> bool:
        """
        Circuit Breaker Safety Mechanism.
        Halts automatic deletion sync if pending deletions exceed safety threshold.
        """
        if self.storage.is_circuit_breaker_tripped():
            logger.warning(
                "Circuit Breaker is currently TRIPPED. Deletion sync halted until approved via CLI: 'drivesync circuit-breaker approve'."
            )
            return True

        pending_deletions = self.storage.count_pending_deletions()
        manifest_count = self.storage.get_manifest_count()

        max_deletes = self.config.get("circuit_breaker_max_deletes", 50)
        max_percent = self.config.get("circuit_breaker_max_percent", 20.0)

        exceeds_count = pending_deletions > max_deletes
        exceeds_percent = manifest_count > 0 and ((pending_deletions / manifest_count) * 100.0) > max_percent

        if exceeds_count or exceeds_percent:
            self.storage.set_circuit_breaker_tripped(True)
            logger.critical(
                f"CRITICAL CIRCUIT BREAKER TRIPPED! Pending deletions ({pending_deletions}) exceed threshold "
                f"(max {max_deletes} files or {max_percent}% of {manifest_count} tracked files). "
                f"Sync halted to prevent accidental data destruction. Run 'drivesync circuit-breaker approve' to confirm."
            )
            return True

        return False

    def process_pending_batch(self, limit: int = 50) -> int:
        """Process next batch of pending sync events."""
        if not self.check_rclone_health():
            return 0

        circuit_tripped = self.evaluate_circuit_breaker()

        pending_events = self.storage.get_pending_events(limit=limit)
        if not pending_events:
            return 0

        processed_count = 0
        remote_name = self.config.get("rclone_remote", "gdrive")
        watched_map = {
            f["local_path"]: f["remote_path"] for f in self.config.watched_folders
        }

        for event in pending_events:
            event_id = event["id"]
            op = event["operation"]
            full_path = event["full_path"]
            local_root = event["local_root"]
            rel_path = event["rel_path"]

            if op == "DELETE" and circuit_tripped:
                # Skip deletion processing while circuit breaker is tripped
                continue

            remote_folder_root = watched_map.get(local_root, "")
            remote_full_path = to_remote_path(remote_folder_root, rel_path)

            self.storage.mark_event_processing(event_id)

            success = False
            error_msg = ""
            remote_id = None

            if op in ("ADD", "MODIFY"):
                if not os.path.exists(full_path):
                    success = False
                    error_msg = f"Local file no longer exists: {full_path}"
                    is_perm = True
                else:
                    success, error_msg = self.rclone_client.copy_file(
                        full_path, remote_name, remote_full_path
                    )
                    is_perm = not is_transient_error(error_msg) if not success else False

            elif op == "DELETE":
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                trash_remote_path = f"DriveSync_Trash/{timestamp}/{rel_path.lstrip('/')}"
                success = self.rclone_client.move_file(
                    f"{remote_name}:", remote_full_path, f"{remote_name}:", trash_remote_path
                )
                if not success:
                    # Fallback to delete_file (which moves to Google Drive native trash)
                    success, error_msg = self.rclone_client.delete_file(
                        remote_name, remote_full_path
                    )
                else:
                    error_msg = ""
                is_perm = not is_transient_error(error_msg) if not success else False

            if success:
                self.storage.mark_event_completed(event_id, remote_id=remote_id)
                logger.info(f"Successfully synced {op} event for {rel_path}")
                processed_count += 1
            else:
                logger.error(f"Failed {op} sync for {rel_path}: {error_msg}")
                self.storage.mark_event_failed(event_id, error_msg, permanent=is_perm)

        return processed_count
