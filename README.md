# DriveSync

Lightweight background folder synchronization service for Windows to Google Drive.

DriveSync continuously monitors local folders using `watchfiles`, queues normalized file events into an SQLite database, and synchronizes changes to Google Drive using `rclone`'s local Remote Control (RC) HTTP API.

## Features

- **User Space Operation**: Installs and runs without administrative privileges (`%LOCALAPPDATA%\DriveSync` and HKCU registry).
- **Real-Time Monitoring**: Uses `watchfiles` with event stability checking and rapid save coalescing.
- **Crash Resilience**: SQLite WAL mode automatically recovers interrupted tasks on startup.
- **Safety Circuit Breaker**: Halts synchronization if a batch delete exceeds 50 files or 20% of tracked files.
- **Standalone Binary**: Can be compiled into a single executable without Python runtime requirements.

## Quickstart

### Prerequisites
- Windows 10/11
- Python 3.11+
- `rclone` executable

### Setup

1. Install package:
   ```cmd
   pip install -e .
   ```

2. (Optional) Configure personal Google Client ID and Secret (avoids shared rclone quota limits):
   - Create a project in [Google Cloud Console](https://console.cloud.google.com/).
   - Enable **Google Drive API**.
   - Create **OAuth client ID** credentials (Application type: **Desktop App**).
   - Set credentials in DriveSync:
     ```cmd
     drivesync auth credentials --client-id "YOUR_CLIENT_ID" --client-secret "YOUR_CLIENT_SECRET"
     ```

3. Authenticate Google Drive via rclone:
   ```cmd
   drivesync auth login
   ```

4. Register a folder:
   ```cmd
   drivesync folders add "C:\Users\User\Documents" --remote-path "DriveSync/Documents"
   ```

5. Start background service:
   ```cmd
   drivesync start
   ```

6. Enable autostart on user login (optional):
   ```cmd
   drivesync autostart enable
   ```

## Standalone Executable

To build a single-file `drivesync.exe` without Python runtime dependencies:

```cmd
python build_exe.py
```

The output executable is created at `dist/drivesync.exe`.

## CLI Reference

| Command | Subcommands / Actions | Description |
| :--- | :--- | :--- |
| `drivesync auth` | `login` / `status` / `clear` / `credentials` | Manage Google Drive authentication and API credentials |
| `drivesync folders` | `add` / `remove` / `list` | Manage watched local folders |
| `drivesync start` | — | Launch background daemon |
| `drivesync stop` | — | Stop background daemon |
| `drivesync status` | — | Show daemon PID, queue metrics, and circuit breaker state |
| `drivesync resync` | `[path]` | Trigger manual 2-way baseline sync |
| `drivesync trash` | `list` / `clear` | Manage local safety trash folder |
| `drivesync autostart` | `enable` / `disable` / `status` | Manage Windows startup registry key |
| `drivesync circuit-breaker` | `approve` / `status` / `reset` | Inspect or approve mass deletion halts |
| `drivesync doctor` | — | Run system health diagnostics |
| `drivesync logs` | `[--tail N]` | View service logs |

## Storage Locations

All application data resides in user space under `%LOCALAPPDATA%\DriveSync\`:

- `config.json` - Folder and remote configuration
- `drivesync.db` - SQLite WAL state database
- `drivesync.pid` - Process ID file
- `logs/drivesync.log` - Service logs

## Testing

Run unit and chaos test suites:

```cmd
python -m unittest discover -s tests -p "test_*.py"
```
