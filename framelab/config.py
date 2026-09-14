from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv


PACKAGE_DIR = Path(__file__).resolve().parent
APP_DIR = PACKAGE_DIR.parent
ENV_FILE = APP_DIR / ".env"

# Both the API process and the worker import this module, so one project-level
# file supplies the same configuration to each process. File values win over
# inherited shell variables to keep local startup deterministic.
load_dotenv(APP_DIR / ".env", override=True, encoding="utf-8")


def persist_env_variables(updates: dict[str, str], env_path: Path | None = None) -> None:
    """Update or append environment variables in .env while preserving the rest of the file."""
    for key, val in updates.items():
        if any(char in str(val) for char in "\x00\r\n"):
            raise ValueError(f"{key} 不能包含换行或无效字符。")

    env_path = env_path or ENV_FILE
    existing = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    lines = existing.splitlines()

    for key, val in updates.items():
        active_pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
        comment_pattern = re.compile(rf"^\s*#\s*{re.escape(key)}\s*=")

        active_indices = [i for i, line in enumerate(lines) if active_pattern.match(line)]
        if active_indices:
            target_idx = active_indices[-1]
            lines[target_idx] = f"{key}={val}"
            for idx in reversed(active_indices[:-1]):
                lines.pop(idx)
        else:
            comment_indices = [i for i, line in enumerate(lines) if comment_pattern.match(line)]
            if comment_indices:
                target_idx = comment_indices[-1]
                lines[target_idx] = f"{key}={val}"
            else:
                lines.append(f"{key}={val}")

    content = "\n".join(lines)
    if existing.endswith(("\n", "\r")) or lines:
        content += "\n"

    env_path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{env_path.name}.", suffix=".tmp", dir=env_path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as temporary:
            temporary.write(content)
        os.replace(temporary_name, env_path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def persist_media_dir(media_dir: Path, env_path: Path | None = None) -> None:
    """Persist the image directory while preserving the rest of the env file."""
    persist_env_variables({"FRAMELAB_MEDIA_DIR": str(media_dir)}, env_path)


def reload_env(env_path: Path | None = None) -> None:
    """Reload environment variables from .env file into os.environ."""
    load_dotenv(env_path or ENV_FILE, override=True, encoding="utf-8")


def persist_provider_config(
    base_url: str | None = None,
    api_key: str | None = None,
    api_key_env: str = "CODEX_IMAGE_API_KEY",
    env_path: Path | None = None,
) -> None:
    """Persist provider base_url and api_key to .env and sync with os.environ."""
    updates: dict[str, str] = {}
    if base_url is not None and base_url.strip():
        clean_url = base_url.strip().rstrip("/")
        updates["CODEX_IMAGE_BASE_URL"] = clean_url
        os.environ["CODEX_IMAGE_BASE_URL"] = clean_url
    if api_key is not None and api_key.strip():
        clean_key = api_key.strip()
        updates[api_key_env] = clean_key
        os.environ[api_key_env] = clean_key
    if updates:
        persist_env_variables(updates, env_path)
        reload_env(env_path)


def _first_env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name)
        if value is not None and value.strip():
            return value.strip()
    return default


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off", ""}


def public_url(value: str) -> str:
    """Return a URL snapshot without credentials or query parameters."""
    parsed = urlsplit(value.strip())
    if not parsed.scheme or not parsed.netloc:
        return ""
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    data_dir: Path
    database_path: Path
    media_dir: Path
    frontend_dist: Path
    image_api_key: str
    image_base_url: str
    image_provider_id: str
    image_provider_name: str
    image_model: str
    image_poll_interval: float
    image_max_prompt_chars: int
    image_max_wait_seconds: int
    imgbed_base_url: str
    imgbed_api_token: str
    imgbed_auth_code: str
    imgbed_upload_path: str
    imgbed_upload_channel: str
    imgbed_upload_channel_name: str
    imgbed_upload_folder: str
    imgbed_enabled: bool
    max_upload_bytes: int

    @classmethod
    def from_env(cls) -> "Settings":
        raw_data_dir = _first_env(
            "FRAMELAB_DATA_DIR",
            default=str(Path.home() / "Pictures" / "FrameLab"),
        )
        data_dir = Path(raw_data_dir).expanduser().resolve()
        raw_db = _first_env("FRAMELAB_DB_PATH", default=str(data_dir / "framelab.sqlite3"))
        raw_media_dir = _first_env("FRAMELAB_MEDIA_DIR", default=str(data_dir / "media"))
        image_base_url = _first_env("CODEX_IMAGE_BASE_URL")
        imgbed_base_url = _first_env(
            "FRAMELAB_IMGBED_BASE_URL",
            "CLOUDFLARE_IMGBED_BASE_URL",
            "IMGBED_BASE_URL",
        )
        return cls(
            host=_first_env("FRAMELAB_HOST", default="127.0.0.1"),
            port=int(_first_env("FRAMELAB_PORT", default="8765")),
            data_dir=data_dir,
            database_path=Path(raw_db).expanduser().resolve(),
            media_dir=Path(raw_media_dir).expanduser().resolve(),
            frontend_dist=APP_DIR / "web" / "dist",
            image_api_key=_first_env("CODEX_IMAGE_API_KEY"),
            image_base_url=image_base_url,
            image_provider_id=_first_env("CODEX_IMAGE_PROVIDER_ID", default="codex-relay"),
            image_provider_name=_first_env("CODEX_IMAGE_PROVIDER_NAME", default="Image Relay"),
            image_model=_first_env("CODEX_IMAGE_DEFAULT_MODEL", default="gpt-image-2"),
            image_poll_interval=float(_first_env("CODEX_IMAGE_POLL_INTERVAL", default="3")),
            image_max_prompt_chars=int(_first_env("CODEX_IMAGE_MAX_PROMPT_CHARS", default="32000")),
            image_max_wait_seconds=int(_first_env("CODEX_IMAGE_MAX_WAIT_SECONDS", default="1800")),
            imgbed_base_url=imgbed_base_url,
            imgbed_api_token=_first_env(
                "FRAMELAB_IMGBED_API_TOKEN",
                "CLOUDFLARE_IMGBED_API_TOKEN",
                "IMGBED_API_TOKEN",
            ),
            imgbed_auth_code=_first_env("FRAMELAB_IMGBED_AUTH_CODE", "IMGBED_AUTH_CODE"),
            imgbed_upload_path=_first_env("FRAMELAB_IMGBED_UPLOAD_PATH", default="/upload"),
            imgbed_upload_channel=_first_env("FRAMELAB_IMGBED_UPLOAD_CHANNEL"),
            imgbed_upload_channel_name=_first_env("FRAMELAB_IMGBED_UPLOAD_CHANNEL_NAME"),
            imgbed_upload_folder=_first_env("FRAMELAB_IMGBED_UPLOAD_FOLDER"),
            imgbed_enabled=_env_bool("FRAMELAB_IMGBED_ENABLED", bool(imgbed_base_url)),
            max_upload_bytes=int(_first_env("FRAMELAB_MAX_UPLOAD_BYTES", default=str(50 * 1024 * 1024))),
        )

    @property
    def image_base_url_public(self) -> str:
        return public_url(self.image_base_url)

    @property
    def imgbed_base_url_public(self) -> str:
        return public_url(self.imgbed_base_url)

    @property
    def ready_for_generation(self) -> bool:
        return bool(self.image_api_key and self.image_base_url)

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.media_dir.mkdir(parents=True, exist_ok=True)
        (self.media_dir / "originals").mkdir(parents=True, exist_ok=True)
        (self.media_dir / "derived").mkdir(parents=True, exist_ok=True)
