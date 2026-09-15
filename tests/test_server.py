from __future__ import annotations

import http.server
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import urllib.parse
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient
from PIL import Image

from framelab.api import create_app
from framelab.config import Settings
from framelab.imgbed import ImgBedClient
from framelab.models import GenerationJob, JobEvent
from framelab.providers import OpenAIAsyncProvider, ProviderResult, SubmittedTask
from framelab.worker import FrameLabWorker, _parent_watch


def png_bytes(color=(26, 122, 85)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (96, 64), color=color).save(buffer, format="PNG")
    return buffer.getvalue()


class FakeImageApiHandler(http.server.BaseHTTPRequestHandler):
    submissions = 0
    polls = 0
    image = png_bytes()
    last_path = ""
    last_content_type = ""
    last_body = b""
    last_json = {}

    def log_message(self, fmt, *args):
        pass

    def send_json(self, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        type(self).last_path = self.path
        type(self).last_content_type = self.headers.get("Content-Type", "")
        type(self).last_body = body
        try:
            type(self).last_json = json.loads(body) if "application/json" in self.headers.get("Content-Type", "") else {}
        except ValueError:
            type(self).last_json = {}
        if self.path in {"/v1/images/generations/async", "/v1/images/edits/async", "/v1/custom/generate"}:
            type(self).submissions += 1
            self.send_json({"task_id": "task-123", "status": "queued"})
            return
        if self.path == "/v1/images/generations":
            type(self).submissions += 1
            self.send_json({"id": "img-123", "status": "pending"})
            return
        self.send_error(404)

    def do_GET(self):  # noqa: N802
        if self.path == "/v1/images/tasks/task-123":
            type(self).polls += 1
            image_url = f"http://127.0.0.1:{self.server.server_port}/result.png"
            self.send_json({"task_id": "task-123", "status": "succeeded", "result": {"data": [{"url": image_url}]}})
            return
        if self.path == "/v1/images/img-123":
            self.send_json({"id": "img-123", "status": "completed", "content_url": "/v1/images/img-123/content"})
            return
        if self.path == "/v1/images/img-123/content":
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(self.image)))
            self.end_headers()
            self.wfile.write(self.image)
            return
        if self.path == "/result.png":
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(self.image)))
            self.end_headers()
            self.wfile.write(self.image)
            return
        self.send_error(404)


class FakeImgBedHandler(http.server.BaseHTTPRequestHandler):
    uploads = 0
    authorization = ""
    query = {}

    def log_message(self, fmt, *args):
        pass

    def do_POST(self):  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path != "/upload":
            self.send_error(404)
            return
        type(self).uploads += 1
        type(self).authorization = self.headers.get("Authorization", "")
        type(self).query = dict(urllib.parse.parse_qsl(parsed.query))
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        body = json.dumps([{"src": "/file/mock-image-id"}]).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

def make_settings(data_dir: Path) -> Settings:
    with mock.patch.dict(
        os.environ,
        {
            "FRAMELAB_DATA_DIR": str(data_dir),
            "CODEX_IMAGE_API_KEY": "test-key",
            "CODEX_IMAGE_BASE_URL": os.environ.get("TEST_IMAGE_BASE_URL", "http://127.0.0.1:9/v1"),
            "CODEX_IMAGE_POLL_INTERVAL": "0.01",
            "FRAMELAB_IMGBED_ENABLED": "false",
        },
        clear=False,
    ):
        return Settings.from_env()


def test_upload_persists_original_metadata_and_tags():
    with tempfile.TemporaryDirectory() as temporary:
        settings = make_settings(Path(temporary))
        with TestClient(create_app(settings)) as client:
            response = client.post(
                "/api/assets/upload",
                files={"file": ("sample.png", png_bytes(), "image/png")},
                data={"title": "测试图片", "tags_value": "地图, 手绘", "sync_enabled": "false"},
            )
            assert response.status_code == 201
            result = response.json()
            assert result["created"] is True
            asset = result["asset"]
            assert asset["title"] == "测试图片"
            assert asset["width"] == 96
            assert asset["height"] == 64
            assert asset["tags"] == ["地图", "手绘"]
            assert asset["remote"]["status"] == "disabled"
            assert client.get(asset["original_url"]).content == png_bytes()

            duplicate = client.post(
                "/api/assets/upload",
                files={"file": ("again.png", png_bytes(), "image/png")},
                data={"sync_enabled": "true"},
            ).json()
            assert duplicate["created"] is False
            assert duplicate["asset"]["id"] == asset["id"]


