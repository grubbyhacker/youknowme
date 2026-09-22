from __future__ import annotations

import hashlib
import json
import os
import socket
import tempfile
import threading
from pathlib import Path

import pytest

from ykm.contracts import UploadFileInput, UploadRequest
from ykm.curator_trigger import CuratorUploadTrigger, CuratorUploadTriggerConfig
from ykm.intake import IntakeStore
from ykm.server import stage_upload_for_mcp
from ykm.shadow_workitem_mirror import (
    ShadowMirrorConfig,
    ShadowWorkItemMirror,
    build_envelope,
)


class ManualTimer:
    def __init__(self, interval: float, callback) -> None:
        self.interval = interval
        self.callback = callback
        self.daemon = False

    def start(self) -> None:
        return None

    def cancel(self) -> None:
        return None


def upload_request() -> UploadRequest:
    return UploadRequest(
        idempotency_key="test:shadow-mirror:upload-1",
        files=[UploadFileInput(filename="note.md", content="# Note\n")],
    )


def _trigger(launched: list[tuple[str, str]]) -> CuratorUploadTrigger:
    return CuratorUploadTrigger(
        CuratorUploadTriggerConfig(
            enabled=True, url="http://curator.example/launch", token="token"
        ),
        launcher=lambda _c, event_type, event_id: launched.append((event_type, event_id)),
        timer_factory=lambda interval, callback: ManualTimer(interval, callback),
    )


class _OneFrameServer:
    """Accepts one connection, records one framed envelope, replies once."""

    def __init__(self, socket_path: str, reply: bytes | None = None) -> None:
        self.frames: list[bytes] = []
        self.connections = 0
        self.reply = reply or (
            b'{"matched":true,"work_item_id":"work-1",'
            b'"event_id":"event-1","duplicate":false,"launched":false}\n'
        )
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(socket_path)
        self._sock.listen(4)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            self.connections += 1
            with conn:
                conn.settimeout(2.0)
                data = b""
                while not data.endswith(b"\n"):
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    data += chunk
                self.frames.append(data)
                conn.sendall(self.reply)

    def close(self) -> None:
        self._sock.close()


def _short_socket_path() -> str:
    # AF_UNIX sun_path is capped (~104 bytes); pytest's tmp_path is too long, so
    # bind under a short system-temp dir instead.
    return os.path.join(tempfile.mkdtemp(prefix="ykmshadow"), "s.sock")


def test_disabled_mirror_is_noop() -> None:
    mirror = ShadowWorkItemMirror(ShadowMirrorConfig(socket_path=""))
    assert mirror.config.enabled is False
    assert mirror.mirror_upload("upl_1") is False


def test_build_envelope_is_deterministic_and_matches_route_tuple() -> None:
    envelope = build_envelope("upl_abc")
    canonical = json.dumps(
        {
            "action": "completed",
            "event_kind": "upload",
            "namespace": "grubbyhacker/youknowme",
            "object_kind": "upload",
            "source": "youknowme",
            "upload_id": "upl_abc",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    expected_digest = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert envelope == {
        "signal_id": "upl_abc",
        "source_delivery_id": "upl_abc",
        "transport_stream": "youknowme.upload.upl_abc",
        "transport_sequence": 1,
        "source": "youknowme",
        "namespace": "grubbyhacker/youknowme",
        "object_kind": "upload",
        "object_id": "upl_abc",
        "event_kind": "upload",
        "action": "completed",
        "actor_class": "service",
        "source_revision": "upl_abc",
        "payload_digest": expected_digest,
        "evidence_ref": "youknowme://uploads/upl_abc",
    }
    assert build_envelope("upl_abc") == envelope




def test_mirror_rejected_reply_is_not_success() -> None:
    socket_path = _short_socket_path()
    server = _OneFrameServer(
        socket_path,
        b'{"matched":false,"work_item_id":"","event_id":"",'
        b'"duplicate":false,"launched":false}\n',
    )
    try:
        mirror = ShadowWorkItemMirror(ShadowMirrorConfig(socket_path=socket_path))
        assert mirror.mirror_upload("upl_unmatched") is False
    finally:
        server.close()

def test_mirror_sends_exactly_one_valid_frame(tmp_path: Path) -> None:
    socket_path = _short_socket_path()
    server = _OneFrameServer(socket_path)
    try:
        mirror = ShadowWorkItemMirror(ShadowMirrorConfig(socket_path=socket_path))
        assert mirror.mirror_upload("upl_one") is True
    finally:
        server.close()

    assert server.connections == 1
    assert len(server.frames) == 1
    frame = server.frames[0]
    assert frame.endswith(b"\n")
    assert frame.count(b"\n") == 1
    assert json.loads(frame) == build_envelope("upl_one")


def test_mirror_socket_failure_is_isolated(tmp_path: Path) -> None:
    # No server is listening at this path: connect fails, but mirror_upload must
    # swallow it, return False, and never raise.
    mirror = ShadowWorkItemMirror(
        ShadowMirrorConfig(socket_path=str(tmp_path / "absent.sock"))
    )
    assert mirror.mirror_upload("upl_fail") is False


def test_mirror_failure_does_not_affect_upload_or_launch(tmp_path: Path) -> None:
    launched: list[tuple[str, str]] = []
    store = IntakeStore(tmp_path / "intake")
    trigger = _trigger(launched)
    # Socket path points nowhere -> mirror fails internally.
    mirror = ShadowWorkItemMirror(
        ShadowMirrorConfig(socket_path=str(tmp_path / "absent.sock"))
    )

    response = stage_upload_for_mcp(
        store, upload_request(), build_id="build-1", trigger=trigger, mirror=mirror
    )

    # Upload still succeeds; mirror never triggers a launch.
    assert response.upload_id
    assert launched == []


def test_stage_upload_without_mirror_is_unchanged(tmp_path: Path) -> None:
    launched: list[tuple[str, str]] = []
    store = IntakeStore(tmp_path / "intake")
    trigger = _trigger(launched)

    response = stage_upload_for_mcp(
        store, upload_request(), build_id="build-1", trigger=trigger
    )

    assert response.upload_id
    assert launched == []


def test_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("YKM_SHADOW_MIRROR_SOCKET_PATH", raising=False)
    assert ShadowMirrorConfig.from_env().enabled is False
    monkeypatch.setenv("YKM_SHADOW_MIRROR_SOCKET_PATH", "/run/ykm/shadow.sock")
    config = ShadowMirrorConfig.from_env()
    assert config.enabled is True
    assert config.socket_path == "/run/ykm/shadow.sock"
