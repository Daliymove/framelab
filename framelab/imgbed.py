from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from .config import Settings


class ImgBedError(RuntimeError):
    pass


def _error_text(response: httpx.Response) -> str:
    try:
        value = response.json()
    except ValueError:
        value = response.text
    if isinstance(value, dict):
        return str(value.get("error") or value.get("message") or value)[:2000]
    return str(value)[:2000]


class ImgBedClient:
    """REST adapter for MarSeventh/CloudFlare-ImgBed's /upload endpoint."""

    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return bool(self.settings.imgbed_enabled and self.settings.imgbed_base_url)

    def upload(self, path: Path, filename: str, mime_type: str) -> dict[str, Any]:
        if not self.enabled:
            raise ImgBedError("cloudflare-imgbed 尚未配置。")
        endpoint = urljoin(self.settings.imgbed_base_url.rstrip("/") + "/", self.settings.imgbed_upload_path.lstrip("/"))
        params: dict[str, str] = {
            "returnFormat": "full",
            "autoRetry": "false",
            "serverCompress": "false",
        }
        if self.settings.imgbed_auth_code:
            params["authCode"] = self.settings.imgbed_auth_code
        if self.settings.imgbed_upload_channel:
            params["uploadChannel"] = self.settings.imgbed_upload_channel
        if self.settings.imgbed_upload_channel_name:
            params["channelName"] = self.settings.imgbed_upload_channel_name
        if self.settings.imgbed_upload_folder:
            params["uploadFolder"] = self.settings.imgbed_upload_folder
        headers = {"Accept": "application/json", "User-Agent": "FrameLab/0.1"}
        if self.settings.imgbed_api_token:
            headers["Authorization"] = f"Bearer {self.settings.imgbed_api_token}"
        try:
            with path.open("rb") as handle, httpx.Client(timeout=180.0, follow_redirects=False) as client:
                response = client.post(
                    endpoint,
                    params=params,
                    headers=headers,
                    files={"file": (filename, handle, mime_type)},
                )
        except OSError as exc:
            raise ImgBedError(f"读取本地文件失败：{exc}") from exc
        except httpx.HTTPError as exc:
            raise ImgBedError(f"调用 cloudflare-imgbed 失败：{exc}") from exc
        if response.status_code >= 400:
            raise ImgBedError(f"cloudflare-imgbed 返回 HTTP {response.status_code}：{_error_text(response)}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ImgBedError("cloudflare-imgbed 返回的不是有效 JSON。") from exc
        item: dict[str, Any] | None = None
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            item = payload[0]
        elif isinstance(payload, dict):
            item = payload
        if item is None:
            raise ImgBedError("cloudflare-imgbed 返回中没有文件信息。")
        source = str(item.get("publicUrl") or item.get("src") or item.get("url") or "")
        if not source:
            raise ImgBedError("cloudflare-imgbed 返回中没有 src/publicUrl。")
        remote_url = urljoin(self.settings.imgbed_base_url.rstrip("/") + "/", source)
        parsed = urlsplit(remote_url)
        remote_id = parsed.path.rstrip("/").rsplit("/", 1)[-1] if parsed.path else ""
        return {
            "remote_url": remote_url,
            "remote_id": remote_id,
            "response": item,
            "raw_response": payload,
        }