def test_api_started_worker_receives_api_parent_pid():
    with tempfile.TemporaryDirectory() as temporary:
        settings = make_settings(Path(temporary))
        with TestClient(create_app(settings)) as client, mock.patch("framelab.api.subprocess.Popen") as popen:
            popen.return_value.pid = 4321

            response = client.post("/api/runtime/workers")

            assert response.status_code == 202
            assert popen.call_args.kwargs["env"]["FRAMELAB_WORKER_PARENT_PID"] == str(os.getpid())


def test_worker_parent_watch_stops_when_launcher_exits():
    class StopProbe:
        def __init__(self, done):
            self.done = done
            self.stopped = False

        def stop(self):
            self.stopped = True
            self.done.set()

    done = threading.Event()
    launcher = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.2)"])
    probe = StopProbe(done)
    watcher = threading.Thread(target=_parent_watch, args=(probe, done, launcher.pid))
    watcher.start()
    launcher.wait(timeout=3)
    watcher.join(timeout=3)

    assert probe.stopped is True
    assert not watcher.is_alive()


def test_delete_asset_hides_asset_and_blocks_file():
    with tempfile.TemporaryDirectory() as temporary:
        settings = make_settings(Path(temporary))
        with TestClient(create_app(settings)) as client:
            uploaded = client.post(
                "/api/assets/upload",
                files={"file": ("sample.png", png_bytes(), "image/png")},
                data={"sync_enabled": "false"},
            ).json()
            asset = uploaded["asset"]

            response = client.delete(f"/api/assets/{asset['id']}")

            assert response.status_code == 200
            assert response.json() == {"ok": True, "id": asset["id"]}
            assert client.get("/api/assets").json()["total"] == 0
            assert client.get("/api/stats").json()["assets"] == 0
            assert client.get(f"/api/assets/{asset['id']}").status_code == 404
            assert client.get(asset["original_url"]).status_code == 404


def test_bulk_delete_removes_selected_assets():
    with tempfile.TemporaryDirectory() as temporary:
        settings = make_settings(Path(temporary))
        with TestClient(create_app(settings)) as client:
            first = client.post(
                "/api/assets/upload",
                files={"file": ("one.png", png_bytes((26, 122, 85)), "image/png")},
                data={"sync_enabled": "false"},
            ).json()["asset"]
            second = client.post(
                "/api/assets/upload",
                files={"file": ("two.png", png_bytes((130, 45, 90)), "image/png")},
                data={"sync_enabled": "false"},
            ).json()["asset"]

            response = client.post(
                "/api/assets/bulk-delete",
                json={"asset_ids": [first["id"], second["id"]]},
            )

            assert response.status_code == 200
            assert response.json() == {"deleted": 2}
            assert client.get("/api/assets").json()["total"] == 0
            assert client.get("/api/stats").json()["assets"] == 0
            assert client.get(f"/api/assets/{first['id']}").status_code == 404
            assert client.get(f"/api/assets/{second['id']}").status_code == 404


def test_media_directory_can_be_migrated_without_changing_asset_urls():
    with tempfile.TemporaryDirectory() as temporary:
        settings = make_settings(Path(temporary))
        target = Path(temporary) / "moved-media"
        with mock.patch("framelab.api.persist_media_dir") as persist:
            with TestClient(create_app(settings)) as client:
                uploaded = client.post(
                    "/api/assets/upload",
                    files={"file": ("sample.png", png_bytes(), "image/png")},
                    data={"sync_enabled": "false"},
                ).json()
                original_url = uploaded["asset"]["original_url"]
                response = client.patch("/api/config/media-dir", json={"media_dir": str(target)})

                assert response.status_code == 200
                assert response.json()["migrated"] is True
                assert response.json()["media_dir"] == str(target.resolve())
                assert persist.call_args.args[0] == target.resolve()
                assert client.get(original_url).content == png_bytes()
                assert list(target.rglob("*.png"))
                assert list(target.rglob("*.webp"))
                assert not list((settings.media_dir / "originals").rglob("*"))


