import logging
import os
import pathlib
import sys
from typing import Optional

logger = logging.getLogger(__name__)

REG_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
REG_VAL_NAME = "DriveSync"


def _get_daemon_command() -> str:
    """Construct native windowless autostart command for Windows registry."""
    python_exe = sys.executable
    if getattr(sys, "frozen", False):
        # Native PowerShell hidden process launch (0 extra files on disk, 0 VBS scripts)
        return f'powershell.exe -WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath \'{python_exe}\' -ArgumentList \'start\' -WindowStyle Hidden"'

    pythonw = os.path.join(os.path.dirname(python_exe), "pythonw.exe")
    if os.path.exists(pythonw):
        return f'"{pythonw}" -m drivesync.cli start'
    return f'"{python_exe}" -m drivesync.cli start'


def enable_autostart() -> bool:
    """
    Enable automatic startup at user login using per-user HKCU registry key.
    Uses native Windows hidden execution (zero VBS script files required).
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
                    f.write(f"@echo off\n{cmd}\n")
                logger.info(f"Autostart fallback script created at {cmd_file}")
                return True
            except Exception as e:
                logger.error(f"Failed to write startup script: {e}")

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

    # Clean legacy fallback scripts if exist
    appdata = os.environ.get("APPDATA")
    if appdata:
        startup_dir = pathlib.Path(appdata) / r"Microsoft\Windows\Start Menu\Programs\Startup"
        for name in ("DriveSync.vbs", "DriveSync.cmd"):
            cmd_file = startup_dir / name
            if cmd_file.exists():
                try:
                    cmd_file.unlink()
                    success = True
                except Exception as e:
                    logger.error(f"Failed to delete startup script: {e}")

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
        startup_dir = pathlib.Path(appdata) / r"Microsoft\Windows\Start Menu\Programs\Startup"
        for name in ("DriveSync.vbs", "DriveSync.cmd"):
            if (startup_dir / name).exists():
                return True

    return False
