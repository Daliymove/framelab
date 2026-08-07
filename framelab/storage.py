from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import uuid
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .models import Asset, AssetVariant, utcnow


ALLOWED_MIME_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
SUFFIX_BY_MIME = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def safe_basename(value: str, fallback: str = "image") -> str:
    candidate = Path(value or fallback).name
    candidate = re.sub(r"[\x00-\x1f\x7f]+", "", candidate).strip(" .")
    return candidate[:180] or fallback


def detect_mime(filename: str, content_type: str | None, data: bytes) -> tuple[str, str, int, int, dict[str, Any]]:
    guessed = (content_type or "").split(";", 1)[0].strip().lower()
    if guessed not in ALLOWED_MIME_TYPES:
        guessed = mimetypes.guess_type(filename)[0] or ""

    try:
        with Image.open(BytesIO(data)) as image:
            image.load()
            width, height = image.size
            image_format = (image.format or "").upper()
            format_mime = {
                "PNG": "image/png",
                "JPEG": "image/jpeg",
                "JPG": "image/jpeg",
                "WEBP": "image/webp",
                "GIF": "image/gif",
            }.get(image_format)
            if format_mime:
                guessed = format_mime
            exif = {}
            for key, value in (image.getexif() or {}).items():
                try:
                    exif[str(key)] = str(value)
                except Exception:
                    continue
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError("只支持有效的 PNG、JPEG、WebP 或 GIF 图片。") from exc

    if guessed not in ALLOWED_MIME_TYPES:
        raise ValueError("只支持 PNG、JPEG、WebP 或 GIF 图片。")
    suffix = SUFFIX_BY_MIME[guessed]
    return guessed, suffix, width, height, exif


def _relative(path: Path, settings: Settings) -> str:
    return path.relative_to(settings.data_dir).as_posix()


def absolute_media_path(relative_path: str, settings: Settings) -> Path:
    candidate = (settings.data_dir / relative_path).resolve()
    media_root = settings.media_dir.resolve()
    if candidate != media_root and media_root not in candidate.parents:
        raise ValueError("媒体路径越界。")
    return candidate


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def _save_variant(
    asset_id: str,
    kind: str,
    source: Image.Image,
    settings: Settings,
    max_size: tuple[int, int],
) -> AssetVariant:
    image = ImageOps.exif_transpose(source.copy())
    image.thumbnail(max_size, Image.Resampling.LANCZOS)
    if image.mode not in {"RGB", "RGBA"}:
        image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
    buffer = BytesIO()
    image.save(buffer, format="WEBP", quality=88, method=6)
    data = buffer.getvalue()
    path = settings.media_dir / "derived" / asset_id / f"{kind}.webp"
    _write_atomic(path, data)
    return AssetVariant(
        id=str(uuid.uuid4()),
        asset_id=asset_id,
        kind=kind,
        local_path=_relative(path, settings),
        mime_type="image/webp",
        size_bytes=len(data),
        width=image.width,
        height=image.height,
    )


def create_asset_from_bytes(
    session: Session,
    settings: Settings,
    data: bytes,
    filename: str,
    *,
    content_type: str | None = None,
    source: str = "upload",
    title: str = "",
    notes: str = "",
    prompt_override: str = "",
    sync_enabled: bool = True,
    deduplicate: bool = True,
) -> tuple[Asset, bool]:
    if not data:
        raise ValueError("图片内容为空。")
    if len(data) > settings.max_upload_bytes:
        raise ValueError(f"图片不能超过 {settings.max_upload_bytes // 1024 // 1024} MB。")

    original_filename = safe_basename(filename)
    mime_type, suffix, width, height, exif = detect_mime(original_filename, content_type, data)
    sha256 = hashlib.sha256(data).hexdigest()

    if deduplicate:
        existing = session.scalar(
            select(Asset).where(Asset.sha256 == sha256, Asset.deleted_at.is_(None)).limit(1)
        )
        if existing is not None:
            return existing, False

    asset_id = str(uuid.uuid4())
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"{timestamp}-{asset_id[:8]}{suffix}"
    path = settings.media_dir / "originals" / datetime.now().strftime("%Y/%m") / filename
    _write_atomic(path, data)

    asset = Asset(
        id=asset_id,
        filename=filename,
        original_filename=original_filename,
        mime_type=mime_type,
        suffix=suffix,
        size_bytes=len(data),
        sha256=sha256,
        width=width,
        height=height,
        source=source,
        local_path=_relative(path, settings),
        title=title.strip()[:255],
        notes=notes.strip(),
        prompt_override=prompt_override.strip(),
        metadata_json=json.dumps({"exif": exif}, ensure_ascii=False),
        sync_enabled=sync_enabled,
        created_at=utcnow(),
    )
    session.add(asset)

    with Image.open(BytesIO(data)) as image:
        for variant in (
            _save_variant(asset_id, "thumbnail", image, settings, (640, 640)),
            _save_variant(asset_id, "preview", image, settings, (1920, 1920)),
        ):
            session.add(variant)
    return asset, True


def get_variant(session: Session, asset_id: str, kind: str) -> AssetVariant | None:
    return session.scalar(
        select(AssetVariant).where(AssetVariant.asset_id == asset_id, AssetVariant.kind == kind)
    )
