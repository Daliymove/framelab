#!/usr/bin/env python3
"""FrameLab local API server entrypoint."""

from __future__ import annotations

import argparse
import threading
import webbrowser

import uvicorn

from framelab.api import create_app
from framelab.config import Settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="FrameLab local personal AI image archive")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--open-browser", action="store_true")
    parser.add_argument("--reload", action="store_true", default=False)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = Settings.from_env()
    host = args.host or settings.host
    port = args.port or settings.port
    url = f"http://{host}:{port}"
    print(f"FrameLab: {url}")
    print(f"Data directory: {settings.data_dir}")
    print("Press Ctrl+C to stop.")
    if args.open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    if args.reload:
        uvicorn.run("framelab.api:create_app", host=host, port=port, log_level="info", factory=True, reload=True)
    else:
        app = create_app(settings)
        uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
