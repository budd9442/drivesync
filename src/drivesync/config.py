import json
import os
import pathlib
import secrets
import sys
from typing import Any, Dict, List, Optional

DEFAULT_IGNORE_PATTERNS = [
    "~$*.docx",
    "*.tmp",
    "*.crdownload",
    "*.part",
    "~$*",
    "*.lock",
    "desktop.ini",
    "Thumbs.db",
    ".DS_Store",
]


class Config:
    """Manages DriveSync configuration persistent storage in LocalAppData."""

    def __init__(self, base_dir: Optional[pathlib.Path] = None):
        if base_dir is None:
            local_appdata = os.environ.get("LOCALAPPDATA")
            if local_appdata:
                base_dir = pathlib.Path(local_appdata) / "DriveSync"
            else:
                base_dir = pathlib.Path.home() / ".drivesync"

        self.base_dir = base_dir.resolve()
        self.config_path = self.base_dir / "config.json"
        self.db_path = self.base_dir / "drivesync.db"
        self.pid_path = self.base_dir / "drivesync.pid"
        self.logs_dir = self.base_dir / "logs"
        self.log_path = self.logs_dir / "drivesync.log"

        # Ensure directory structures exist
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        self._data: Dict[str, Any] = {}
        self.load()

    def _auto_detect_rclone_binary(self) -> str:
        # Check current working directory or workspace path first
        dev_rclone = pathlib.Path.cwd() / "rclone.exe"
        if dev_rclone.exists():
            return str(dev_rclone.resolve())

        bin_rclone = self.base_dir / "bin" / "rclone.exe"
        if bin_rclone.exists():
            return str(bin_rclone.resolve())

        return "rclone"

    def _auto_detect_rclone_conf(self) -> str:
        dev_conf = pathlib.Path.cwd() / "rclone.conf"
        if dev_conf.exists():
            return str(dev_conf.resolve())

        default_conf = self.base_dir / "rclone.conf"
        return str(default_conf.resolve())

    def get_defaults(self) -> Dict[str, Any]:
        return {
            "watched_folders": [],
            "rclone_binary": self._auto_detect_rclone_binary(),
            "rclone_conf": self._auto_detect_rclone_conf(),
            "rclone_remote": "gdrive",
            "rc_host": "127.0.0.1",
            "rc_port": 5572,
            "rc_user": "drivesync_user",
            "rc_pass": secrets.token_hex(16),
            "stability_seconds": 2.0,
            "retention_days": 7,
            "circuit_breaker_max_deletes": 50,
            "circuit_breaker_max_percent": 20.0,
            "ignore_patterns": DEFAULT_IGNORE_PATTERNS.copy(),
            "reconciliation_interval": 300,
        }

    def load(self) -> None:
        defaults = self.get_defaults()
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                defaults.update(saved)
            except Exception as e:
                # Fallback to defaults on corrupt config file
                pass
        self._data = defaults
        self.save()

    def save(self) -> None:
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self.save()

    @property
    def watched_folders(self) -> List[Dict[str, str]]:
        return self._data.get("watched_folders", [])

    def add_folder(self, local_path: str, remote_path: Optional[str] = None) -> Dict[str, str]:
        resolved_local = str(pathlib.Path(local_path).resolve())
        if remote_path is None or not remote_path.strip():
            folder_name = pathlib.Path(resolved_local).name
            remote_path = folder_name.strip("/")

        folders = self.watched_folders
        # Check if already added
        for f in folders:
            if pathlib.Path(f["local_path"]).resolve() == pathlib.Path(resolved_local).resolve():
                f["remote_path"] = remote_path
                self.set("watched_folders", folders)
                return f

        entry = {"local_path": resolved_local, "remote_path": remote_path}
        folders.append(entry)
        self.set("watched_folders", folders)
        return entry

    def remove_folder(self, local_path: str) -> bool:
        resolved_local = pathlib.Path(local_path).resolve()
        folders = self.watched_folders
        new_folders = [
            f for f in folders if pathlib.Path(f["local_path"]).resolve() != resolved_local
        ]
        if len(new_folders) < len(folders):
            self.set("watched_folders", new_folders)
            return True
        return False
