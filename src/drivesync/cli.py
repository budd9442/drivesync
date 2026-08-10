import argparse
import os
import pathlib
import subprocess
import sys
import time
from typing import Optional

from drivesync import autostart
from drivesync.config import Config
from drivesync.rclone_client import RcloneClient
from drivesync.storage import Storage


def get_daemon_pid(config: Config) -> Optional[int]:
    if not config.pid_path.exists():
        return None
    try:
        with open(config.pid_path, "r", encoding="utf-8") as f:
            pid = int(f.read().strip())
        # Check if process is running
        if os.name == "nt":
            # Using tasklist or OpenProcess check
            cmd = f'tasklist /FI "PID eq {pid}" /NH'
            out = subprocess.check_output(cmd, shell=True).decode("utf-8", errors="ignore")
            if str(pid) in out:
                return pid
        else:
            os.kill(pid, 0)
            return pid
    except Exception:
        pass
    return None


def cmd_auth_login(args: argparse.Namespace, config: Config) -> None:
    rclone_bin = config.get("rclone_binary")
    rclone_conf = config.get("rclone_conf")
    remote_name = args.remote_path or config.get("rclone_remote", "gdrive")

    print(f"Launching Google Drive OAuth consent flow via rclone ({remote_name})...")
    print("rclone will open your web browser to authenticate with Google Drive.\n")

    cmd = [
        rclone_bin,
        "config",
        "create",
        remote_name,
        "drive",
        f"--config={rclone_conf}",
    ]

    try:
        res = subprocess.run(cmd)
        if res.returncode == 0:
            config.set("rclone_remote", remote_name)
            print(f"\n[SUCCESS] Google Drive authentication complete for remote '{remote_name}'.")
        else:
            print(f"\n[ERROR] rclone config returned code {res.returncode}")
    except Exception as e:
        print(f"\n[ERROR] Failed to run rclone login command: {e}")


def cmd_auth_status(args: argparse.Namespace, config: Config) -> None:
    rclone_bin = config.get("rclone_binary")
    rclone_conf = config.get("rclone_conf")
    remote_name = config.get("rclone_remote", "gdrive")

    print(f"Checking Google Drive auth status for remote '{remote_name}'...")
    cmd = [rclone_bin, "lsd", f"{remote_name}:", f"--config={rclone_conf}", "--max-depth=1"]

    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode == 0:
            print(f"[STATUS] Auth status: VALID (Remote '{remote_name}' is connected and authorized)")
        else:
            print(f"[STATUS] Auth status: INVALID or NOT CONFIGURED")
            print(f"  Error details: {res.stderr.strip()[:200]}")
    except Exception as e:
        print(f"[STATUS] Auth status error: {e}")


def cmd_auth_clear(args: argparse.Namespace, config: Config) -> None:
    rclone_conf = pathlib.Path(config.get("rclone_conf"))
    remote_name = config.get("rclone_remote", "gdrive")

    if rclone_conf.exists():
        try:
            import configparser
            cp = configparser.ConfigParser()
            cp.read(rclone_conf, encoding="utf-8")

            if cp.has_section(remote_name):
                cp.remove_section(remote_name)
                with open(rclone_conf, "w", encoding="utf-8") as f:
                    cp.write(f)
                print(f"[SUCCESS] Credentials for remote '{remote_name}' removed from {rclone_conf}.")
            else:
                print(f"No credentials section found for '{remote_name}'.")
        except Exception:
            rclone_conf.unlink(missing_ok=True)
            print(f"[SUCCESS] Removed rclone configuration file: {rclone_conf}")
    else:
        print("No stored rclone credentials found.")

    print("Run 'drivesync auth login' to authenticate with a Google Drive account.")


def cmd_folders_add(args: argparse.Namespace, config: Config) -> None:
    local_p = pathlib.Path(args.path).resolve()
    if not local_p.exists() or not local_p.is_dir():
        print(f"[ERROR] Directory does not exist or is not a folder: {args.path}")
        sys.exit(1)

    entry = config.add_folder(str(local_p), args.remote_path)
    print(f"[SUCCESS] Registered folder to sync:")
    print(f"  Local Path:  {entry['local_path']}")
    print(f"  Remote Path: {entry['remote_path']}")


def cmd_folders_remove(args: argparse.Namespace, config: Config) -> None:
    removed = config.remove_folder(args.path)
    if removed:
        print(f"[SUCCESS] Unregistered watched folder: {args.path}")
    else:
        print(f"[ERROR] Folder not found in watched folders list: {args.path}")