def test_generation_worker_submits_once_and_records_trace():
    FakeImageApiHandler.submissions = 0
    FakeImageApiHandler.polls = 0
    FakeImageApiHandler.last_path = ""
    FakeImageApiHandler.last_body = b""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeImageApiHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
            os.environ,
            {
                "FRAMELAB_DATA_DIR": temporary,
                "CODEX_IMAGE_API_KEY": "test-key",
                "CODEX_IMAGE_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1",
                "CODEX_IMAGE_POLL_INTERVAL": "0.01",
                "FRAMELAB_IMGBED_ENABLED": "false",
            },
            clear=False,
            ):
            settings = Settings.from_env()
            with TestClient(create_app(settings)) as client:
                created = client.post(
                    "/api/generation/jobs",
                    json={
                        "prompt": "一张可回溯的测试图片",
                        "model": "gpt-image-2",
                        "size": "auto",
                        "quality": "high",
                        "timeout": 120,
                        "sync_enabled": False,
                    },
                )
                assert created.status_code == 202
                job_id = created.json()["id"]

                worker = FrameLabWorker(settings)
                try:
                    assert worker.run_once() is True
                finally:
                    worker.close()

                job = client.get(f"/api/generation/jobs/{job_id}").json()
                assert job["status"] == "succeeded"
                assert job["prompt"] == "一张可回溯的测试图片"
                assert job["provider_name"] == "Image Relay"
                assert job["upstream_task_id"] == "task-123"
                assert job["elapsed_ms"] is not None
                assert job["asset_id"]
                assert len(job["events"]) >= 3
                asset = client.get(f"/api/assets/{job['asset_id']}").json()
                assert asset["source"] == "generated"
                assert asset["generation"]["prompt"] == job["prompt"]
                assert FakeImageApiHandler.submissions == 1
                assert FakeImageApiHandler.polls >= 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_generation_job_can_override_endpoint_and_add_extra_params():
    FakeImageApiHandler.submissions = 0
    FakeImageApiHandler.polls = 0
    FakeImageApiHandler.last_path = ""
    FakeImageApiHandler.last_json = {}
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeImageApiHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
            os.environ,
            {
                "FRAMELAB_DATA_DIR": temporary,
                "CODEX_IMAGE_API_KEY": "test-key",
                "CODEX_IMAGE_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1",
                "CODEX_IMAGE_POLL_INTERVAL": "0.01",
                "FRAMELAB_IMGBED_ENABLED": "false",
            },
            clear=False,
        ):
            settings = Settings.from_env()
            endpoint = f"http://127.0.0.1:{server.server_port}/v1/custom/generate"
            with TestClient(create_app(settings)) as client:
                created = client.post(
                    "/api/generation/jobs",
                    json={
                        "prompt": "自定义请求地址测试",
                        "model": "gpt-image-2",
                        "size": "auto",
                        "quality": "high",
                        "timeout": 120,
                        "endpoint": endpoint,
                        "extra_params": {"async": True, "output_format": "png"},
                        "sync_enabled": False,
                    },
                )
                assert created.status_code == 202
                assert created.json()["endpoint"] == endpoint
                assert created.json()["extra_params"] == {"async": True, "output_format": "png"}
                job_id = created.json()["id"]

                worker = FrameLabWorker(settings)
                try:
                    assert worker.run_once() is True
                finally:
                    worker.close()

                job = client.get(f"/api/generation/jobs/{job_id}").json()
                assert job["status"] == "succeeded"
                assert job["request"]["endpoint"] == endpoint
                assert job["request"]["async"] is True
                assert job["request"]["output_format"] == "png"
                assert FakeImageApiHandler.last_path == "/v1/custom/generate"
                assert FakeImageApiHandler.last_json["async"] is True
                assert FakeImageApiHandler.last_json["output_format"] == "png"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_generation_job_rejects_invalid_endpoint_and_reserved_extra_param():
    with tempfile.TemporaryDirectory() as temporary:
        settings = make_settings(Path(temporary))
        with TestClient(create_app(settings)) as client:
            base_payload = {
                "prompt": "参数校验测试",
                "model": "gpt-image-2",
                "size": "auto",
                "quality": "high",
                "timeout": 120,
                "sync_enabled": False,
            }
            invalid_endpoint = client.post(
                "/api/generation/jobs",
                json={**base_payload, "endpoint": "/v1/images/generations"},
            )
            assert invalid_endpoint.status_code == 400

            reserved_param = client.post(
                "/api/generation/jobs",
                json={**base_payload, "extra_params": {"prompt": "不能覆盖"}},
            )
            assert reserved_param.status_code == 400

            custom_size = client.post(
                "/api/generation/jobs",
                json={**base_payload, "size": "1200x800"},
            )
            assert custom_size.status_code == 202
            assert custom_size.json()["size"] == "1200x800"


