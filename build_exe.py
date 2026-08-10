import os
import pathlib
import subprocess
import sys


def build_exe():
    print("=======================================================")
    print("  DRIVESYNC STANDALONE WINDOWS EXE BUILDER             ")
    print("=======================================================\n")

    # 1. Install pyinstaller if not present
    try:
        import PyInstaller
    except ImportError:
        print("[*] Installing PyInstaller...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    project_root = pathlib.Path(__file__).parent.resolve()
    cli_entry = project_root / "src" / "drivesync" / "cli.py"

    dist_dir = project_root / "dist"
    target_exe = dist_dir / "drivesync.exe"
    exe_name = "drivesync"

    if target_exe.exists():
        try:
            target_exe.unlink()
        except OSError:
            import time
            subprocess.run(["taskkill", "/F", "/IM", "drivesync.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(0.5)
            try:
                target_exe.unlink()
            except OSError:
                print("[!] dist/drivesync.exe is locked by another user session. Building dist/drivesync_app.exe instead...")
                exe_name = "drivesync_app"

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--name",
        exe_name,
        "--clean",
        "--paths",
        str(project_root / "src"),
        str(cli_entry),
    ]

    print(f"[*] Building standalone drivesync.exe: {' '.join(cmd)}\n")
    res = subprocess.run(cmd, cwd=project_root)

    if res.returncode == 0:
        exe_path = project_root / "dist" / "drivesync.exe"
        print(f"\n[SUCCESS] Standalone Windows executable built successfully!")
        print(f"          Executable Path: {exe_path}")
        print("\nDeploy on any new PC simply by copying 'dist/drivesync.exe'!")
    else:
        print(f"\n[ERROR] PyInstaller build failed with exit code {res.returncode}")


if __name__ == "__main__":
    build_exe()
