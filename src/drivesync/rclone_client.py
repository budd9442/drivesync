import base64
import json
import logging
import os
import pathlib
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class RcloneClient:
    """
    Manages local rclone background process daemon and interacts with it
    via its authenticated HTTP Remote Control (RC) API using stdlib urllib.
    """

    def __init__(
        self,
        rclone_binary: str,
        rclone_conf: str,
        rc_host: str = "127.0.0.1",
        rc_port: int = 5572,
        rc_user: str = "drivesync_user",
        rc_pass: str = "secret",
    ):
        self.rclone_binary = rclone_binary
        self.rclone_conf = rclone_conf
        self.rc_host = rc_host
        self.rc_port = rc_port
        self.rc_user = rc_user
        self.rc_pass = rc_pass
        self.base_url = f"http://{rc_host}:{rc_port}"

        self.process: Optional[subprocess.Popen] = None

    def _get_auth_header(self) -> str:
        credentials = f"{self.rc_user}:{self.rc_pass}"
        encoded = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")
        return f"Basic {encoded}"

    def start_daemon(self) -> bool:
        """Launch local rclone rcd process if not already running."""
        if self.is_alive():
            logger.info("rclone RC daemon is already running and reachable.")
            return True

        if not os.path.exists(self.rclone_binary) and self.rclone_binary == "rclone":
            # Check if rclone is in PATH
            pass

        cmd = [
            self.rclone_binary,
            "rcd",
            f"--rc-addr={self.rc_host}:{self.rc_port}",
            f"--rc-user={self.rc_user}",
            f"--rc-pass={self.rc_pass}",
            f"--config={self.rclone_conf}",
            "--rc-allow-origin=*",
        ]

        logger.info(f"Starting rclone daemon: {' '.join(cmd)}")
        try:
            # Creation flag for non-window process on Windows
            creation_flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creation_flags,
            )

            # Wait for daemon startup
            for _ in range(15):
                time.sleep(0.4)
                if self.is_alive():
                    logger.info("rclone daemon successfully started and responding.")
                    return True

            logger.error("rclone daemon process launched but failed to respond to health check.")
            return False

        except Exception as e:
            logger.error(f"Failed to launch rclone daemon process: {e}", exc_info=True)
            return False

    def stop_daemon(self) -> None:
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=3.0)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None
        logger.info("rclone daemon process stopped.")

    def _call_rc(
        self, endpoint: str, data: Optional[Dict[str, Any]] = None, timeout: float = 60.0
    ) -> Tuple[int, Dict[str, Any]]:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        payload = json.dumps(data or {}).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": self._get_auth_header(),
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = resp.status
                body = resp.read().decode("utf-8")
                res_json = json.loads(body) if body else {}
                return status, res_json
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8") if e.fp else ""
            res_json = json.loads(body) if body and body.startswith("{") else {"error": str(e)}
            return e.code, res_json
        except urllib.error.URLError as e:
            return 0, {"error": str(e.reason)}
        except Exception as e:
            return 0, {"error": str(e)}

    def is_alive(self) -> bool:
        """Health check endpoint ping."""
        code, resp = self._call_rc("core/pid", timeout=5.0)
        return code == 200 and "pid" in resp

    def copy_file(self, local_path: str, remote_name: str, remote_rel_path: str) -> Tuple[bool, str]:
        """
        Copy single file to remote.
        local_path: absolute local file path
        remote_name: e.g. "gdrive"
        remote_rel_path: destination relative file path e.g. "Docs/notes.txt"
        """
        local_p = pathlib.Path(local_path).resolve()
        src_fs = str(local_p.parent)
        src_remote = local_p.name

        remote_rel_p = pathlib.Path(remote_rel_path)
        dst_dir = remote_rel_p.parent.as_posix()
        if dst_dir == ".":
            dst_fs = f"{remote_name}:"
        else:
            dst_fs = f"{remote_name}:{dst_dir}"
        dst_remote = remote_rel_p.name

        payload = {
            "srcFs": src_fs,
            "srcRemote": src_remote,
            "dstFs": dst_fs,
            "dstRemote": dst_remote,
        }

        code, resp = self._call_rc("operations/copyfile", payload, timeout=120.0)
        if code == 200:
            return True, "Success"
        error_msg = resp.get("error", f"HTTP {code}")
        return False, f"copyfile failed: {error_msg}"

    def delete_file(self, remote_name: str, remote_rel_path: str) -> Tuple[bool, str]:
        """Delete remote file."""
        remote_rel_p = pathlib.Path(remote_rel_path)
        dst_dir = remote_rel_p.parent.as_posix()
        if dst_dir == ".":
            fs = f"{remote_name}:"
        else:
            fs = f"{remote_name}:{dst_dir}"
        remote_file = remote_rel_p.name

        payload = {"fs": fs, "remote": remote_file}

        code, resp = self._call_rc("operations/deletefile", payload, timeout=60.0)
        if code == 200:
            return True, "Success"
        error_msg = resp.get("error", f"HTTP {code}")
        return False, f"deletefile failed: {error_msg}"

    def stat_remote(self, remote_name: str, remote_rel_path: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """Stat a remote file/directory object."""
        remote_rel_p = pathlib.Path(remote_rel_path)
        dst_dir = remote_rel_p.parent.as_posix()
        fs = f"{remote_name}:" if dst_dir == "." else f"{remote_name}:{dst_dir}"
        payload = {"fs": fs, "remote": remote_rel_p.name}

        code, resp = self._call_rc("operations/stat", payload, timeout=30.0)
        if code == 200 and "item" in resp and resp["item"] is not None:
            return True, resp["item"]
        return False, None

    def move_file(self, src_fs: str, src_remote: str, dst_fs: str, dst_remote: str) -> bool:
        """Move a remote file from src to dst (e.g. to move to remote Trash folder)."""
        payload = {
            "srcFs": src_fs,
            "srcRemote": src_remote,
            "dstFs": dst_fs,
            "dstRemote": dst_remote,
        }
        code, resp = self._call_rc("operations/movefile", payload, timeout=60.0)
        return code == 200

    def run_bisync(
        self,
        local_path: str,
        remote_name: str,
        remote_path: str,
        resync: bool = False,
        conflict_resolve: str = "newer",
        max_deletes: int = 50,
    ) -> Tuple[bool, str]:
        """
        Execute native 2-way rclone bisync command with trash safety backup directories.
        """
        local_p = pathlib.Path(local_path).resolve()
        remote_spec = f"{remote_name}:{remote_path.strip('/')}"

        local_appdata = os.environ.get("LOCALAPPDATA", str(pathlib.Path.home() / ".drivesync"))
        local_trash = pathlib.Path(local_appdata) / "DriveSync" / "Trash" / "bisync_local"
        local_trash.mkdir(parents=True, exist_ok=True)
        remote_trash = f"{remote_name}:DriveSync_Trash"

        cmd = [
            self.rclone_binary,
            "bisync",
            str(local_p),
            remote_spec,
            f"--config={self.rclone_conf}",
            f"--conflict-resolve={conflict_resolve}",
            f"--max-delete={max_deletes}",
            f"--backup-dir1={str(local_trash)}",
            f"--backup-dir2={remote_trash}",
            "--verbose",
        ]

        if resync:
            cmd.append("--resync")

        logger.info(f"Running rclone bisync (resync={resync}): {' '.join(cmd)}")
        try:
            creation_flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
            res = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=creation_flags,
                timeout=300.0,
            )
            if res.returncode == 0:
                logger.info(f"rclone bisync completed successfully for {local_p}")
                return True, "Success"
            else:
                err_msg = res.stderr.strip() or res.stdout.strip()
                logger.error(f"rclone bisync failed (code {res.returncode}): {err_msg[:300]}")
                return False, f"bisync failed: {err_msg[:300]}"
        except subprocess.TimeoutExpired:
            logger.error(f"rclone bisync timed out after 300s for {local_p}")
            return False, "bisync timed out after 300s"
        except Exception as e:
            logger.error(f"rclone bisync error: {e}")
            return False, str(e)