def test_documented_async_endpoint_polls_image_and_downloads_content():
    FakeImageApiHandler.submissions = 0
    FakeImageApiHandler.polls = 0
    FakeImageApiHandler.last_path = ""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeImageApiHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
            os.environ,
            {
                "FRAMELAB_DATA_DIR": temporary,
                "CODEX_IMAGE_API_KEY": "test-key",
                "CODEX_IMAGE_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1",
                "CODEX_IMAGE_POLL_INTERVAL": "0.01",
                "FRAMELAB_IMGBED_ENABLED": "false",
            },
            clear=False,
        ):
            settings = Settings.from_env()
            endpoint = f"http://127.0.0.1:{server.server_port}/v1/images/generations"
            with TestClient(create_app(settings)) as client:
                created = client.post(
                    "/api/generation/jobs",
                    json={
                        "prompt": "文档协议测试",
                        "model": "gpt-image-2",
                        "size": "1024x1024",
                        "quality": "high",
                        "timeout": 120,
                        "endpoint": endpoint,
                        "extra_params": {"async": True},
                        "sync_enabled": False,
                    },
                )
                assert created.status_code == 202
                job_id = created.json()["id"]

                worker = FrameLabWorker(settings)
                try:
                    assert worker.run_once() is True
                finally:
                    worker.close()

                job = client.get(f"/api/generation/jobs/{job_id}").json()
                assert job["status"] == "succeeded"
                assert job["upstream_task_id"] == "img-123"
                assert FakeImageApiHandler.submissions == 1
                assert FakeImageApiHandler.polls == 0
                asset = client.get(f"/api/assets/{job['asset_id']}").json()
                assert asset["size_bytes"] == len(png_bytes())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_cancel_queued_generation_job_records_event_and_worker_skips_it():
    with tempfile.TemporaryDirectory() as temporary:
        settings = make_settings(Path(temporary))
        with TestClient(create_app(settings)) as client:
            created = client.post(
                "/api/generation/jobs",
                json={
                    "prompt": "停止队列任务测试",
                    "model": "gpt-image-2",
                    "size": "auto",
                    "quality": "high",
                    "timeout": 120,
                    "sync_enabled": False,
                },
            ).json()
            job_id = created["id"]

            response = client.post(f"/api/generation/jobs/{job_id}/cancel")

            assert response.status_code == 200
            assert response.json()["status"] == "canceled"
            detail = client.get(f"/api/generation/jobs/{job_id}").json()
            assert detail["progress_message"] == "已手动停止本地任务"
            assert detail["events"][-1]["type"] == "canceled"
            assert detail["events"][-1]["payload"] == {
                "upstream_task_id": None,
                "local_only": False,
            }

            worker = FrameLabWorker(settings)
            try:
                assert worker.run_once() is False
            finally:
                worker.close()


def test_generation_job_delete_requires_terminal_state_and_cascades_events():
    with tempfile.TemporaryDirectory() as temporary:
        settings = make_settings(Path(temporary))
        with TestClient(create_app(settings)) as client:
            created = client.post(
                "/api/generation/jobs",
                json={
                    "prompt": "删除任务测试",
                    "model": "gpt-image-2",
                    "size": "auto",
                    "quality": "high",
                    "timeout": 120,
                    "sync_enabled": False,
                },
            ).json()
            job_id = created["id"]

            assert client.delete(f"/api/generation/jobs/{job_id}").status_code == 409
            canceled = client.post(f"/api/generation/jobs/{job_id}/cancel").json()
            event_id = client.get(f"/api/generation/jobs/{job_id}").json()["events"][0]["id"]

            response = client.delete(f"/api/generation/jobs/{job_id}")

            assert response.status_code == 200
            assert response.json() == {"ok": True, "id": job_id}
            assert client.get(f"/api/generation/jobs/{job_id}").status_code == 404
            with client.app.state.session_factory() as session:
                assert session.get(JobEvent, event_id) is None
            assert canceled["status"] == "canceled"