def cmd_folders_list(args: argparse.Namespace, config: Config) -> None:
    folders = config.watched_folders
    if not folders:
        print("No watched folders configured. Add one using 'drivesync folders add <path>'")
        return

    print(f"Configured Watched Folders ({len(folders)} total):\n")
    print(f"{'LOCAL PATH':<50} {'REMOTE DESTINATION':<30}")
    print("-" * 80)
    for f in folders:
        print(f"{f['local_path']:<50} {f['remote_path']:<30}")


def cmd_start(args: argparse.Namespace, config: Config) -> None:
    pid = get_daemon_pid(config)
    if pid is not None:
        print(f"DriveSync daemon is already running (PID: {pid}).")
        return

    print("Starting DriveSync background daemon...")
    python_exe = sys.executable

    creation_flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    cmd = [python_exe, "-m", "drivesync.daemon"]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
        )
        time.sleep(1.0)
        new_pid = get_daemon_pid(config)
        print(f"[SUCCESS] DriveSync daemon started (PID: {new_pid or proc.pid}).")
    except Exception as e:
        print(f"[ERROR] Failed to start daemon: {e}")


def cmd_stop(args: argparse.Namespace, config: Config) -> None:
    pid = get_daemon_pid(config)
    if pid is None:
        print("DriveSync daemon is not running.")
        return

    print(f"Stopping DriveSync daemon (PID: {pid})...")
    try:
        if os.name == "nt":
            subprocess.run(f"taskkill /PID {pid} /F", shell=True, check=False)
        else:
            os.kill(pid, 15)
        if config.pid_path.exists():
            config.pid_path.unlink()
        print("[SUCCESS] DriveSync daemon stopped.")
    except Exception as e:
        print(f"[ERROR] Failed to stop daemon: {e}")


def cmd_status(args: argparse.Namespace, config: Config) -> None:
    pid = get_daemon_pid(config)
    storage = Storage(config.db_path)
    pending_count = len(storage.get_pending_events(limit=1000))
    manifest_count = storage.get_manifest_count()
    tripped = storage.is_circuit_breaker_tripped()

    print("=== DriveSync Status ===")
    print(f"Daemon Status:    {'RUNNING (PID ' + str(pid) + ')' if pid else 'STOPPED'}")
    print(f"Watched Folders:  {len(config.watched_folders)}")
    print(f"Manifest Tracked: {manifest_count} files")
    print(f"Pending Events:   {pending_count}")
    print(f"Circuit Breaker:  {'TRIPPED (HALTED)' if tripped else 'OK (Active)'}")
    print(f"Conflict Policy:  Last-Write-Wins by mtime")
    print(f"Config File:      {config.config_path}")
    print(f"Database File:    {config.db_path}")
    print(f"Log File:         {config.log_path}")


def cmd_autostart(args: argparse.Namespace, config: Config) -> None:
    action = args.action.lower()
    if action == "enable":
        if autostart.enable_autostart():
            print("[SUCCESS] DriveSync autostart enabled for current user.")
        else:
            print("[ERROR] Failed to enable autostart.")
    elif action == "disable":
        if autostart.disable_autostart():
            print("[SUCCESS] DriveSync autostart disabled for current user.")
        else:
            print("[ERROR] Failed to disable autostart.")
    elif action == "status":
        enabled = autostart.is_autostart_enabled()
        print(f"Autostart Status: {'ENABLED' if enabled else 'DISABLED'}")


def cmd_circuit_breaker(args: argparse.Namespace, config: Config) -> None:
    action = args.cb_action.lower()
    storage = Storage(config.db_path)

    if action == "status":
        tripped = storage.is_circuit_breaker_tripped()
        deletions = storage.count_pending_deletions()
        print(f"Circuit Breaker State: {'TRIPPED' if tripped else 'OK'}")
        print(f"Pending Deletions:    {deletions}")
    elif action == "approve" or action == "reset":
        storage.set_circuit_breaker_tripped(False)
        print("[SUCCESS] Circuit breaker approved and reset. Pending deletion sync resumed.")


