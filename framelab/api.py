from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import uuid
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session

from .config import Settings, persist_media_dir, public_url
from .db import init_db, make_session_factory
from .models import Asset, AssetTag, AssetVariant, GenerationJob, JobEvent, Provider, RemoteObject, Tag, utcnow
from .runtime import list_workers, request_worker_stop
from .storage import absolute_media_path, create_asset_from_bytes, get_variant, media_reference


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=32000)
    model: str = Field(default="gpt-image-2", min_length=1, max_length=120)
    size: str = Field(default="auto", max_length=40)
    quality: str = Field(default="high", max_length=30)
    timeout: int = Field(default=600, ge=30, le=1800)
    provider_id: str | None = None
    parent_job_id: str | None = None
    reference_asset_id: str | None = Field(default=None, max_length=36)
    sync_enabled: bool = True


class AssetUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=255)
    notes: str | None = None
    prompt_override: str | None = None
    sync_enabled: bool | None = None


class MediaDirectoryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    media_dir: str = Field(min_length=1, max_length=2000)


class BulkTagsRequest(BaseModel):
    asset_ids: list[str] = Field(min_length=1, max_length=200)
    tags: list[str] = Field(min_length=1, max_length=30)


class BulkSyncRequest(BaseModel):
    asset_ids: list[str] = Field(min_length=1, max_length=200)


