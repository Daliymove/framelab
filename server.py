#!/usr/bin/env python3
"""FrameLab local API server entrypoint."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import webbrowser
from typing import Callable

import uvicorn

from framelab.api import create_app
from framelab.config import Settings
from framelab.runtime import WORKER_PARENT_PID_ENV

_ctrl_handler_ref = None


def register_windows_shutdown_handler(cleanup_callbacks: list[Callable[[], None]] | None = None) -> None:
    """Register Win32 console control handler for graceful exit on shutdown/close."""
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    def console_ctrl_handler(ctrl_type: int) -> bool:
        # 5: CTRL_LOGOFF_EVENT, 6: CTRL_SHUTDOWN_EVENT (系统关机或注销时主动优雅退出)
        if ctrl_type in (5, 6):
            if cleanup_callbacks:
                for cb in cleanup_callbacks:
                    try:
                        cb()
                    except Exception:
                        pass
            os._exit(0)
        return False

    handler_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
    global _ctrl_handler_ref
    _ctrl_handler_ref = handler_type(console_ctrl_handler)
    ctypes.windll.kernel32.SetConsoleCtrlHandler(_ctrl_handler_ref, True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="FrameLab local personal AI image archive")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--open-browser", action="store_true")
    parser.add_argument("--reload", action="store_true", default=False)
    parser.add_argument("--with-worker", action="store_true", default=False)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = Settings.from_env()
    host = args.host or settings.host
    port = args.port or settings.port
    url = f"http://{host}:{port}"

    worker_proc: subprocess.Popen | None = None
    cleanup_callbacks: list[Callable[[], None]] = []

    def stop_worker() -> None:
        nonlocal worker_proc
        if worker_proc is not None and worker_proc.poll() is None:
            try:
                worker_proc.terminate()
            except Exception:
                try:
                    worker_proc.kill()
                except Exception:
                    pass

    if args.with_worker and not os.environ.get("FRAMELAB_WORKER_SPAWNED"):
        os.environ["FRAMELAB_WORKER_SPAWNED"] = "1"
        worker_env = os.environ.copy()
        worker_env[WORKER_PARENT_PID_ENV] = str(os.getpid())
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        worker_proc = subprocess.Popen(
            [sys.executable, "-m", "framelab.worker"],
            cwd=str(settings.frontend_dist.parent.parent),
            env=worker_env,
            creationflags=creationflags,
        )
        cleanup_callbacks.append(stop_worker)

    register_windows_shutdown_handler(cleanup_callbacks)

    print(f"FrameLab: {url}")
    print(f"Data directory: {settings.data_dir}")
    print("Press Ctrl+C to stop.")
    if args.open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        if args.reload:
            uvicorn.run("framelab.api:create_app", host=host, port=port, log_level="info", factory=True, reload=True)
        else:
            app = create_app(settings)
            uvicorn.run(app, host=host, port=port, log_level="info")
    finally:
        stop_worker()


if __name__ == "__main__":
    main()
