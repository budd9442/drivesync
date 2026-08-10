import logging
import os
import pathlib
import sys
from typing import Optional

logger = logging.getLogger(__name__)

REG_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
REG_VAL_NAME = "DriveSync"


def _get_daemon_command() -> str:
    """Construct command to run daemon in background."""
    python_exe = sys.executable
    return f'"{python_exe}" -m drivesync.daemon start'


def enable_autostart() -> bool:
    """
    Enable automatic startup at user login using per-user HKCU registry key.
    Requires ZERO admin rights.
    """
    cmd = _get_daemon_command()
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, REG_KEY_PATH, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.SetValueEx(key, REG_VAL_NAME, 0, winreg.REG_SZ, cmd)
            logger.info("Autostart successfully enabled in HKCU Run registry.")
            return True
        except Exception as e:
            logger.error(f"Failed to set HKCU registry autostart: {e}")

    # Fallback to Startup folder shortcut
    appdata = os.environ.get("APPDATA")
    if appdata:
        startup_dir = pathlib.Path(appdata) / r"Microsoft\Windows\Start Menu\Programs\Startup"
        if startup_dir.exists():
            cmd_file = startup_dir / "DriveSync.cmd"
            try:
                with open(cmd_file, "w", encoding="utf-8") as f:
                    f.write(f"@echo off\nstart /b {cmd}\n")
                logger.info(f"Autostart batch fallback created at {cmd_file}")
                return True
            except Exception as e:
                logger.error(f"Failed to write startup batch script: {e}")

    return False


def disable_autostart() -> bool:
    """Disable automatic startup at user login."""
    success = False
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, REG_KEY_PATH, 0, winreg.KEY_SET_VALUE
            ) as key:
                try:
                    winreg.DeleteValue(key, REG_VAL_NAME)
                    success = True
                except FileNotFoundError:
                    success = True
            logger.info("Autostart entry removed from HKCU Run registry.")
        except Exception as e:
            logger.error(f"Failed to remove HKCU registry autostart: {e}")

    # Also clean fallback file if exists
    appdata = os.environ.get("APPDATA")
    if appdata:
        cmd_file = pathlib.Path(appdata) / r"Microsoft\Windows\Start Menu\Programs\Startup\DriveSync.cmd"
        if cmd_file.exists():
            try:
                cmd_file.unlink()
                success = True
            except Exception as e:
                logger.error(f"Failed to delete startup batch script: {e}")

    return success


def is_autostart_enabled() -> bool:
    """Check whether autostart is currently enabled."""
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, REG_KEY_PATH, 0, winreg.KEY_READ
            ) as key:
                val, _ = winreg.QueryValueEx(key, REG_VAL_NAME)
                if val:
                    return True
        except Exception:
            pass

    appdata = os.environ.get("APPDATA")
    if appdata:
        cmd_file = pathlib.Path(appdata) / r"Microsoft\Windows\Start Menu\Programs\Startup\DriveSync.cmd"
        if cmd_file.exists():
            return True

    return False