def test_canceled_job_is_not_saved_after_provider_returns_result():
    with tempfile.TemporaryDirectory() as temporary:
        settings = make_settings(Path(temporary))
        with TestClient(create_app(settings)) as client:
            created = client.post(
                "/api/generation/jobs",
                json={
                    "prompt": "取消竞态测试",
                    "model": "gpt-image-2",
                    "size": "auto",
                    "quality": "high",
                    "timeout": 120,
                    "sync_enabled": False,
                },
            ).json()
            job_id = created["id"]
            worker = FrameLabWorker(settings)

            def return_after_cancel(_task_id, _deadline, on_poll=None):
                with worker.session_factory() as session:
                    job = session.get(GenerationJob, job_id)
                    job.status = "canceled"
                    session.commit()
                return ProviderResult(image_bytes=png_bytes(), suffix=".png", response={"status": "completed"})

            try:
                with mock.patch.object(
                    OpenAIAsyncProvider,
                    "submit",
                    return_value=SubmittedTask(task_id="task-canceled", response={}, request={}),
                ), mock.patch.object(
                    OpenAIAsyncProvider,
                    "poll_until_done",
                    side_effect=return_after_cancel,
                ):
                    assert worker.run_once() is True
            finally:
                worker.close()

            job = client.get(f"/api/generation/jobs/{job_id}").json()
            assert job["status"] == "canceled"
            assert job["asset_id"] is None