def cmd_doctor(args: argparse.Namespace, config: Config) -> None:
    print("=== DriveSync System Doctor & Health Checks ===\n")
    all_ok = True

    # 1. Check Rclone Binary
    rclone_bin = config.get("rclone_binary")
    bin_ok = os.path.exists(rclone_bin) or rclone_bin == "rclone"
    print(f"1. Rclone Binary:       [{'OK' if bin_ok else 'FAIL'}] {rclone_bin}")
    if not bin_ok:
        all_ok = False

    # 2. Check Rclone RC Daemon Health
    client = RcloneClient(
        rclone_binary=rclone_bin,
        rclone_conf=config.get("rclone_conf"),
        rc_host=config.get("rc_host", "127.0.0.1"),
        rc_port=config.get("rc_port", 5572),
        rc_user=config.get("rc_user", "drivesync_user"),
        rc_pass=config.get("rc_pass", "secret"),
    )
    rc_alive = client.is_alive()
    print(f"2. Rclone RC Daemon:     [{'OK' if rc_alive else 'WARNING - Stopped'}] (Host {config.get('rc_host')}:{config.get('rc_port')})")

    # 3. Watched Folders Accessibility
    folders = config.watched_folders
    print(f"3. Watched Folders:      [{'OK' if folders else 'WARNING - None configured'}] ({len(folders)} folders)")
    for f in folders:
        exists = pathlib.Path(f["local_path"]).exists()
        print(f"   - {f['local_path']} [{'ACCESSIBLE' if exists else 'MISSING'}]")
        if not exists:
            all_ok = False

    # 4. Database & WAL mode
    storage = Storage(config.db_path)
    db_path = pathlib.Path(config.db_path)
    db_ok = db_path.exists()
    print(f"4. Database & WAL:       [{'OK' if db_ok else 'FAIL'}] Path: {db_path}")

    # 5. Disk space check
    try:
        import shutil
        total, used, free = shutil.disk_usage(config.base_dir)
        free_mb = free / (1024 * 1024)
        print(f"5. Free Disk Space:      [{'OK' if free_mb > 500 else 'LOW'}] {free_mb:.1f} MB free in {config.base_dir}")
    except Exception:
        pass

    # 6. Circuit Breaker
    storage = Storage(config.db_path)
    tripped = storage.is_circuit_breaker_tripped()
    print(f"6. Circuit Breaker:      [{'OK' if not tripped else 'TRIPPED'}]")

    # 7. Autostart Status
    enabled = autostart.is_autostart_enabled()
    print(f"7. User Autostart:       [{'ENABLED' if enabled else 'DISABLED'}]")

    print("\nSummary:")
    print("  Conflict Resolution Policy: Last-Write-Wins by mtime")
    print(f"  Overall Health Check:       {'PASSED' if all_ok else 'ISSUES DETECTED'}")


def cmd_logs(args: argparse.Namespace, config: Config) -> None:
    log_path = config.log_path
    if not log_path.exists():
        print(f"Log file does not exist yet: {log_path}")
        return

    tail_lines = args.tail or 50
    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
        tail = lines[-tail_lines:] if len(lines) >= tail_lines else lines
        print("".join(tail), end="")


def cmd_resync(args: argparse.Namespace, config: Config) -> None:
    storage = Storage(config.db_path)
    client = RcloneClient(
        rclone_binary=config.get("rclone_binary"),
        rclone_conf=config.get("rclone_conf"),
    )
    remote_name = config.get("rclone_remote", "gdrive")
    folders = config.watched_folders

    if not folders:
        print("[ERROR] No watched folders configured.")
        return

    print("Triggering manual 2-way rclone bisync (--resync)...")
    for f in folders:
        local_p = f["local_path"]
        remote_p = f["remote_path"]
        if args.path and pathlib.Path(args.path).resolve() != pathlib.Path(local_p).resolve():
            continue

        print(f"Running bisync --resync for {local_p} -> {remote_name}:{remote_p}...")
        ok, msg = client.run_bisync(
            local_path=local_p,
            remote_name=remote_name,
            remote_path=remote_p,
            resync=True,
            conflict_resolve=config.get("bisync_conflict_resolve", "newer"),
            max_deletes=config.get("bisync_max_deletes", 50),
        )
        if ok:
            storage.set_resync_completed(local_p, True)
            storage.reconcile_folder(local_p, config.get("ignore_patterns", []))
            print(f"[SUCCESS] 2-Way bisync resync complete for {local_p}")
        else:
            print(f"[ERROR] bisync failed for {local_p}: {msg}")


