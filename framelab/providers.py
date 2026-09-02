from __future__ import annotations

import base64
import binascii
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

import httpx

from .config import Settings, public_url
from .models import GenerationJob, Provider


class ProviderError(RuntimeError):
    pass


CORE_REQUEST_KEYS = frozenset(
    {"model", "prompt", "n", "size", "quality", "response_format", "method", "endpoint", "image[]"}
)


def normalize_endpoint(value: str | None) -> str:
    endpoint = (value or "").strip()
    if not endpoint:
        return ""
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("调用地址必须是完整的 http 或 https URL。")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("调用地址不能包含账号、密码或片段。")
    return endpoint


def validate_extra_params(value: dict[str, Any]) -> dict[str, Any]:
    reserved = sorted(set(value) & CORE_REQUEST_KEYS)
    if reserved:
        names = ", ".join(reserved)
        raise ValueError(f"附加参数不能覆盖表单字段：{names}。")
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError("附加参数必须是可序列化的 JSON 对象。") from exc
    if len(encoded.encode("utf-8")) > 20000:
        raise ValueError("附加参数不能超过 20KB。")
    return value


def _image_endpoint(base_url: str) -> str:
    base = _api_root(base_url)
    if base.endswith("/images/generations"):
        return base
    return f"{base}/images/generations"


def _async_endpoint(base_url: str) -> str:
    return f"{_image_endpoint(base_url)}/async"


def _edits_async_endpoint(base_url: str) -> str:
    base = _api_root(base_url)
    if base.endswith("/images/edits"):
        return f"{base}/async"
    return f"{base}/images/edits/async"


def _task_endpoint(base_url: str, task_id: str) -> str:
    api_root = _api_root(base_url)
    return f"{api_root}/images/tasks/{quote(task_id, safe='')}"


def _documented_task_endpoint(submit_endpoint: str, task_id: str) -> str:
    parsed = urlsplit(submit_endpoint)
    path = parsed.path.rstrip("/")
    image_root = path.rsplit("/images/", 1)[0] + "/images"
    return urlunsplit((parsed.scheme, parsed.netloc, f"{image_root}/{quote(task_id, safe='')}", "", ""))


def _legacy_task_endpoint(submit_endpoint: str, task_id: str) -> str | None:
    parsed = urlsplit(submit_endpoint)
    path = parsed.path.rstrip("/")
    for suffix in ("/images/generations/async", "/images/edits/async"):
        if path.endswith(suffix):
            api_root = path.removesuffix(suffix)
            return urlunsplit(
                (parsed.scheme, parsed.netloc, f"{api_root}/images/tasks/{quote(task_id, safe='')}", "", "")
            )
    return None


def _uses_documented_async_endpoint(endpoint: str) -> bool:
    path = urlsplit(endpoint).path.rstrip("/")
    return path.endswith("/images/generations") or path.endswith("/images/edits")


def _api_root(base_url: str) -> str:
    base = base_url.strip().rstrip("/")
    for suffix in ("/images/generations", "/images/edits"):
        if base.endswith(suffix):
            return base.removesuffix(suffix)
    return base if base.endswith("/v1") else f"{base}/v1"


def _clean_payload(
    value: Any,
    limit: int = 12000,
    redacted_keys: set[str] | frozenset[str] | None = None,
) -> dict[str, Any] | list[Any] | str:
    """Keep diagnostics useful without persisting huge base64 payloads."""
    redacted_keys = redacted_keys or frozenset()
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if key in redacted_keys or key in {"b64_json", "image_base64", "input_image", "reference_image"}:
                if isinstance(item, str):
                    cleaned[str(key)] = f"<omitted {len(item)} chars>"
                else:
                    cleaned[str(key)] = "<omitted reference image>"
            elif key == "data" and isinstance(item, str) and len(item) > 512:
                cleaned[key] = f"<omitted {len(item)} chars>"
            else:
                cleaned[str(key)] = _clean_payload(item, limit, redacted_keys)
        return cleaned
    if isinstance(value, list):
        return [_clean_payload(item, limit, redacted_keys) for item in value[:20]]
    if isinstance(value, str):
        return value[:limit]
    return value


def _parse_error(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = response.text
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error)
        if error:
            return str(error)
    return str(payload)[:4000] or f"HTTP {response.status_code}"


def _decode_data_url(value: str) -> bytes:
    _, separator, encoded = value.partition(",")
    if not separator:
        raise ProviderError("provider 返回了无效的 data URL。")
    try:
        return base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ProviderError("provider 返回了无效的 base64 图片。") from exc


