"""
Run the IRIS FastAPI server with the current Python interpreter.

Usage:
  python scripts/run_server.py
  python scripts/run_server.py --port 8001
  python scripts/run_server.py app.main:app --host 0.0.0.0 --port 8000 --reload

Notes:
  - Uses sys.executable (no .venv assumptions).
  - Auto-installs dependencies from requirements.txt when uvicorn is missing.
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.runtime_env import ensure_python_modules

DEFAULT_APP = "app.main:app"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8000


def _is_port_free(host: str, port: int) -> bool:
    """Return True if a TCP bind is possible on host:port."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((host, port))
            return True
    except OSError:
        return False


def _find_pid_on_port(port: int) -> int | None:
    """Find a LISTENING PID for a port on Windows."""
    if os.name != "nt":
        return None

    try:
        output = subprocess.check_output(
            ["netstat", "-ano"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return None

    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue

        local_addr = parts[1]
        state = parts[3].upper()
        pid = parts[-1]

        if state != "LISTENING":
            continue

        try:
            local_port = int(local_addr.rsplit(":", 1)[-1])
            parsed_pid = int(pid)
        except (ValueError, IndexError):
            continue

        if local_port == port:
            return parsed_pid

    return None


def _try_kill_port(port: int) -> bool:
    """Try to terminate the process holding a port."""
    pid = _find_pid_on_port(port)
    if pid is None:
        return False

    print(f"[run_server] Port {port} is used by PID {pid}. Attempting to stop it...")
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid), "/T"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            import signal

            os.kill(pid, signal.SIGTERM)
    except Exception:
        return False

    # Give the OS a moment to release sockets.
    for _ in range(10):
        if _is_port_free("127.0.0.1", port) and _is_port_free("0.0.0.0", port):
            return True
        time.sleep(0.2)

    return False


def _find_available_port(host: str, start: int, max_tries: int = 10) -> int:
    """Find the next bindable port in [start, start + max_tries)."""
    for offset in range(max_tries):
        port = start + offset
        if _is_port_free(host, port):
            return port
    raise RuntimeError(
        f"No free port found in range {start}-{start + max_tries - 1}."
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the IRIS FastAPI server.",
        add_help=True,
    )
    parser.add_argument(
        "app",
        nargs="?",
        default=DEFAULT_APP,
        help=f"ASGI app path (default: {DEFAULT_APP})",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"Host (default: {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Port (default: {DEFAULT_PORT})")
    parser.add_argument("--reload", dest="reload", action="store_true", help="Enable auto-reload")
    parser.add_argument("--no-reload", dest="reload", action="store_false", help="Disable auto-reload")
    parser.add_argument(
        "--no-install",
        action="store_true",
        help="Do not auto-install requirements when dependencies are missing.",
    )
    parser.set_defaults(reload=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args, passthrough = parser.parse_known_args(argv)

    try:
        ensure_python_modules(
            {"uvicorn": "uvicorn"},
            auto_install=not args.no_install,
            python_executable=sys.executable,
        )
    except Exception as exc:
        print(f"[run_server] ERROR: {exc}")
        return 1

    port = args.port
    host = args.host

    if not _is_port_free(host, port):
        print(f"[run_server] WARNING: Port {port} is already in use.")
        if _try_kill_port(port):
            print(f"[run_server] Port {port} is now free.")
        else:
            try:
                fallback_port = _find_available_port(host, port + 1)
            except RuntimeError as exc:
                print(f"[run_server] ERROR: {exc}")
                return 1

            port = fallback_port
            print(f"[run_server] Using fallback port {port}.")
            print(f"[run_server] API docs:  http://localhost:{port}/docs")
            print(f"[run_server] Customer:  http://localhost:{port}/tools/customer_chat.html")
            print(f"[run_server] Owner:     http://localhost:{port}/tools/owner_chat.html")

    uvicorn_args = [args.app, "--host", host, "--port", str(port)]
    if args.reload:
        uvicorn_args.append("--reload")
    uvicorn_args.extend(passthrough)

    cmd = [sys.executable, "-m", "uvicorn", *uvicorn_args]
    print(f"[run_server] Starting: {' '.join(cmd)}")

    subprocess.check_call(cmd, cwd=str(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
