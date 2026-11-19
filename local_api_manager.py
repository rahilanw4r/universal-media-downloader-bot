"""
Local Telegram Bot API Server Manager.
Enables native 2,000 MB (2 GB) file uploads and downloads by managing the
local telegram-bot-api daemon.
"""

import atexit
import logging
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("local_api_manager")

_server_process: Optional[subprocess.Popen] = None


def is_server_listening(host: str = "127.0.0.1", port: int = 8081, timeout: float = 1.0) -> bool:
    """Check if a service is actively listening on the given host and port."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def find_bot_api_binary() -> Optional[Path]:
    """Locate the telegram-bot-api executable on the system."""
    candidate = shutil.which("telegram-bot-api")
    if candidate:
        return Path(candidate)

    home_local = Path.home() / ".local" / "bin" / "telegram-bot-api"
    if home_local.exists() and os.access(home_local, os.X_OK):
        return home_local

    for p in ("/usr/local/bin/telegram-bot-api", "/usr/bin/telegram-bot-api"):
        pp = Path(p)
        if pp.exists() and os.access(pp, os.X_OK):
            return pp

    return None


def start_local_bot_api(
    api_id: str,
    api_hash: str,
    port: int = 8081,
    work_dir: Optional[Path] = None,
    log_file: Optional[Path] = None,
) -> bool:
    """
    Ensure the local telegram-bot-api daemon is running on port 8081.
    If already listening, returns True immediately.
    If not listening, spawns the server as a background daemon process.
    """
    global _server_process

    if is_server_listening("127.0.0.1", port):
        logger.info("Local Bot API server is already running on port %d.", port)
        return True

    binary = find_bot_api_binary()
    if not binary:
        logger.warning("telegram-bot-api binary not found on this system.")
        return False

    if not api_id or not api_hash:
        logger.warning("TELEGRAM_API_ID or TELEGRAM_API_HASH not configured.")
        return False

    work_dir = work_dir or (Path.home() / ".telegram-bot-api")
    temp_dir = work_dir / "temp"
    work_dir.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_file or (work_dir / "server.log")

    cmd = [
        str(binary),
        f"--api-id={api_id}",
        f"--api-hash={api_hash}",
        "--local",
        f"--http-port={port}",
        f"--dir={work_dir.resolve()}",
        f"--temp-dir={temp_dir.resolve()}",
        f"--log={log_file.resolve()}",
        "--verbosity=1",
    ]

    try:
        logger.info("Launching local Telegram Bot API server on port %d...", port)
        _server_process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        # Wait up to 5 seconds for the server to bind and respond
        start_time = time.time()
        while time.time() - start_time < 5.0:
            if is_server_listening("127.0.0.1", port):
                logger.info("Local Telegram Bot API server successfully started on port %d (PID %d).", port, _server_process.pid)
                return True
            time.sleep(0.2)

        logger.warning("Local Telegram Bot API server failed to respond within 5s.")
        return False
    except Exception as e:
        logger.error("Failed to start local Telegram Bot API server: %s", e)
        return False


def stop_local_bot_api():
    """Cleanly terminate the local Bot API server process if managed by this instance."""
    global _server_process
    if _server_process and _server_process.poll() is None:
        try:
            logger.info("Stopping local Telegram Bot API server (PID %d)...", _server_process.pid)
            _server_process.terminate()
            _server_process.wait(timeout=3)
        except Exception:
            try:
                _server_process.kill()
            except Exception:
                pass
        _server_process = None


def get_local_api_status(port: int = 8081) -> Dict[str, Any]:
    """Return status and capability information for the local Bot API server."""
    listening = is_server_listening("127.0.0.1", port)
    binary = find_bot_api_binary()
    version = None
    if binary:
        try:
            res = subprocess.run(
                [str(binary), "--version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=2,
            )
            version = (res.stdout or "").strip()
        except Exception:
            version = None

    return {
        "is_running": listening,
        "port": port,
        "binary_path": str(binary) if binary else None,
        "version": version,
        "max_upload_mb": 2000.0 if listening else 50.0,
        "supports_2gb": listening,
        "local_mode": True if listening else False,
    }
