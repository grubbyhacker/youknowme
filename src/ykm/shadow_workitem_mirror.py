"""Optional, disabled-by-default shadow mirror of successful uploads.

When a Unix-socket path is configured, a completed upload is mirrored as
exactly one source-neutral ``upload.completed`` domain-fact envelope to the
signal-plane ``workitem-shadow-ingress`` socket. The mirror:

- makes NO broker launch and never enqueues to any launcher;
- never replaces, delays, or gates the existing Curator upload trigger;
- is fully isolated -- any mirror failure is bounded and logged (without the
  reply body or any secret) and can NEVER turn a successful upload into a
  failure;
- opens no network endpoint (Unix domain socket only).

The envelope tuple is fixed to the deployed ``ykm-upload-intake`` route
definition: source=youknowme, namespace=grubbyhacker/youknowme,
object_kind=upload, event_kind=upload, action=completed. Every evidence field
is derived deterministically from the immutable completed-upload identity
(``upload_id``), so the same upload always mirrors the same envelope.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import socket
from dataclasses import dataclass

module_logger = logging.getLogger(__name__)

# Fixed domain-fact tuple, matching roles/signal-plane/.../ykm-upload-intake.json.
_SOURCE = "youknowme"
_NAMESPACE = "grubbyhacker/youknowme"
_OBJECT_KIND = "upload"
_EVENT_KIND = "upload"
_ACTION = "completed"
_ACTOR_CLASS = "service"

# Bounds: the ingress caps a framed request at 64 KiB; the derived envelope is
# far smaller, but the read of the reply is bounded regardless.
_MAX_REPLY_BYTES = 64 << 10
_CONNECT_TIMEOUT_SECONDS = 2.0
_IO_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True)
class ShadowMirrorConfig:
    """Disabled by default: an empty socket_path makes the mirror a no-op."""

    socket_path: str = ""
    timeout_seconds: float = _IO_TIMEOUT_SECONDS

    @classmethod
    def from_env(cls) -> "ShadowMirrorConfig":
        return cls(
            socket_path=os.getenv("YKM_SHADOW_MIRROR_SOCKET_PATH", "").strip(),
            timeout_seconds=_env_float(
                "YKM_SHADOW_MIRROR_TIMEOUT_SECONDS", _IO_TIMEOUT_SECONDS
            ),
        )

    @property
    def enabled(self) -> bool:
        return bool(self.socket_path)


def _canonical_evidence(upload_id: str) -> str:
    """Canonical JSON evidence object: the exact tuple plus the upload id.

    Sorted keys + compact separators make the digest stable for a given
    upload_id across processes and Python builds.
    """
    return json.dumps(
        {
            "action": _ACTION,
            "event_kind": _EVENT_KIND,
            "namespace": _NAMESPACE,
            "object_kind": _OBJECT_KIND,
            "source": _SOURCE,
            "upload_id": upload_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def build_envelope(upload_id: str) -> dict[str, object]:
    """Build the deterministic shadow-ingress envelope for a completed upload.

    Every field is derived from the immutable ``upload_id`` so the mapping is a
    pure function of the completed-upload identity.
    """
    evidence = _canonical_evidence(upload_id)
    payload_digest = "sha256:" + hashlib.sha256(evidence.encode("utf-8")).hexdigest()
    return {
        "signal_id": upload_id,
        "source_delivery_id": upload_id,
        "transport_stream": "youknowme.upload." + upload_id,
        "transport_sequence": 1,
        "source": _SOURCE,
        "namespace": _NAMESPACE,
        "object_kind": _OBJECT_KIND,
        "object_id": upload_id,
        "event_kind": _EVENT_KIND,
        "action": _ACTION,
        "actor_class": _ACTOR_CLASS,
        "source_revision": upload_id,
        "payload_digest": payload_digest,
        "evidence_ref": "youknowme://uploads/" + upload_id,
    }


class ShadowWorkItemMirror:
    """Mirrors a completed upload as one shadow-ingress envelope, best-effort."""

    def __init__(
        self,
        config: ShadowMirrorConfig,
        *,
        logger: logging.Logger = module_logger,
    ) -> None:
        self.config = config
        self._logger = logger

    def mirror_upload(self, upload_id: str) -> bool:
        """Send exactly one envelope for a completed upload.

        Returns True when the frame was sent and a bounded reply was read;
        False on any bounded, logged failure. NEVER raises: the caller's upload
        has already succeeded and must not be affected.
        """
        if not self.config.enabled:
            return False
        try:
            frame = (json.dumps(build_envelope(upload_id)) + "\n").encode("utf-8")
            self._send_one_frame(frame)
        except Exception as exc:  # noqa: BLE001 - isolation boundary
            # Log the failure class only -- never the reply body, socket
            # contents, or any secret.
            self._logger.warning(
                "Shadow upload mirror failed for upload %s: %s",
                upload_id,
                type(exc).__name__,
            )
            return False
        self._logger.info("Shadow upload mirror sent for upload %s", upload_id)
        return True

    def _send_one_frame(self, frame: bytes) -> None:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(_CONNECT_TIMEOUT_SECONDS)
            sock.connect(self.config.socket_path)
            sock.settimeout(self.config.timeout_seconds)
            sock.sendall(frame)
            try:
                sock.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            # Read (and discard) a bounded reply so a well-behaved server sees
            # the frame consumed; content is intentionally never inspected.
            sock.recv(_MAX_REPLY_BYTES)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    value = float(raw)
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value
