from __future__ import annotations

import json
import os
import signal
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import select

from .config import Settings
from .db import init_db, make_session_factory
from .imgbed import ImgBedClient, ImgBedError
from .models import Asset, GenerationJob, JobEvent, Provider, RemoteObject, utcnow
from .providers import OpenAIAsyncProvider, ProviderError
from .runtime import WORKER_PARENT_PID_ENV, WorkerRuntime
from .storage import absolute_media_path, create_asset_from_bytes


TERMINAL_JOB_STATES = {"succeeded", "failed", "canceled"}


class WorkerAlreadyRunning(RuntimeError):
    pass


def _parent_pid() -> int | None:
    try:
        value = int(os.environ.get(WORKER_PARENT_PID_ENV, ""))
    except ValueError:
        return None
    return value if value > 0 else None


def _stop_after_parent_exit(worker: "FrameLabWorker", done: threading.Event) -> None:
    worker.stop()
    if not done.wait(5.0):
        # An in-flight provider request may hold the main loop. The launcher is
        # already gone, so do not leave an orphan worker until its HTTP timeout.
        os._exit(0)


def _parent_watch(worker: "FrameLabWorker", done: threading.Event, parent_pid: int) -> None:
    """Stop an owned worker when its launcher exits, including a closed Windows console."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel32.WaitForSingleObject.restype = wintypes.DWORD
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel32.OpenProcess(0x00100000, False, parent_pid)
            if handle:
                try:
                    while not done.is_set():
                        result = kernel32.WaitForSingleObject(handle, 1000)
                        if result in (0, 0xFFFFFFFF):
                            _stop_after_parent_exit(worker, done)
                            return
                finally:
                    kernel32.CloseHandle(handle)
                return
        except (OSError, AttributeError):
            pass

    while not done.wait(1.0):
        try:
            os.kill(parent_pid, 0)
        except PermissionError:
            continue
        except OSError:
            _stop_after_parent_exit(worker, done)
            return


@contextmanager
def _single_worker_lock(data_dir: Path):
    """Hold an OS-level lock so only one process can consume the SQLite queue."""
    data_dir.mkdir(parents=True, exist_ok=True)
    handle = (data_dir / "framelab-worker.lock").open("a+b")
    handle.seek(0)
    if not handle.read(1):
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise WorkerAlreadyRunning("已有 FrameLab worker 正在运行。") from exc

    try:
        yield
    finally:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _json_load(value: str, fallback: Any = None) -> Any:
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _event(session, job_id: str, event_type: str, message: str, payload: Any = None) -> None:
    session.add(
        JobEvent(
            id=str(uuid.uuid4()),
            job_id=job_id,
            event_type=event_type,
            message=message[:500],
            payload_json=_json(payload or {}),
        )
    )


def _elapsed_ms(started_at) -> int | None:
    if started_at is None:
        return None
    return max(0, int((utcnow() - started_at).total_seconds() * 1000))


class FrameLabWorker:
    def __init__(self, settings: Settings | None = None, session_factory=None):
        self.settings = settings or Settings.from_env()
        self.settings.ensure_directories()
        self.session_factory = session_factory or make_session_factory(self.settings)
        init_db(self.session_factory, self.settings)
        self.stop_event = threading.Event()

    def stop(self) -> None:
        self.stop_event.set()

    def close(self) -> None:
        self.session_factory.kw["bind"].dispose()

    def recover_after_restart(self) -> None:
        """Do not resubmit ambiguous provider calls; only resume known task IDs."""
        with self.session_factory() as session:
            for job in session.scalars(
                select(GenerationJob).where(GenerationJob.status.in_(["running", "submitted"]))
            ):
                if job.upstream_task_id:
                    job.status = "queued"
                    job.progress_message = "服务重启后继续查询已有 provider 任务"
                    _event(session, job.id, "recovered", job.progress_message)
                else:
                    job.status = "failed"
                    job.error_message = "服务在 provider task_id 写入前停止，未自动重新提交。"
                    job.progress_message = "失败：未自动重试"
                    job.completed_at = utcnow()
                    job.elapsed_ms = _elapsed_ms(job.started_at)
                    _event(session, job.id, "failed", job.error_message)
            for remote in session.scalars(select(RemoteObject).where(RemoteObject.status == "syncing")):
                remote.status = "failed"
                remote.last_error = "服务在图床同步期间停止，请手动重新同步。"
            session.commit()

    def _claim_generation(self) -> str | None:
        with self.session_factory() as session:
            job = session.scalar(
                select(GenerationJob)
                .where(GenerationJob.status.in_(["queued", "running", "submitted"]))
                .order_by(GenerationJob.created_at)
                .limit(1)
            )
            if job is None:
                return None
            job.status = "running"
            job.started_at = job.started_at or utcnow()
            job.progress_message = "worker 正在处理"
            _event(session, job.id, "worker_started", job.progress_message)
            session.commit()
            return job.id

    def _claim_sync(self) -> str | None:
        with self.session_factory() as session:
            remote = session.scalar(
                select(RemoteObject)
                .where(RemoteObject.status == "pending")
                .order_by(RemoteObject.created_at)
                .limit(1)
            )
            if remote is None:
                return None
            remote.status = "syncing"
            remote.last_error = ""
            session.commit()
            return remote.id

    def _provider_for(self, session, job: GenerationJob) -> tuple[Provider, str]:
        provider = session.get(Provider, job.provider_id)
        if provider is None:
            raise ProviderError(f"provider {job.provider_id!r} 不存在。")
        api_key = os.environ.get(provider.api_key_env, "").strip()
        if not provider.base_url.strip() or not api_key:
            raise ProviderError(f"provider {provider.name} 未配置 base URL 或 API key。")
        return provider, api_key

    def process_generation(self, job_id: str) -> None:
        started_monotonic = time.monotonic()
        try:
            reference_image: tuple[bytes, str, str] | None = None
            with self.session_factory() as session:
                job = session.get(GenerationJob, job_id)
                if job is None or job.status in TERMINAL_JOB_STATES:
                    return
                provider, api_key = self._provider_for(session, job)
                client = OpenAIAsyncProvider(provider, api_key, self.settings)
                if job.reference_asset_id and not job.upstream_task_id:
                    reference_asset = session.get(Asset, job.reference_asset_id)
                    if reference_asset is None or reference_asset.deleted_at is not None:
                        raise ProviderError("参考图不存在或已被删除。")
                    try:
                        reference_path = absolute_media_path(reference_asset.local_path, self.settings)
                        reference_image = (
                            reference_path.read_bytes(),
                            reference_asset.mime_type,
                            reference_asset.original_filename,
                        )
                    except (OSError, ValueError) as exc:
                        raise ProviderError("读取参考图失败。") from exc
                session.expunge(job)

            with self.session_factory() as session:
                job = session.get(GenerationJob, job_id)
                if job is None or job.status in TERMINAL_JOB_STATES:
                    return
                task_id = job.upstream_task_id
                session.expunge(job)

            if not task_id:
                submitted = client.submit(job, reference_image=reference_image)
                with self.session_factory() as session:
                    current = session.get(GenerationJob, job_id)
                    if current is None:
                        return
                    if current.status in TERMINAL_JOB_STATES:
                        if current.status == "canceled":
                            current.upstream_task_id = submitted.task_id
                            current.submitted_at = utcnow()
                            current.request_json = _json(submitted.request)
                            _event(
                                session,
                                current.id,
                                "submitted_after_cancel",
                                "provider 任务已提交，但本地任务已停止",
                                submitted.response,
                            )
                            session.commit()
                        return
                    current.upstream_task_id = submitted.task_id
                    current.submitted_at = utcnow()
                    current.status = "submitted"
                    current.progress_message = f"已提交 provider 任务：{submitted.task_id}"
                    current.request_json = _json(submitted.request)
                    _event(session, current.id, "submitted", current.progress_message, submitted.response)
                    task_id = current.upstream_task_id
                    session.commit()

            with self.session_factory() as session:
                job = session.get(GenerationJob, job_id)
                if job is None or not job.upstream_task_id:
                    return
                task_id = job.upstream_task_id
                started_at = job.started_at or utcnow()
                deadline = time.monotonic() + max(1, job.timeout_seconds)
                request_snapshot = _json_load(job.request_json, {})
                submit_endpoint = request_snapshot.get("endpoint") if isinstance(request_snapshot, dict) else None
                # A task recovered after a restart still gets the remaining wall-clock budget.
                if job.started_at:
                    elapsed = (utcnow() - started_at).total_seconds()
                    deadline = time.monotonic() + max(1, job.timeout_seconds - elapsed)

            last_poll_state: str | None = None

            def on_poll(status: str, payload: Any) -> None:
                nonlocal last_poll_state
                poll_state = status or "processing"
                with self.session_factory() as progress_session:
                    current = progress_session.get(GenerationJob, job_id)
                    if current is not None and current.status not in TERMINAL_JOB_STATES:
                        current.status = "running"
                        current.progress_message = (
                            "provider 查询连接中断，正在继续等待原任务"
                            if poll_state == "reconnecting"
                            else f"provider 状态：{poll_state}"
                        )
                        if poll_state != last_poll_state:
                            _event(progress_session, job_id, "poll", current.progress_message, payload)
                            last_poll_state = poll_state
                        progress_session.commit()

            if submit_endpoint:
                result = client.poll_until_done(task_id, deadline, on_poll=on_poll, submit_endpoint=submit_endpoint)
            else:
                result = client.poll_until_done(task_id, deadline, on_poll=on_poll)
            with self.session_factory() as session:
                job = session.get(GenerationJob, job_id)
                if job is None or job.status in TERMINAL_JOB_STATES:
                    return
                asset, _created = create_asset_from_bytes(
                    session,
                    self.settings,
                    result.image_bytes,
                    f"generated-{job.id}{result.suffix}",
                    content_type=None,
                    source="generated",
                    prompt_override="",
                    sync_enabled=job.sync_enabled,
                    deduplicate=False,
                )
                job.asset_id = asset.id
                job.response_json = _json(result.response)
                job.status = "succeeded"
                job.progress_message = "生成完成，原图已本地入库"
                job.completed_at = utcnow()
                job.elapsed_ms = _elapsed_ms(job.started_at) or int((time.monotonic() - started_monotonic) * 1000)
                _event(session, job.id, "succeeded", job.progress_message, {"asset_id": asset.id})
                if job.sync_enabled:
                    session.add(
                        RemoteObject(
                            id=str(uuid.uuid4()),
                            asset_id=asset.id,
                            provider="cloudflare-imgbed",
                            status="pending",
                        )
                    )
                else:
                    session.add(
                        RemoteObject(
                            id=str(uuid.uuid4()),
                            asset_id=asset.id,
                            provider="cloudflare-imgbed",
                            status="disabled",
                        )
                    )
                session.commit()
        except Exception as exc:
            message = str(exc)[:4000]
            with self.session_factory() as session:
                job = session.get(GenerationJob, job_id)
                if job is not None and job.status not in {"succeeded", "canceled"}:
                    job.status = "failed"
                    job.error_message = message
                    job.progress_message = "失败：未自动重试"
                    job.completed_at = utcnow()
                    job.elapsed_ms = _elapsed_ms(job.started_at) or int((time.monotonic() - started_monotonic) * 1000)
                    _event(session, job.id, "failed", message)
                    session.commit()

    def process_sync(self, remote_id: str) -> None:
        try:
            with self.session_factory() as session:
                remote = session.get(RemoteObject, remote_id)
                if remote is None or remote.status != "syncing":
                    return
                asset = session.get(Asset, remote.asset_id)
                if asset is None:
                    raise ImgBedError("本地资源不存在。")
                if asset.deleted_at is not None:
                    raise ImgBedError("图片已删除，已跳过图床同步。")
                path = absolute_media_path(asset.local_path, self.settings)
                filename = asset.filename
                mime_type = asset.mime_type
            result = ImgBedClient(self.settings).upload(path, filename, mime_type)
            with self.session_factory() as session:
                remote = session.get(RemoteObject, remote_id)
                if remote is None:
                    return
                remote.status = "succeeded"
                remote.remote_url = result["remote_url"]
                remote.remote_id = result.get("remote_id", "")
                remote.response_json = _json(result.get("response", {}))
                remote.synced_at = utcnow()
                remote.last_error = ""
                session.commit()
        except Exception as exc:
            with self.session_factory() as session:
                remote = session.get(RemoteObject, remote_id)
                if remote is not None:
                    remote.status = "failed"
                    remote.last_error = str(exc)[:4000]
                    session.commit()

    def run_once(self) -> bool:
        job_id = self._claim_generation()
        if job_id:
            self.process_generation(job_id)
            return True
        remote_id = self._claim_sync()
        if remote_id:
            self.process_sync(remote_id)
            return True
        return False

    def run_forever(self, interval: float = 0.5, runtime: WorkerRuntime | None = None) -> None:
        self.recover_after_restart()
        heartbeat_stop = threading.Event()
        heartbeat_thread: threading.Thread | None = None
        if runtime is not None:
            # Provider polling can block the worker for several minutes. Keep the
            # liveness signal independent from that work so the runtime page does
            # not mark a healthy worker as stopped.
            def heartbeat_loop() -> None:
                while not heartbeat_stop.wait(1.0):
                    runtime.heartbeat()

            heartbeat_thread = threading.Thread(
                target=heartbeat_loop,
                name="framelab-worker-heartbeat",
                daemon=True,
            )
            heartbeat_thread.start()
        try:
            while not self.stop_event.is_set():
                if runtime is not None:
                    if runtime.stop_requested():
                        self.stop()
                        continue
                did_work = self.run_once()
                self.stop_event.wait(0.05 if did_work else interval)
        finally:
            heartbeat_stop.set()
            if heartbeat_thread is not None:
                heartbeat_thread.join(timeout=2.0)
            if runtime is not None:
                runtime.close()
            self.close()


def main() -> None:
    settings = Settings.from_env()
    try:
        with _single_worker_lock(settings.data_dir):
            runtime = WorkerRuntime(settings)
            worker = FrameLabWorker(settings)
            parent_done = threading.Event()
            parent_thread: threading.Thread | None = None
            parent_pid = _parent_pid()
            if parent_pid is not None:
                parent_thread = threading.Thread(
                    target=_parent_watch,
                    args=(worker, parent_done, parent_pid),
                    name="framelab-worker-parent-watch",
                    daemon=True,
                )
                parent_thread.start()
            for signal_name in ("SIGINT", "SIGTERM"):
                signal_number = getattr(signal, signal_name, None)
                if signal_number is not None:
                    signal.signal(signal_number, lambda *_args: worker.stop())
            try:
                worker.run_forever(runtime=runtime)
            finally:
                parent_done.set()
                if parent_thread is not None:
                    parent_thread.join(timeout=2.0)
    except WorkerAlreadyRunning as exc:
        print(exc)


if __name__ == "__main__":
    main()
