import datetime
import logging
import os
import pathlib
import shutil
import time
from typing import List, Optional

logger = logging.getLogger(__name__)


class TrashManager:
    """Manages moving deleted files to a safety local/remote trash folder instead of permanent deletion."""

    def __init__(self, base_trash_dir: Optional[pathlib.Path] = None):
        if base_trash_dir is None:
            local_appdata = os.environ.get("LOCALAPPDATA")
            if local_appdata:
                base_trash_dir = pathlib.Path(local_appdata) / "DriveSync" / "Trash"
            else:
                base_trash_dir = pathlib.Path.home() / ".drivesync" / "Trash"

        self.trash_dir = base_trash_dir.resolve()
        self.trash_dir.mkdir(parents=True, exist_ok=True)

    def move_local_to_trash(self, full_path: str | pathlib.Path) -> Optional[str]:
        """Move a local file to the DriveSync local trash folder."""
        p = pathlib.Path(full_path)
        if not p.exists():
            return None

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        target_dir = self.trash_dir / timestamp
        target_dir.mkdir(parents=True, exist_ok=True)

        dest_path = target_dir / p.name
        try:
            shutil.move(str(p), str(dest_path))
            logger.info(f"Moved local file to safety trash: {p} -> {dest_path}")
            return str(dest_path)
        except Exception as e:
            logger.error(f"Failed to move file to local trash: {e}")
            return None

    def list_trash_items(self) -> List[pathlib.Path]:
        """List all items currently in local trash."""
        if not self.trash_dir.exists():
            return []
        items = []
        for root, dirs, files in os.walk(self.trash_dir):
            for f in files:
                items.append(pathlib.Path(root) / f)
        return items

    def clear_trash(self) -> int:
        """Purge all files in local trash."""
        items = self.list_trash_items()
        count = len(items)
        if self.trash_dir.exists():
            shutil.rmtree(self.trash_dir, ignore_errors=True)
            self.trash_dir.mkdir(parents=True, exist_ok=True)
        return count
