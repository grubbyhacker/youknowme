from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

import pytest

from ykm.contracts import UploadFileInput, UploadRequest
from ykm.curator_trigger import (
    CuratorUploadTrigger,
    CuratorUploadTriggerConfig,
    launch_curator,
)
from ykm.intake import IntakeError, IntakeStore
from ykm.server import stage_upload_for_mcp


class ManualTimer:
    def __init__(self, interval: float, callback: Callable[[], None]) -> None:
        self.interval = interval
        self.callback = callback
        self.daemon = False
        self.started = False
        self.cancelled = False

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True

    def fire(self) -> None:
        if not self.cancelled:
            self.callback()


def upload_request(filename: str = "note.md", content: str = "# Note\n") -> UploadRequest:
    return UploadRequest(files=[UploadFileInput(filename=filename, content=content)])


def test_disabled_trigger_ignores_upload() -> None:
    timers: list[ManualTimer] = []
    trigger = CuratorUploadTrigger(
        CuratorUploadTriggerConfig(enabled=False),
        timer_factory=lambda interval, callback: timers.append(ManualTimer(interval, callback))
        or timers[-1],
    )

    trigger.record_upload("upl_1")

    assert timers == []


def test_upload_trigger_debounces_to_latest_upload() -> None:
    timers: list[ManualTimer] = []
    launched: list[str] = []
    config = CuratorUploadTriggerConfig(
        enabled=True,
        url="http://curator.example/launch",
        token="token",
        debounce_seconds=90,
    )
    trigger = CuratorUploadTrigger(
        config,
        launcher=lambda launch_config: launched.append(launch_config.url),
        timer_factory=lambda interval, callback: timers.append(ManualTimer(interval, callback))
        or timers[-1],
    )

    trigger.record_upload("upl_1")
    trigger.record_upload("upl_2")

    assert [timer.interval for timer in timers] == [90, 90]
    assert timers[0].cancelled is True
    assert timers[1].started is True
    timers[0].fire()
    assert launched == []
    timers[1].fire()
    assert launched == ["http://curator.example/launch"]


def test_stop_cancels_pending_trigger() -> None:
    timers: list[ManualTimer] = []
    launched: list[str] = []
    trigger = CuratorUploadTrigger(
        CuratorUploadTriggerConfig(enabled=True, url="http://curator.example/launch", token="token"),
        launcher=lambda launch_config: launched.append(launch_config.url),
        timer_factory=lambda interval, callback: timers.append(ManualTimer(interval, callback))
        or timers[-1],
    )

    trigger.record_upload("upl_1")
    trigger.stop()
    timers[0].fire()

    assert timers[0].cancelled is True
    assert launched == []


def test_launch_curator_posts_bearer_token() -> None:
    received: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            received["path"] = self.path
            received["authorization"] = self.headers.get("Authorization", "")
            self.send_response(202)
            self.end_headers()

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        launch_curator(
            CuratorUploadTriggerConfig(
                enabled=True,
                url=f"http://127.0.0.1:{server.server_port}/v1/launch",
                token="secret",
                timeout_seconds=2,
            )
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)

    assert received == {
        "path": "/v1/launch",
        "authorization": "Bearer secret",
    }


def test_stage_upload_for_mcp_records_successful_upload(tmp_path: Path) -> None:
    uploads: list[str] = []
    store = IntakeStore(tmp_path / "intake")
    trigger = CuratorUploadTrigger(
        CuratorUploadTriggerConfig(enabled=True, url="http://curator.example/launch", token="token"),
        launcher=lambda _config: None,
        timer_factory=lambda interval, callback: ManualTimer(interval, callback),
    )
    trigger.record_upload = uploads.append  # type: ignore[method-assign]

    response = stage_upload_for_mcp(
        store,
        upload_request(),
        build_id="build-1",
        trigger=trigger,
    )

    assert uploads == [response.upload_id]


def test_stage_upload_for_mcp_does_not_trigger_rejected_upload(tmp_path: Path) -> None:
    uploads: list[str] = []
    store = IntakeStore(tmp_path / "intake")
    trigger = CuratorUploadTrigger(
        CuratorUploadTriggerConfig(enabled=True, url="http://curator.example/launch", token="token"),
        launcher=lambda _config: None,
        timer_factory=lambda interval, callback: ManualTimer(interval, callback),
    )
    trigger.record_upload = uploads.append  # type: ignore[method-assign]

    with pytest.raises(IntakeError):
        stage_upload_for_mcp(
            store,
            upload_request("unsafe.md", "<script>alert('no')</script>"),
            build_id="build-1",
            trigger=trigger,
        )

    assert uploads == []
