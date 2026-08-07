from __future__ import annotations

import http.server
import io
import json
import os
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
from framelab.worker import FrameLabWorker


def png_bytes(color=(26, 122, 85)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (96, 64), color=color).save(buffer, format="PNG")
    return buffer.getvalue()


class FakeImageApiHandler(http.server.BaseHTTPRequestHandler):
    submissions = 0
    polls = 0
    image = png_bytes()

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
        self.rfile.read(length)
        if self.path == "/v1/images/generations/async":
            type(self).submissions += 1
            self.send_json({"task_id": "task-123", "status": "queued"})
            return
        self.send_error(404)

    def do_GET(self):  # noqa: N802
        if self.path == "/v1/images/tasks/task-123":
            type(self).polls += 1
            image_url = f"http://127.0.0.1:{self.server.server_port}/result.png"
            self.send_json({"task_id": "task-123", "status": "succeeded", "result": {"data": [{"url": image_url}]}})
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


def test_generation_worker_submits_once_and_records_trace():
    FakeImageApiHandler.submissions = 0
    FakeImageApiHandler.polls = 0
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
