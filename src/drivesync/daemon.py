import logging
from logging.handlers import RotatingFileHandler
import os
import queue
import signal
import sys
import time
from typing import Optional

from drivesync.config import Config
from drivesync.normalizer import EventNormalizer
from drivesync.rclone_client import RcloneClient
from drivesync.storage import Storage
from drivesync.sync_manager import SyncManager
from drivesync.watcher import FolderWatcher

logger = logging.getLogger("drivesync")


def setup_logging(config: Config) -> None:
    config.log_path.parent.mkdir(parents=True, exist_ok=True)
    logger.setLevel(logging.INFO)

    # Rotating file handler (max 10MB, keep 5 backups)
    file_handler = RotatingFileHandler(
        config.log_path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)


class DriveSyncDaemon:
    """Main continuous background service manager."""

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        setup_logging(self.config)

        self.storage = Storage(self.config.db_path)
        self.rclone_client = RcloneClient(
            rclone_binary=self.config.get("rclone_binary"),
            rclone_conf=self.config.get("rclone_conf"),
            rc_host=self.config.get("rc_host", "127.0.0.1"),
            rc_port=self.config.get("rc_port", 5572),
            rc_user=self.config.get("rc_user", "drivesync_user"),
            rc_pass=self.config.get("rc_pass", "secret"),
        )

        self.sync_manager = SyncManager(self.config, self.storage, self.rclone_client)
        self.raw_queue: queue.Queue = queue.Queue()

        self.watcher: Optional[FolderWatcher] = None
        self.normalizer: Optional[EventNormalizer] = None

        self._running = False
        self._last_reconciliation = 0.0
        self._last_purge = 0.0

    def write_pid_file(self) -> None:
        with open(self.config.pid_path, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))

    def remove_pid_file(self) -> None:
        if self.config.pid_path.exists():
            try:
                self.config.pid_path.unlink()
            except Exception:
                pass

    def run_reconciliation(self, force_resync: bool = False) -> None:
        """Run periodic/startup 2-way rclone bisync and local directory reconciliation diff scan."""
        logger.info("Starting 2-way bisync reconciliation scan...")
        remote_name = self.config.get("rclone_remote", "gdrive")
        bisync_enabled = self.config.get("bisync_enabled", True)
        conflict_resolve = self.config.get("bisync_conflict_resolve", "newer")
        max_deletes = self.config.get("bisync_max_deletes", 50)

        total_a, total_m, total_d = 0, 0, 0

        for folder in self.config.watched_folders:
            local_path = folder["local_path"]
            remote_path = folder["remote_path"]

            if bisync_enabled:
                needs_resync = force_resync or not self.storage.is_resync_completed(local_path)
                ok, msg = self.rclone_client.run_bisync(
                    local_path=local_path,
                    remote_name=remote_name,
                    remote_path=remote_path,
                    resync=needs_resync,
                    conflict_resolve=conflict_resolve,
                    max_deletes=max_deletes,
                )
                if ok:
                    self.storage.set_resync_completed(local_path, True)
                else:
                    logger.warning(f"bisync run for {local_path} returned: {msg}")

            # Refresh local manifest state post-bisync
            a, m, d = self.storage.reconcile_folder(
                local_path, self.config.get("ignore_patterns", [])
            )
            total_a += a
            total_m += m
            total_d += d

        logger.info(
            f"2-Way Reconciliation complete. Enqueued: {total_a} added, {total_m} modified, {total_d} deleted."
        )

    def start(self) -> None:
        logger.info("DriveSync Daemon starting up...")
        self.write_pid_file()
        self._running = True

        # Perform startup DB recovery
        recovered_rows = self.storage.startup_recovery()
        if recovered_rows > 0:
            logger.info(f"Startup DB Recovery: Reset {recovered_rows} PROCESSING events back to PENDING.")

        # Launch local rclone daemon
        daemon_started = self.rclone_client.start_daemon()
        if not daemon_started:
            logger.error("Failed to start rclone RC daemon on startup.")

        # Start FolderWatcher & EventNormalizer
        watched_paths = [f["local_path"] for f in self.config.watched_folders]
        self.watcher = FolderWatcher(watched_paths, self.raw_queue)
        self.normalizer = EventNormalizer(
            storage=self.storage,
            watched_folders=self.config.watched_folders,
            ignore_patterns=self.config.get("ignore_patterns", []),
            stability_seconds=self.config.get("stability_seconds", 2.0),
        )

        self.normalizer.start(self.raw_queue)
        self.watcher.start()

        # Initial startup reconciliation scan
        self.run_reconciliation()
        self._last_reconciliation = time.time()
        self._last_purge = time.time()

        # Main processing loop
        reconciliation_interval = self.config.get("reconciliation_interval", 300)
        retention_days = self.config.get("retention_days", 7)

        try:
            while self._running:
                now = time.time()

                # Process pending sync events
                self.sync_manager.process_pending_batch(limit=25)

                # Periodic reconciliation check
                if now - self._last_reconciliation >= reconciliation_interval:
                    self.run_reconciliation()
                    self._last_reconciliation = now

                # Periodic DB purge check (every 12 hours)
                if now - self._last_purge >= 43200:
                    purged = self.storage.purge_completed_events(retention_days)
                    if purged > 0:
                        logger.info(f"Purged {purged} COMPLETED events older than {retention_days} days.")
                    self._last_purge = now

                time.sleep(1.0)

        except (KeyboardInterrupt, SystemExit):
            logger.info("Shutdown signal received.")
        finally:
            self.stop()

    def stop(self) -> None:
        self._running = False
        logger.info("Shutting down DriveSync Daemon...")

        if self.watcher:
            self.watcher.stop()
        if self.normalizer:
            self.normalizer.stop()

        self.rclone_client.stop_daemon()
        self.remove_pid_file()
        logger.info("DriveSync Daemon gracefully stopped.")


def main():
    daemon = DriveSyncDaemon()

    def handle_signal(sig, frame):
        daemon.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    daemon.start()


if __name__ == "__main__":
    main()