class ProviderInput(BaseModel):
    id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._-]+$")
    name: str = Field(min_length=1, max_length=200)
    kind: str = Field(default="openai-async", max_length=80)
    base_url: str = Field(min_length=1, max_length=1000)
    api_key_env: str = Field(default="CODEX_IMAGE_API_KEY", max_length=200)
    enabled: bool = True


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _json_load(value: str, fallback: Any = None) -> Any:
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _parse_tags(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        decoded = json.loads(value)
        if isinstance(decoded, list):
            values = decoded
        else:
            values = value.split(",")
    except ValueError:
        values = value.split(",")
    result: list[str] = []
    for item in values:
        name = re.sub(r"\s+", " ", str(item)).strip()
        if name and name not in result:
            result.append(name[:80])
    return result[:30]


def _ensure_tags(session: Session, names: list[str]) -> list[Tag]:
    tags: list[Tag] = []
    for raw_name in names:
        name = re.sub(r"\s+", " ", raw_name).strip()[:80]
        if not name:
            continue
        tag = session.scalar(select(Tag).where(func.lower(Tag.name) == name.lower()))
        if tag is None:
            tag = Tag(id=str(uuid.uuid4()), name=name)
            session.add(tag)
            session.flush()
        tags.append(tag)
    return tags


def _set_asset_tags(session: Session, asset_id: str, names: list[str], *, replace: bool = False) -> None:
    tags = _ensure_tags(session, names)
    if replace:
        session.query(AssetTag).filter(AssetTag.asset_id == asset_id).delete(synchronize_session=False)
    existing = {
        row.tag_id
        for row in session.scalars(select(AssetTag).where(AssetTag.asset_id == asset_id))
    }
    for tag in tags:
        if tag.id not in existing:
            session.add(AssetTag(asset_id=asset_id, tag_id=tag.id))


def _asset_tags(session: Session, asset_id: str) -> list[str]:
    return list(
        session.scalars(
            select(Tag.name)
            .join(AssetTag, AssetTag.tag_id == Tag.id)
            .where(AssetTag.asset_id == asset_id)
            .order_by(Tag.name)
        )
    )


def _remote(session: Session, asset_id: str) -> RemoteObject | None:
    return session.scalar(
        select(RemoteObject).where(RemoteObject.asset_id == asset_id, RemoteObject.provider == "cloudflare-imgbed")
    )


def _ensure_remote(session: Session, asset: Asset) -> RemoteObject:
    remote = _remote(session, asset.id)
    if remote is None:
        remote = RemoteObject(
            id=str(uuid.uuid4()),
            asset_id=asset.id,
            provider="cloudflare-imgbed",
            status="pending" if asset.sync_enabled else "disabled",
        )
        session.add(remote)
    return remote


def _variant_url(asset_id: str, kind: str) -> str:
    return f"/api/assets/{asset_id}/file?variant={kind}"


def _asset_payload(session: Session, asset: Asset, settings: Settings, *, detail: bool = False) -> dict[str, Any]:
    remote = _remote(session, asset.id)
    generation = session.scalar(select(GenerationJob).where(GenerationJob.asset_id == asset.id))
    value: dict[str, Any] = {
        "id": asset.id,
        "filename": asset.filename,
        "original_filename": asset.original_filename,
        "mime_type": asset.mime_type,
        "size_bytes": asset.size_bytes,
        "sha256": asset.sha256,
        "width": asset.width,
        "height": asset.height,
        "source": asset.source,
        "title": asset.title,
        "notes": asset.notes,
        "prompt_override": asset.prompt_override,
        "sync_enabled": asset.sync_enabled,
        "created_at": _iso(asset.created_at),
        "updated_at": _iso(asset.updated_at),
        "thumbnail_url": _variant_url(asset.id, "thumbnail"),
        "preview_url": _variant_url(asset.id, "preview"),
        "original_url": _variant_url(asset.id, "original"),
        "tags": _asset_tags(session, asset.id),
        "remote": {
            "provider": remote.provider if remote else "cloudflare-imgbed",
            "status": remote.status if remote else ("disabled" if not asset.sync_enabled else "pending"),
            "url": remote.remote_url if remote else "",
            "remote_id": remote.remote_id if remote else "",
            "synced_at": _iso(remote.synced_at) if remote else None,
            "updated_at": _iso(remote.updated_at) if remote else None,
            "last_error": remote.last_error if remote else "",
        },
    }
    if detail:
        value["metadata"] = _json_load(asset.metadata_json, {})
        if generation:
            value["generation"] = _job_payload(session, generation, include_events=True)
        else:
            value["generation"] = None
    return value


def _job_payload(session: Session, job: GenerationJob, *, include_events: bool = False) -> dict[str, Any]:
    reference_asset = session.get(Asset, job.reference_asset_id) if job.reference_asset_id else None
    value: dict[str, Any] = {
        "id": job.id,
        "parent_job_id": job.parent_job_id,
        "asset_id": job.asset_id,
        "reference_asset_id": job.reference_asset_id,
        "provider_id": job.provider_id,
        "provider_name": job.provider_name_snapshot,
        "provider_url": job.provider_url_snapshot,
        "model": job.model,
        "prompt": job.prompt,
        "size": job.size,
        "quality": job.quality,
        "timeout_seconds": job.timeout_seconds,
        "sync_enabled": job.sync_enabled,
        "status": job.status,
        "progress_message": job.progress_message,
        "upstream_task_id": job.upstream_task_id,
        "error_message": job.error_message,
        "submitted_at": _iso(job.submitted_at),
        "started_at": _iso(job.started_at),
        "completed_at": _iso(job.completed_at),
        "elapsed_ms": job.elapsed_ms,
        "created_at": _iso(job.created_at),
    }
    if reference_asset is not None and reference_asset.deleted_at is None:
        value["reference_asset"] = {
            "id": reference_asset.id,
            "filename": reference_asset.filename,
            "original_filename": reference_asset.original_filename,
            "thumbnail_url": _variant_url(reference_asset.id, "thumbnail"),
        }
    if job.asset_id:
        value["asset_url"] = f"/api/assets/{job.asset_id}"
    if include_events:
        value["request"] = _json_load(job.request_json, {})
        value["response"] = _json_load(job.response_json, {})
        value["events"] = [
            {
                "id": event.id,
                "type": event.event_type,
                "message": event.message,
                "created_at": _iso(event.created_at),
                "payload": _json_load(event.payload_json, {}),
            }
            for event in session.scalars(
                select(JobEvent).where(JobEvent.job_id == job.id).order_by(JobEvent.created_at)
            )
        ]
    return value


def _path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _resolve_media_dir(value: str) -> Path:
    raw_value = value.strip()
    if not raw_value or any(char in raw_value for char in "\x00\r\n"):
        raise ValueError("图片目录不能为空，且不能包含换行或无效字符。")
    candidate = Path(raw_value).expanduser()
    if not candidate.is_absolute():
        raise ValueError("图片目录必须使用绝对路径。")
    candidate = candidate.resolve()
    if candidate == Path(candidate.anchor):
        raise ValueError("图片目录不能直接指向磁盘根目录。")
    return candidate


def _move_media_contents(source: Path, target: Path) -> list[tuple[Path, Path]]:
    if _path_is_within(target, source) or _path_is_within(source, target):
        raise ValueError("新图片目录不能是当前图片目录本身或其父子目录。")
    if target.exists():
        if not target.is_dir():
            raise ValueError("新图片目录已被同名文件占用。")
        if any(target.iterdir()):
            raise ValueError("新图片目录必须为空，避免覆盖已有文件。")
    else:
        target.mkdir(parents=True, exist_ok=True)

    moved: list[tuple[Path, Path]] = []
    try:
        for child in list(source.iterdir()):
            destination = target / child.name
            shutil.move(str(child), str(destination))
            moved.append((destination, child))
    except Exception:
        _restore_media_contents(moved)
        raise
    return moved


def _restore_media_contents(moved: list[tuple[Path, Path]]) -> None:
    for destination, source in reversed(moved):
        if destination.exists():
            shutil.move(str(destination), str(source))


def _migrate_media_dir(session_factory, settings: Settings, target: Path) -> None:
    source = settings.media_dir.resolve()
    target = target.resolve()
    if source == target:
        persist_media_dir(target)
        return

    records: list[tuple[str, str, str, str]] = []
    with session_factory() as session:
        assets = list(session.scalars(select(Asset)))
        variants = list(session.scalars(select(AssetVariant)))
        for row in assets:
            path = absolute_media_path(row.local_path, settings)
            if not path.is_file():
                raise ValueError(f"找不到图片文件：{row.local_path}")
            records.append(("asset", row.id, row.local_path, media_reference(path, settings)))
        for row in variants:
            path = absolute_media_path(row.local_path, settings)
            if not path.is_file():
                raise ValueError(f"找不到图片衍生文件：{row.local_path}")
            records.append(("variant", row.id, row.local_path, media_reference(path, settings)))

        moved = _move_media_contents(source, target)
        try:
            for kind, row_id, _old_path, new_path in records:
                row = session.get(Asset if kind == "asset" else AssetVariant, row_id)
                if row is not None:
                    row.local_path = new_path
            session.commit()
        except Exception:
            session.rollback()
            _restore_media_contents(moved)
            raise

    try:
        persist_media_dir(target)
    except Exception:
        try:
            with session_factory() as session:
                for kind, row_id, old_path, _new_path in records:
                    row = session.get(Asset if kind == "asset" else AssetVariant, row_id)
                    if row is not None:
                        row.local_path = old_path
                session.commit()
        finally:
            _restore_media_contents(moved)
        raise


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.ensure_directories()
    session_factory = make_session_factory(settings)
    init_db(session_factory, settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        session_factory.kw["bind"].dispose()

    app = FastAPI(title="FrameLab", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.session_factory = session_factory

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "version": "0.1.0"}

    @app.get("/api/runtime")
    def runtime_status() -> dict[str, Any]:
        workers = list_workers(settings)
        return {
            "api": {"pid": os.getpid(), "status": "running"},
            "active_worker_count": sum(item["status"] == "running" for item in workers),
            "workers": workers,
        }

    @app.post("/api/runtime/workers", status_code=202)
    def start_worker() -> dict[str, Any]:
        active = [item for item in list_workers(settings) if item["status"] == "running"]
        if active:
            return {"started": False, "message": "已有 worker 正在运行。", "worker": active[0]}
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        process = subprocess.Popen(
            [sys.executable, "-m", "framelab.worker"],
            cwd=str(settings.frontend_dist.parent.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
        )
        return {"started": True, "pid": process.pid}

    @app.post("/api/runtime/workers/{pid}/stop", status_code=202)
    def stop_worker(pid: int) -> dict[str, Any]:
        if not request_worker_stop(settings, pid):
            raise HTTPException(status_code=404, detail="worker 不存在或已经停止。")
        return {"stopping": True, "pid": pid}

    @app.get("/api/config")
    def config() -> dict[str, Any]:
        with session_factory() as session:
            provider_count = session.scalar(select(func.count()).select_from(Provider)) or 0
            asset_count = session.scalar(select(func.count()).select_from(Asset).where(Asset.deleted_at.is_(None))) or 0
            pending_count = session.scalar(
                select(func.count()).select_from(RemoteObject).where(RemoteObject.status.in_(["pending", "failed"]))
            ) or 0
        return {
            "ready": settings.ready_for_generation,
            "provider_count": provider_count,
            "provider_ready": settings.ready_for_generation,
            "provider": {
                "id": settings.image_provider_id,
                "name": settings.image_provider_name,
                "base_url": settings.image_base_url_public,
            },
            "imgbed": {
                "enabled": settings.imgbed_enabled,
                "configured": bool(settings.imgbed_base_url),
                "base_url": settings.imgbed_base_url_public,
            },
            "data_dir": str(settings.data_dir),
            "media_dir": str(settings.media_dir),
            "database_path": str(settings.database_path),
            "asset_count": asset_count,
            "pending_sync_count": pending_count,
            "retries": 0,
        }

    @app.patch("/api/config/media-dir")
    def update_media_dir(payload: MediaDirectoryUpdate) -> dict[str, Any]:
        nonlocal settings
        try:
            target = _resolve_media_dir(payload.media_dir)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        current = settings.media_dir.resolve()
        if target != current:
            active_workers = [item for item in list_workers(settings) if item["status"] == "running"]
            if active_workers:
                raise HTTPException(status_code=409, detail="请先停止 worker，再迁移图片目录。")
            try:
                _migrate_media_dir(session_factory, settings, target)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except OSError as exc:
                raise HTTPException(status_code=500, detail=f"图片目录迁移失败：{exc}") from exc
        else:
            try:
                persist_media_dir(target)
            except OSError as exc:
                raise HTTPException(status_code=500, detail=f"无法保存图片目录设置：{exc}") from exc

        settings = replace(settings, media_dir=target)
        app.state.settings = settings
        return {
            "media_dir": str(settings.media_dir),
            "migrated": target != current,
            "message": "图片目录已更新，现有图片已迁移。" if target != current else "图片目录设置已保存。",
        }

    @app.get("/api/stats")
    def stats() -> dict[str, int]:
        with session_factory() as session:
            assets = session.scalar(select(func.count()).select_from(Asset).where(Asset.deleted_at.is_(None))) or 0
            generated = session.scalar(
                select(func.count()).select_from(Asset).where(Asset.source == "generated", Asset.deleted_at.is_(None))
            ) or 0
            uploaded = session.scalar(
                select(func.count()).select_from(Asset).where(Asset.source == "upload", Asset.deleted_at.is_(None))
            ) or 0
            active_jobs = session.scalar(
                select(func.count()).select_from(GenerationJob).where(GenerationJob.status.not_in(list(TERMINAL_STATES)))
            ) or 0
            failed_jobs = session.scalar(
                select(func.count()).select_from(GenerationJob).where(GenerationJob.status == "failed")
            ) or 0
            synced = session.scalar(
                select(func.count()).select_from(RemoteObject).where(RemoteObject.status == "succeeded")
            ) or 0
        return {
            "assets": int(assets),
            "generated": int(generated),
            "uploaded": int(uploaded),
            "active_jobs": int(active_jobs),
            "failed_jobs": int(failed_jobs),
            "synced": int(synced),
        }

    @app.get("/api/providers")
    def providers() -> list[dict[str, Any]]:
        with session_factory() as session:
            rows = list(session.scalars(select(Provider).order_by(Provider.name)))
        return [
            {
                "id": row.id,
                "name": row.name,
                "kind": row.kind,
                "base_url": public_url(row.base_url),
                "api_key_env": row.api_key_env,
                "enabled": row.enabled,
                "ready": bool(os.environ.get(row.api_key_env, "").strip() and row.base_url.strip()),
            }
            for row in rows
        ]

    @app.post("/api/providers", status_code=201)
    def create_provider(payload: ProviderInput) -> dict[str, Any]:
        with session_factory() as session:
            if session.get(Provider, payload.id):
                raise HTTPException(status_code=409, detail="provider ID 已存在。")
            row = Provider(
                id=payload.id,
                name=payload.name,
                kind=payload.kind,
                base_url=payload.base_url.rstrip("/"),
                api_key_env=payload.api_key_env,
                enabled=payload.enabled,
            )
            session.add(row)
            session.commit()
            return {"id": row.id, "name": row.name, "base_url": public_url(row.base_url), "ready": False}

    @app.get("/api/tags")
    def tags() -> list[dict[str, Any]]:
        with session_factory() as session:
            rows = list(session.scalars(select(Tag).order_by(Tag.name)))
        return [{"id": row.id, "name": row.name} for row in rows]

    @app.get("/api/assets")
    def list_assets(
        q: str = Query(default="", max_length=500),
        source: str = Query(default="all"),
        sync_status: str = Query(default="all"),
        tag: str = Query(default=""),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=30, ge=1, le=100),
    ) -> dict[str, Any]:
        with session_factory() as session:
            query = select(Asset).where(Asset.deleted_at.is_(None))
            if source in {"upload", "generated"}:
                query = query.where(Asset.source == source)
            if q.strip():
                needle = f"%{q.strip()}%"
                prompt_ids = select(GenerationJob.asset_id).where(GenerationJob.prompt.ilike(needle))
                query = query.where(
                    or_(
                        Asset.filename.ilike(needle),
                        Asset.original_filename.ilike(needle),
                        Asset.title.ilike(needle),
                        Asset.notes.ilike(needle),
                        Asset.prompt_override.ilike(needle),
                        Asset.id.in_(prompt_ids),
                    )
                )
            if sync_status in {"pending", "syncing", "succeeded", "failed", "disabled"}:
                matching = select(RemoteObject.asset_id).where(RemoteObject.status == sync_status)
                query = query.where(Asset.id.in_(matching))
            if tag.strip():
                matching = (
                    select(AssetTag.asset_id)
                    .join(Tag, Tag.id == AssetTag.tag_id)
                    .where(func.lower(Tag.name) == tag.strip().lower())
                )
                query = query.where(Asset.id.in_(matching))
            total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
            rows = list(
                session.scalars(
                    query.order_by(desc(Asset.created_at)).offset((page - 1) * page_size).limit(page_size)
                )
            )
            return {
                "items": [_asset_payload(session, row, settings) for row in rows],
                "page": page,
                "page_size": page_size,
                "total": int(total),
                "has_more": page * page_size < total,
            }

    @app.get("/api/assets/{asset_id}")
    def get_asset(asset_id: str) -> dict[str, Any]:
        with session_factory() as session:
            asset = session.get(Asset, asset_id)
            if asset is None or asset.deleted_at is not None:
                raise HTTPException(status_code=404, detail="图片不存在。")
            return _asset_payload(session, asset, settings, detail=True)

    @app.get("/api/assets/{asset_id}/file")
    def asset_file(asset_id: str, variant: str = Query(default="original")):
        with session_factory() as session:
            asset = session.get(Asset, asset_id)
            if asset is None or asset.deleted_at is not None:
                raise HTTPException(status_code=404, detail="图片不存在。")
            if variant == "original":
                relative_path = asset.local_path
                media_type = asset.mime_type
            else:
                item = get_variant(session, asset_id, variant)
                if item is None:
                    raise HTTPException(status_code=404, detail="图片派生文件不存在。")
                relative_path = item.local_path
                media_type = item.mime_type
        path = absolute_media_path(relative_path, settings)
        if not path.exists():
            raise HTTPException(status_code=404, detail="本地图片文件不存在。")
        return FileResponse(path, media_type=media_type)

    @app.post("/api/assets/upload", status_code=201)
    async def upload_asset(
        file: UploadFile = File(...),
        title: str = Form(default=""),
        notes: str = Form(default=""),
        tags_value: str = Form(default=""),
        sync_enabled: str = Form(default="true"),
    ) -> dict[str, Any]:
        data = await file.read(settings.max_upload_bytes + 1)
        if len(data) > settings.max_upload_bytes:
            raise HTTPException(status_code=413, detail="图片超过大小限制。")
        enabled = sync_enabled.strip().lower() not in {"0", "false", "no", "off"}
        with session_factory() as session:
            try:
                asset, created = create_asset_from_bytes(
                    session,
                    settings,
                    data,
                    file.filename or "upload",
                    content_type=file.content_type,
                    source="upload",
                    title=title,
                    notes=notes,
                    sync_enabled=enabled,
                    deduplicate=True,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if created:
                _set_asset_tags(session, asset.id, _parse_tags(tags_value))
                session.add(
                    RemoteObject(
                        id=str(uuid.uuid4()),
                        asset_id=asset.id,
                        provider="cloudflare-imgbed",
                        status="pending" if enabled else "disabled",
                    )
                )
            session.commit()
            return {"created": created, "asset": _asset_payload(session, asset, settings, detail=True)}

    @app.patch("/api/assets/{asset_id}")
    def update_asset(asset_id: str, payload: AssetUpdate) -> dict[str, Any]:
        with session_factory() as session:
            asset = session.get(Asset, asset_id)
            if asset is None or asset.deleted_at is not None:
                raise HTTPException(status_code=404, detail="图片不存在。")
            values = payload.model_dump(exclude_unset=True)
            for key, value in values.items():
                if key in {"title", "notes", "prompt_override"}:
                    setattr(asset, key, (value or "").strip())
            if "sync_enabled" in values:
                asset.sync_enabled = bool(values["sync_enabled"])
                remote = _ensure_remote(session, asset)
                if asset.sync_enabled and remote.status in {"disabled", "failed"}:
                    remote.status = "pending"
                    remote.last_error = ""
                if not asset.sync_enabled and remote.status in {"pending", "failed"}:
                    remote.status = "disabled"
            session.commit()
            return _asset_payload(session, asset, settings, detail=True)

    @app.delete("/api/assets/{asset_id}")
    def delete_asset(asset_id: str) -> dict[str, Any]:
        with session_factory() as session:
            asset = session.get(Asset, asset_id)
            if asset is None or asset.deleted_at is not None:
                raise HTTPException(status_code=404, detail="图片不存在。")
            asset.deleted_at = utcnow()
            session.commit()
        return {"ok": True, "id": asset_id}

    @app.post("/api/assets/bulk-tags")
    def bulk_tags(payload: BulkTagsRequest) -> dict[str, Any]:
        with session_factory() as session:
            rows = list(
                session.scalars(
                    select(Asset).where(Asset.id.in_(payload.asset_ids), Asset.deleted_at.is_(None))
                )
            )
            for row in rows:
                _set_asset_tags(session, row.id, payload.tags)
            session.commit()
        return {"updated": len(rows), "tags": payload.tags}

    @app.post("/api/assets/bulk-sync")
    def bulk_sync(payload: BulkSyncRequest) -> dict[str, Any]:
        created = 0
        with session_factory() as session:
            rows = list(
                session.scalars(
                    select(Asset).where(Asset.id.in_(payload.asset_ids), Asset.deleted_at.is_(None))
                )
            )
            for asset in rows:
                asset.sync_enabled = True
                remote = _ensure_remote(session, asset)
                if remote.status != "succeeded":
                    remote.status = "pending"
                    remote.last_error = ""
                    created += 1
            session.commit()
        return {"updated": len(rows), "queued": created}

    @app.post("/api/assets/{asset_id}/sync")
    def sync_asset(asset_id: str) -> dict[str, Any]:
        with session_factory() as session:
            asset = session.get(Asset, asset_id)
            if asset is None or asset.deleted_at is not None:
                raise HTTPException(status_code=404, detail="图片不存在。")
            asset.sync_enabled = True
            remote = _ensure_remote(session, asset)
            if remote.status != "succeeded":
                remote.status = "pending"
                remote.last_error = ""
            session.commit()
            return _asset_payload(session, asset, settings, detail=True)

    @app.post("/api/generation/jobs", status_code=202)
    def create_generation_job(payload: GenerateRequest) -> dict[str, Any]:
        if not re.fullmatch(r"gpt-image-[A-Za-z0-9._-]+", payload.model):
            raise HTTPException(status_code=400, detail="模型名称必须是 gpt-image-*。")
        allowed_sizes = {
            "auto", "1024x1024", "1536x864", "864x1536", "1536x1024", "1024x1536",
            "2048x2048", "2048x1152", "1440x2560", "2560x1440", "3840x2160", "2160x3840", "2880x2880",
        }
        if payload.size not in allowed_sizes:
            raise HTTPException(status_code=400, detail="不支持的图片尺寸。")
        if payload.quality not in {"low", "medium", "high", "auto"}:
            raise HTTPException(status_code=400, detail="不支持的图片质量。")
        with session_factory() as session:
            provider_id = payload.provider_id or settings.image_provider_id
            provider = session.get(Provider, provider_id)
            if provider is None or not provider.enabled:
                raise HTTPException(status_code=400, detail="provider 不存在或已停用。")
            if not provider.base_url or not os.environ.get(provider.api_key_env, "").strip():
                raise HTTPException(status_code=503, detail=f"provider {provider.name} 尚未配置完成。")
            if payload.parent_job_id and session.get(GenerationJob, payload.parent_job_id) is None:
                raise HTTPException(status_code=400, detail="父任务不存在。")
            reference_asset = None
            if payload.reference_asset_id:
                reference_asset = session.get(Asset, payload.reference_asset_id)
                if reference_asset is None or reference_asset.deleted_at is not None:
                    raise HTTPException(status_code=400, detail="参考图不存在或已被删除。")
                try:
                    reference_path = absolute_media_path(reference_asset.local_path, settings)
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail="参考图路径无效。") from exc
                if not reference_path.is_file():
                    raise HTTPException(status_code=400, detail="参考图文件不存在。")
            job = GenerationJob(
                id=str(uuid.uuid4()),
                parent_job_id=payload.parent_job_id,
                provider_id=provider.id,
                provider_name_snapshot=provider.name,
                provider_url_snapshot=public_url(provider.base_url),
                reference_asset_id=reference_asset.id if reference_asset else None,
                model=payload.model,
                prompt=payload.prompt.strip(),
                size=payload.size,
                quality=payload.quality,
                timeout_seconds=payload.timeout,
                sync_enabled=payload.sync_enabled,
                status="queued",
                progress_message="已加入本地任务队列",
            )
            session.add(job)
            session.flush()
            from .worker import _event

            _event(session, job.id, "queued", job.progress_message)
            session.commit()
            return _job_payload(session, job)

    @app.get("/api/generation/jobs")
    def list_generation_jobs(limit: int = Query(default=50, ge=1, le=200), status: str = Query(default="all")):
        with session_factory() as session:
            query = select(GenerationJob).order_by(desc(GenerationJob.created_at)).limit(limit)
            if status != "all":
                query = query.where(GenerationJob.status == status)
            return [_job_payload(session, row) for row in session.scalars(query)]

    @app.get("/api/generation/jobs/{job_id}")
    def get_generation_job(job_id: str):
        with session_factory() as session:
            job = session.get(GenerationJob, job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="任务不存在。")
            return _job_payload(session, job, include_events=True)

    @app.post("/api/generation/jobs/{job_id}/retry", status_code=202)
    def retry_generation_job(job_id: str):
        with session_factory() as session:
            original = session.get(GenerationJob, job_id)
            if original is None:
                raise HTTPException(status_code=404, detail="任务不存在。")
            if original.status not in {"failed", "canceled"}:
                raise HTTPException(status_code=409, detail="只有失败或取消的任务可以手动重试。")
            resumable_errors = (
                "查询异步任务失败",
                "任务等待超过设定上限",
                "下载 provider 图片失败",
                "下载 provider 图片返回",
            )
            resume_task_id = (
                original.upstream_task_id
                if original.upstream_task_id and original.error_message.startswith(resumable_errors)
                else None
            )
            job = GenerationJob(
                id=str(uuid.uuid4()),
                parent_job_id=original.id,
                provider_id=original.provider_id,
                provider_name_snapshot=original.provider_name_snapshot,
                provider_url_snapshot=original.provider_url_snapshot,
                reference_asset_id=original.reference_asset_id,
                model=original.model,
                prompt=original.prompt,
                size=original.size,
                quality=original.quality,
                response_format=original.response_format,
                timeout_seconds=original.timeout_seconds,
                sync_enabled=original.sync_enabled,
                status="queued",
                upstream_task_id=resume_task_id,
                submitted_at=original.submitted_at if resume_task_id else None,
                request_json=original.request_json,
                progress_message=(
                    "继续查询原 provider 任务"
                    if resume_task_id
                    else "手动重试已加入队列"
                ),
            )
            session.add(job)
            session.flush()
            from .worker import _event

            _event(
                session,
                job.id,
                "manual_resume" if resume_task_id else "manual_retry",
                job.progress_message,
                {
                    "parent_job_id": original.id,
                    "upstream_task_id": resume_task_id,
                },
            )
            session.commit()
            return _job_payload(session, job)

    @app.get("/api/generation/jobs/{job_id}/events")
    def generation_events(job_id: str):
        with session_factory() as session:
            if session.get(GenerationJob, job_id) is None:
                raise HTTPException(status_code=404, detail="任务不存在。")
            return [
                {
                    "id": event.id,
                    "type": event.event_type,
                    "message": event.message,
                    "created_at": _iso(event.created_at),
                    "payload": _json_load(event.payload_json, {}),
                }
                for event in session.scalars(
                    select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.created_at)
                )
            ]

    @app.get("/", response_class=HTMLResponse)
    def index():
        path = settings.frontend_dist / "index.html"
        if path.exists():
            return FileResponse(path, media_type="text/html")
        return HTMLResponse("<h1>FrameLab</h1><p>请先运行 npm install 和 npm run build。</p>", status_code=503)

    if settings.frontend_dist.exists():
        app.mount("/assets", StaticFiles(directory=settings.frontend_dist / "assets"), name="frontend-assets")

    return app


TERMINAL_STATES = {"succeeded", "failed", "canceled"}