@dataclass
class SubmittedTask:
    task_id: str
    response: dict[str, Any]
    request: dict[str, Any]


@dataclass
class ProviderResult:
    image_bytes: bytes
    suffix: str
    response: dict[str, Any]


MAX_ASYNC_EDIT_BYTES = 15 * 1024 * 1024


class OpenAIAsyncProvider:
    """OpenAI-compatible async image provider; deliberately has no retry layer."""

    def __init__(self, provider: Provider, api_key: str, settings: Settings):
        self.provider = provider
        self.api_key = api_key
        self.settings = settings

    @property
    def base_url(self) -> str:
        return self.provider.base_url.strip().rstrip("/")

    def _headers(self, *, multipart: bool = False) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "Connection": "close",
            "User-Agent": "FrameLab/0.1",
        }
        if not multipart:
            headers["Content-Type"] = "application/json"
        return headers

    def build_payload(self, job: GenerationJob) -> dict[str, Any]:
        extra = _json_load(job.extra_params_json, {})
        if not isinstance(extra, dict):
            extra = {}
        return {
            **extra,
            "model": job.model,
            "prompt": job.prompt,
            "n": 1,
            "size": job.size,
            "quality": job.quality,
            "response_format": job.response_format,
        }

    def submit(
        self,
        job: GenerationJob,
        reference_image: tuple[bytes, str, str] | None = None,
    ) -> SubmittedTask:
        payload = self.build_payload(job)
        timeout = max(1.0, min(float(job.timeout_seconds), 1800.0))
        try:
            with httpx.Client(timeout=timeout, follow_redirects=False) as client:
                if reference_image is None:
                    endpoint = job.endpoint_override.strip() if job.endpoint_override else _async_endpoint(self.base_url)
                    response = client.post(endpoint, headers=self._headers(), json=payload)
                    request_snapshot = _clean_payload({"method": "POST", "endpoint": endpoint, **payload})
                else:
                    image_bytes, mime_type, filename = reference_image
                    if not image_bytes:
                        raise ProviderError("参考图内容为空。")
                    if len(image_bytes) > MAX_ASYNC_EDIT_BYTES:
                        raise ProviderError("参考图超过异步改图 15MB 限制，请压缩后重试。")
                    endpoint = job.endpoint_override.strip() if job.endpoint_override else _edits_async_endpoint(self.base_url)
                    form_data = {
                        key: json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                        if isinstance(value, (dict, list))
                        else str(value).lower()
                        if isinstance(value, bool)
                        else str(value)
                        for key, value in payload.items()
                    }
                    files = {
                        "image[]": (
                            Path(filename).name or "reference-image",
                            image_bytes,
                            mime_type,
                        )
                    }
                    response = client.post(
                        endpoint,
                        headers=self._headers(multipart=True),
                        data=form_data,
                        files=files,
                    )
                    request_snapshot = {
                        "method": "POST",
                        **payload,
                        "endpoint": endpoint,
                        "image[]": {
                            "filename": Path(filename).name or "reference-image",
                            "mime_type": mime_type,
                            "size_bytes": len(image_bytes),
                        },
                    }
        except httpx.HTTPError as exc:
            raise ProviderError(f"提交 provider 失败：{exc}") from exc
        if response.status_code >= 400:
            raise ProviderError(f"provider 返回 HTTP {response.status_code}：{_parse_error(response)}")
        try:
            decoded = response.json()
        except ValueError as exc:
            raise ProviderError("provider 提交响应不是有效 JSON。") from exc
        if not isinstance(decoded, dict):
            raise ProviderError("provider 提交响应不是对象。")
        task_id = decoded.get("task_id") or decoded.get("id")
        if not isinstance(task_id, str) or not task_id:
            raise ProviderError("provider 提交成功但响应缺少 task_id。")
        return SubmittedTask(
            task_id=task_id,
            response=_clean_payload(decoded),
            request=_clean_payload(request_snapshot),
        )

    def poll_until_done(self, task_id: str, deadline: float, on_poll=None, submit_endpoint: str | None = None) -> ProviderResult:
        legacy_endpoint = _legacy_task_endpoint(submit_endpoint, task_id) if submit_endpoint else None
        endpoint = (
            _documented_task_endpoint(submit_endpoint, task_id)
            if submit_endpoint and _uses_documented_async_endpoint(submit_endpoint)
            else legacy_endpoint or _task_endpoint(self.base_url, task_id)
        )
        last_response: dict[str, Any] = {}
        last_poll_error = ""
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                detail = f"；最后一次查询错误：{last_poll_error}" if last_poll_error else ""
                raise ProviderError(f"任务等待超过设定上限{detail}；task_id={task_id}")
            # This provider may hold the status request open until generation
            # advances. Match the original client behavior by allowing the
            # request to use the job's full remaining wait budget.
            timeout = max(1.0, remaining)
            try:
                with httpx.Client(timeout=timeout, follow_redirects=False) as client:
                    response = client.get(endpoint, headers=self._headers())
            except httpx.HTTPError as exc:
                last_poll_error = str(exc)
                if on_poll:
                    on_poll("reconnecting", {"error": last_poll_error, "task_id": task_id})
                time.sleep(min(self.settings.image_poll_interval, max(0.05, remaining)))
                continue
            last_poll_error = ""
            if response.status_code >= 400:
                raise ProviderError(
                    f"查询异步任务返回 HTTP {response.status_code}：{_parse_error(response)}；task_id={task_id}"
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise ProviderError(f"异步任务响应不是有效 JSON；task_id={task_id}") from exc
            if not isinstance(payload, dict):
                raise ProviderError(f"异步任务响应不是对象；task_id={task_id}")
            last_response = _clean_payload(payload)
            status = str(payload.get("status") or payload.get("state") or "").lower()
            if on_poll:
                on_poll(status, last_response)
            if status in {"succeeded", "success", "completed", "done"}:
                result = payload.get("result")
                if not isinstance(result, dict):
                    result = payload
                image_bytes, suffix = self._extract_image(result, timeout, response_endpoint=endpoint)
                return ProviderResult(image_bytes=image_bytes, suffix=suffix, response=last_response)
            if status in {"failed", "failure", "error", "canceled", "cancelled"}:
                error = payload.get("error")
                detail = error.get("message") if isinstance(error, dict) else error
                raise ProviderError(f"异步任务失败：{detail or 'provider 未提供原因'}；task_id={task_id}")
            if status not in {"queued", "pending", "running", "processing", "in_progress", ""}:
                raise ProviderError(f"provider 返回未知任务状态 {status!r}；task_id={task_id}")
            time.sleep(min(self.settings.image_poll_interval, max(0.05, remaining)))

    def _extract_image(self, payload: dict[str, Any], timeout: float, response_endpoint: str | None = None) -> tuple[bytes, str]:
        content_url = payload.get("content_url")
        if isinstance(content_url, str) and content_url:
            parsed = urlsplit(response_endpoint or self.base_url)
            origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
            content_endpoint = urljoin(origin, content_url)
            content_parsed = urlsplit(content_endpoint)
            return self._download_image(
                content_endpoint,
                timeout,
                include_auth=content_parsed.netloc == parsed.netloc,
            )
        data = payload.get("data")
        if not isinstance(data, list) or not data or not isinstance(data[0], dict):
            raise ProviderError("provider 成功响应中没有 data[0]。")
        item = data[0]
        encoded = item.get("b64_json")
        if isinstance(encoded, str) and encoded:
            return _decode_data_url(f"data:image/png;base64,{encoded}"), ".png"
        url = item.get("url")
        if isinstance(url, str) and url.startswith("data:"):
            mime = url.split(";", 1)[0].removeprefix("data:")
            suffix = {"image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}.get(mime, ".png")
            return _decode_data_url(url), suffix
        if not isinstance(url, str) or not url:
            raise ProviderError("provider 成功响应中没有图片 URL 或 base64。")
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"}:
            raise ProviderError("provider 返回了不支持的图片 URL。")
        return self._download_image(url, timeout, parsed=parsed)

    def _download_image(self, url: str, timeout: float, parsed=None, include_auth: bool = False) -> tuple[bytes, str]:
        try:
            headers = {"User-Agent": "FrameLab/0.1"}
            if include_auth:
                headers["Authorization"] = f"Bearer {self.api_key}"
            with httpx.Client(timeout=max(1.0, timeout), follow_redirects=True) as client:
                response = client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise ProviderError(f"下载 provider 图片失败：{exc}") from exc
        if response.status_code >= 400:
            raise ProviderError(f"下载 provider 图片返回 HTTP {response.status_code}。")
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        suffix = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/webp": ".webp",
            "image/gif": ".gif",
        }.get(content_type)
        if suffix is None:
            path_suffix = Path((parsed or urlsplit(url)).path).suffix.lower()
            suffix = path_suffix if path_suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"} else ".png"
        return response.content, suffix


def _json_load(value: str, fallback: Any = None) -> Any:
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def provider_snapshot(provider: Provider) -> str:
    return public_url(provider.base_url)