def test_deleted_asset_job_is_marked_in_queue_payload():
    FakeImageApiHandler.submissions = 0
    FakeImageApiHandler.polls = 0
    FakeImageApiHandler.last_path = ""
    FakeImageApiHandler.last_body = b""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeImageApiHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
            os.environ,
            {
                "FRAMELAB_DATA_DIR": temporary,
                "CODEX_IMAGE_API_KEY": "test-key",
                "CODEX_IMAGE_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1",
                "CODEX_IMAGE_POLL_INTERVAL": "0.01",
                "FRAMELAB_IMGBED_ENABLED": "false",
            },
            clear=False,
        ):
            settings = Settings.from_env()
            with TestClient(create_app(settings)) as client:
                created = client.post(
                    "/api/generation/jobs",
                    json={
                        "prompt": "队列里应标记图片已删除",
                        "model": "gpt-image-2",
                        "size": "auto",
                        "quality": "high",
                        "timeout": 120,
                        "sync_enabled": False,
                    },
                ).json()
                worker = FrameLabWorker(settings)
                try:
                    assert worker.run_once() is True
                finally:
                    worker.close()

                job = client.get("/api/generation/jobs").json()[0]
                asset_id = job["asset_id"]
                assert job["asset_url"] == f"/api/assets/{asset_id}"
                assert "asset_deleted" not in job

                response = client.delete(f"/api/assets/{asset_id}")
                assert response.status_code == 200

                job = client.get("/api/generation/jobs").json()[0]
                assert job["asset_id"] == asset_id
                assert job["asset_deleted"] is True
                assert "asset_url" not in job
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_deleted_asset_is_not_uploaded_to_imgbed():
    FakeImgBedHandler.uploads = 0
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeImgBedHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
            os.environ,
            {
                "FRAMELAB_DATA_DIR": temporary,
                "FRAMELAB_IMGBED_BASE_URL": f"http://127.0.0.1:{server.server_port}",
                "FRAMELAB_IMGBED_API_TOKEN": "imgbed_test-token",
                "FRAMELAB_IMGBED_AUTH_CODE": "upload-test-code",
                "FRAMELAB_IMGBED_UPLOAD_CHANNEL": "discord",
                "FRAMELAB_IMGBED_UPLOAD_CHANNEL_NAME": "discard-image",
                "FRAMELAB_IMGBED_ENABLED": "true",
            },
            clear=False,
        ):
            settings = Settings.from_env()
            with TestClient(create_app(settings)) as client:
                uploaded = client.post(
                    "/api/assets/upload",
                    files={"file": ("sample.png", png_bytes(), "image/png")},
                    data={"sync_enabled": "true"},
                ).json()
                asset_id = uploaded["asset"]["id"]
                assert client.delete(f"/api/assets/{asset_id}").status_code == 200

                worker = FrameLabWorker(settings)
                try:
                    assert worker.run_once() is True
                finally:
                    worker.close()

                assert FakeImgBedHandler.uploads == 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_reference_image_uses_async_edits_multipart_and_is_traceable():
    FakeImageApiHandler.submissions = 0
    FakeImageApiHandler.polls = 0
    FakeImageApiHandler.last_path = ""
    FakeImageApiHandler.last_content_type = ""
    FakeImageApiHandler.last_body = b""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeImageApiHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
            os.environ,
            {
                "FRAMELAB_DATA_DIR": temporary,
                "CODEX_IMAGE_API_KEY": "test-key",
                "CODEX_IMAGE_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1",
                "CODEX_IMAGE_POLL_INTERVAL": "0.01",
                "FRAMELAB_IMGBED_ENABLED": "false",
            },
            clear=False,
        ):
            settings = Settings.from_env()
            with TestClient(create_app(settings)) as client:
                uploaded = client.post(
                    "/api/assets/upload",
                    files={"file": ("reference.png", png_bytes((130, 45, 90)), "image/png")},
                    data={"sync_enabled": "false"},
                ).json()
                reference_asset = uploaded["asset"]
                created = client.post(
                    "/api/generation/jobs",
                    json={
                        "prompt": "保留主体和构图，改成高级杂志风并提升光影细节",
                        "model": "gpt-image-2",
                        "size": "1024x1024",
                        "quality": "high",
                        "timeout": 120,
                        "reference_asset_id": reference_asset["id"],
                        "sync_enabled": False,
                    },
                )
                assert created.status_code == 202
                job_id = created.json()["id"]

                worker = FrameLabWorker(settings)
                try:
                    assert worker.run_once() is True
                finally:
                    worker.close()

                job = client.get(f"/api/generation/jobs/{job_id}").json()
                assert job["status"] == "succeeded"
                assert job["reference_asset_id"] == reference_asset["id"]
                assert job["reference_asset"]["id"] == reference_asset["id"]
                assert job["request"]["endpoint"].endswith("/v1/images/edits/async")
                assert job["request"]["image[]"]["size_bytes"] == len(png_bytes((130, 45, 90)))
                assert FakeImageApiHandler.last_path == "/v1/images/edits/async"
                assert "multipart/form-data" in FakeImageApiHandler.last_content_type
                assert b'name="image[]"' in FakeImageApiHandler.last_body
                assert png_bytes((130, 45, 90)) in FakeImageApiHandler.last_body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_imgbed_upload_uses_documented_auth_and_preserves_original():
    FakeImgBedHandler.uploads = 0
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeImgBedHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
            os.environ,
            {
                "FRAMELAB_DATA_DIR": temporary,
                "FRAMELAB_IMGBED_BASE_URL": f"http://127.0.0.1:{server.server_port}",
                "FRAMELAB_IMGBED_API_TOKEN": "imgbed_test-token",
                "FRAMELAB_IMGBED_AUTH_CODE": "upload-test-code",
                "FRAMELAB_IMGBED_UPLOAD_CHANNEL": "discord",
                "FRAMELAB_IMGBED_UPLOAD_CHANNEL_NAME": "discard-image",
                "FRAMELAB_IMGBED_ENABLED": "true",
            },
            clear=False,
        ):
            path = Path(temporary) / "sample.png"
            path.write_bytes(png_bytes())
            result = ImgBedClient(Settings.from_env()).upload(path, path.name, "image/png")
            assert result["remote_url"] == f"http://127.0.0.1:{server.server_port}/file/mock-image-id"
            assert FakeImgBedHandler.uploads == 1
            assert FakeImgBedHandler.authorization == "Bearer imgbed_test-token"
            assert FakeImgBedHandler.query["authCode"] == "upload-test-code"
            assert FakeImgBedHandler.query["uploadChannel"] == "discord"
            assert FakeImgBedHandler.query["channelName"] == "discard-image"
            assert FakeImgBedHandler.query["autoRetry"] == "false"
            assert FakeImgBedHandler.query["serverCompress"] == "false"
            assert FakeImgBedHandler.query["returnFormat"] == "full"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_provider_update_and_env_persistence():
    with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
        os.environ,
        {
            "FRAMELAB_DATA_DIR": temporary,
            "CODEX_IMAGE_API_KEY": "initial-key",
            "CODEX_IMAGE_BASE_URL": "https://api.initial.com/v1",
        },
        clear=False,
    ):
        test_env_file = Path(temporary) / ".env"
        test_env_file.write_text("CODEX_IMAGE_API_KEY=initial-key\nCODEX_IMAGE_BASE_URL=https://api.initial.com/v1\n", encoding="utf-8")

        with mock.patch("framelab.config.ENV_FILE", test_env_file):
            settings = Settings.from_env()
            with TestClient(create_app(settings)) as client:
                providers = client.get("/api/providers").json()
                assert len(providers) >= 1
                provider_id = providers[0]["id"]
                assert providers[0]["base_url"] == "https://api.initial.com/v1"
                assert providers[0]["ready"] is True
                assert "api_key" not in providers[0]

                session_factory = client.app.state.session_factory
                worker = FrameLabWorker(settings, session_factory=session_factory)

                response = client.patch(
                    f"/api/providers/{provider_id}",
                    json={
                        "base_url": "https://api.custom-relay.com/v1",
                        "api_key": "sk-brand-new-secret",
                    },
                )
                assert response.status_code == 200
                data = response.json()
                assert data["id"] == provider_id
                assert data["base_url"] == "https://api.custom-relay.com/v1"
                assert data["ready"] is True
                assert "api_key" not in data

                # Check os.environ updated
                assert os.environ["CODEX_IMAGE_BASE_URL"] == "https://api.custom-relay.com/v1"
                assert os.environ["CODEX_IMAGE_API_KEY"] == "sk-brand-new-secret"

                # Check .env file persisted
                saved_env = test_env_file.read_text(encoding="utf-8")
                assert "CODEX_IMAGE_BASE_URL=https://api.custom-relay.com/v1" in saved_env
                assert "CODEX_IMAGE_API_KEY=sk-brand-new-secret" in saved_env

                # Check subsequent GET /api/providers
                providers_after = client.get("/api/providers").json()
                target = next(p for p in providers_after if p["id"] == provider_id)
                assert target["base_url"] == "https://api.custom-relay.com/v1"
                assert target["ready"] is True

                # Check worker dynamically picks up the new secret from .env without restarting
                # simulate worker process having stale in-memory environment
                os.environ["CODEX_IMAGE_API_KEY"] = "sk-stale-worker-key"
                fake_job = GenerationJob(
                    id="fake-job-id",
                    provider_id=provider_id,
                    provider_name_snapshot="Custom Relay",
                    model="gpt-image-2",
                    prompt="test",
                )
                with session_factory() as session:
                    prov, key = worker._provider_for(session, fake_job)
                    assert key == "sk-brand-new-secret"
                    assert prov.base_url == "https://api.custom-relay.com/v1"


