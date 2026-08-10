import logging
import os
import pathlib
import shutil
import urllib.request
import zipfile
from typing import Optional

logger = logging.getLogger(__name__)

RCLONE_ZIP_URL = "https://downloads.rclone.org/rclone-current-windows-amd64.zip"


def ensure_rclone_binary(custom_path: Optional[str] = None, target_dir: Optional[pathlib.Path] = None) -> Optional[str]:
    """
    Ensure rclone.exe binary is available.
    If not found in custom_path or PATH, automatically download official zero-admin rclone.exe.
    """
    # 1. Check custom path if provided
    if custom_path:
        p = pathlib.Path(custom_path)
        if p.exists() and p.is_file():
            return str(p)

    # 2. Check PATH
    path_rclone = shutil.which("rclone")
    if path_rclone:
        return path_rclone

    # 3. Check default LocalAppData target directory
    if target_dir is None:
        local_appdata = os.environ.get("LOCALAPPDATA", str(pathlib.Path.home() / ".drivesync"))
        target_dir = pathlib.Path(local_appdata) / "DriveSync"

    target_dir.mkdir(parents=True, exist_ok=True)
    target_binary = target_dir / "rclone.exe"

    if target_binary.exists():
        return str(target_binary)

    # 4. Auto-download official rclone.exe zip
    logger.info(f"rclone.exe not found. Downloading official binary from {RCLONE_ZIP_URL}...")
    print(f"[*] Downloading official rclone binary to {target_binary}...")

    zip_path = target_dir / "rclone_download.zip"
    try:
        urllib.request.urlretrieve(RCLONE_ZIP_URL, str(zip_path))
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            for member in zf.namelist():
                if member.endswith("rclone.exe"):
                    with zf.open(member) as source, open(str(target_binary), "wb") as target:
                        shutil.copyfileobj(source, target)
                    break

        if zip_path.exists():
            zip_path.unlink()

        if target_binary.exists():
            logger.info(f"rclone.exe successfully installed to {target_binary}")
            print(f"[SUCCESS] rclone binary installed to {target_binary}")
            return str(target_binary)
    except Exception as e:
        logger.error(f"Failed to auto-download rclone.exe: {e}")
        print(f"[ERROR] Auto-download of rclone failed: {e}")
        if zip_path.exists():
            try:
                zip_path.unlink()
            except Exception:
                pass

    return None