def cmd_trash(args: argparse.Namespace, config: Config) -> None:
    from drivesync.trash import TrashManager

    trash_mgr = TrashManager()
    if args.trash_action == "list":
        items = trash_mgr.list_trash_items()
        if not items:
            print("Local safety trash folder is empty.")
        else:
            print(f"Local Safety Trash Items ({len(items)} files):")
            for item in items:
                print(f"  - {item}")
    elif args.trash_action == "clear":
        count = trash_mgr.clear_trash()
        print(f"[SUCCESS] Purged {count} file(s) from local safety trash.")


def main():
    parser = argparse.ArgumentParser(
        prog="drivesync",
        description="DriveSync - Zero-admin continuous Google Drive folder sync for Windows",
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Available subcommands")

    # Auth subparser
    auth_parser = subparsers.add_parser("auth", help="Manage Google Drive authentication")
    auth_sub = auth_parser.add_subparsers(dest="auth_action")
    login_p = auth_sub.add_parser("login", help="Run OAuth login flow via rclone")
    login_p.add_argument("--remote-path", help="Remote name to configure (default: gdrive)")
    auth_sub.add_parser("status", help="Check Google Drive auth status")
    auth_sub.add_parser("clear", help="Clear stored Google Drive credentials and log out")

    # Folders subparser
    folders_parser = subparsers.add_parser("folders", help="Manage watched local folders")
    folders_sub = folders_parser.add_subparsers(dest="folders_action")
    add_p = folders_sub.add_parser("add", help="Add a local folder to sync")
    add_p.add_argument("path", help="Local folder path")
    add_p.add_argument("--remote-path", help="Remote Drive destination folder path")
    rm_p = folders_sub.add_parser("remove", help="Remove a local folder from sync")
    rm_p.add_argument("path", help="Local folder path")
    folders_sub.add_parser("list", help="List all configured folders")

    # Daemon control subcommands
    subparsers.add_parser("start", help="Start background sync service")
    subparsers.add_parser("stop", help="Stop background sync service")
    subparsers.add_parser("status", help="Show current daemon and queue status")

    # Resync subcommand
    resync_p = subparsers.add_parser("resync", help="Run manual 2-way rclone bisync --resync")
    resync_p.add_argument("path", nargs="?", help="Optional local folder path to resync")

    # Trash subparser
    trash_parser = subparsers.add_parser("trash", help="Manage local safety trash folder")
    trash_parser.add_argument("trash_action", choices=["list", "clear"])

    # Autostart subparser
    autostart_parser = subparsers.add_parser("autostart", help="Configure user-level autostart")
    autostart_parser.add_argument("action", choices=["enable", "disable", "status"])

    # Circuit Breaker subparser
    cb_parser = subparsers.add_parser("circuit-breaker", help="Manage circuit breaker state")
    cb_parser.add_argument("cb_action", choices=["approve", "status", "reset"])

    # Doctor subcommand
    subparsers.add_parser("doctor", help="Run diagnostic health checks")

    # Logs subcommand
    logs_parser = subparsers.add_parser("logs", help="View background service logs")
    logs_parser.add_argument("--tail", type=int, default=50, help="Number of lines to show")

    args = parser.parse_args()
    config = Config()

    if args.subcommand == "auth":
        if args.auth_action == "login":
            cmd_auth_login(args, config)
        elif args.auth_action == "status":
            cmd_auth_status(args, config)
        elif args.auth_action == "clear":
            cmd_auth_clear(args, config)
        else:
            auth_parser.print_help()

    elif args.subcommand == "folders":
        if args.folders_action == "add":
            cmd_folders_add(args, config)
        elif args.folders_action == "remove":
            cmd_folders_remove(args, config)
        elif args.folders_action == "list":
            cmd_folders_list(args, config)
        else:
            folders_parser.print_help()

    elif args.subcommand == "start":
        cmd_start(args, config)
    elif args.subcommand == "stop":
        cmd_stop(args, config)
    elif args.subcommand == "status":
        cmd_status(args, config)
    elif args.subcommand == "resync":
        cmd_resync(args, config)
    elif args.subcommand == "trash":
        cmd_trash(args, config)
    elif args.subcommand == "autostart":
        cmd_autostart(args, config)
    elif args.subcommand == "circuit-breaker":
        cmd_circuit_breaker(args, config)
    elif args.subcommand == "doctor":
        cmd_doctor(args, config)
    elif args.subcommand == "logs":
        cmd_logs(args, config)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
