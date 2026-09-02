from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Settings


HEARTBEAT_TIMEOUT_SECONDS = 5
STALE_WORKER_RETENTION_SECONDS = 60
WORKER_PARENT_PID_ENV = "FRAMELAB_WORKER_PARENT_PID"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def runtime_dir(settings: Settings) -> Path:
    path = settings.data_dir / "runtime"
    path.mkdir(parents=True, exist_ok=True)
    return path


class WorkerRuntime:
    def __init__(self, settings: Settings):
        self.pid = os.getpid()
        self.started_at = _now()
        directory = runtime_dir(settings)
        self.state_path = directory / f"worker-{self.pid}.json"
        self.stop_path = directory / f"worker-{self.pid}.stop"
        self.stop_path.unlink(missing_ok=True)
        self.heartbeat()

    def heartbeat(self, status: str = "running") -> None:
        payload = {
            "pid": self.pid,
            "status": status,
            "started_at": _iso(self.started_at),
            "heartbeat_at": _iso(_now()),
        }
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
        temporary.replace(self.state_path)

    def stop_requested(self) -> bool:
        return self.stop_path.exists()

    def close(self) -> None:
        self.stop_path.unlink(missing_ok=True)
        self.state_path.unlink(missing_ok=True)


def list_workers(settings: Settings) -> list[dict[str, Any]]:
    now = _now()
    values: list[dict[str, Any]] = []
    for path in runtime_dir(settings).glob("worker-*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            heartbeat = _parse(str(payload.get("heartbeat_at", "")))
            age = (now - heartbeat).total_seconds() if heartbeat else None
            active = payload.get("status") == "running" and age is not None and age <= HEARTBEAT_TIMEOUT_SECONDS
            if not active and (age is None or age > STALE_WORKER_RETENTION_SECONDS):
                path.unlink(missing_ok=True)
                path.with_suffix(".stop").unlink(missing_ok=True)
                continue
            values.append(
                {
                    "pid": int(payload["pid"]),
                    "status": "running" if active else "stopped",
                    "started_at": payload.get("started_at"),
                    "heartbeat_at": payload.get("heartbeat_at"),
                    "heartbeat_age_seconds": round(age, 1) if age is not None else None,
                }
            )
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            continue
    values.sort(key=lambda item: item.get("started_at") or "", reverse=True)
    values.sort(key=lambda item: item["status"] != "running")
    return values[:20]


def request_worker_stop(settings: Settings, pid: int) -> bool:
    worker = next((item for item in list_workers(settings) if item["pid"] == pid and item["status"] == "running"), None)
    if worker is None:
        return False
    (runtime_dir(settings) / f"worker-{pid}.stop").write_text("stop\n", encoding="ascii")
    return True