def test_generation_job_size_validation():
    with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
        os.environ,
        {
            "FRAMELAB_DATA_DIR": temporary,
            "CODEX_IMAGE_API_KEY": "test-key",
            "CODEX_IMAGE_BASE_URL": "http://127.0.0.1:9999/v1",
            "FRAMELAB_IMGBED_ENABLED": "false",
        },
        clear=False,
    ):
        settings = Settings.from_env()
        with TestClient(create_app(settings)) as client:
            valid_sizes = ["auto", "1:1", "16:9", "9:16", "21:9", "1:4", "8:1", "1024x1024", "1200x800"]
            for s in valid_sizes:
                resp = client.post(
                    "/api/generation/jobs",
                    json={
                        "prompt": "测试尺寸有效性",
                        "model": "gpt-image-2",
                        "size": s,
                        "quality": "high",
                        "timeout": 120,
                        "sync_enabled": False,
                    },
                )
                assert resp.status_code == 202, f"Failed for size {s}: {resp.text}"
                assert resp.json()["size"] == s.lower()

            invalid_sizes = ["invalid", "0x0", "-1:-1", "16:9:1", "auto1", "x800"]
            for s in invalid_sizes:
                resp = client.post(
                    "/api/generation/jobs",
                    json={
                        "prompt": "测试尺寸无效性",
                        "model": "gpt-image-2",
                        "size": s,
                        "quality": "high",
                        "timeout": 120,
                        "sync_enabled": False,
                    },
                )
                assert resp.status_code == 400, f"Expected 400 for size {s}"

