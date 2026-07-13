from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from typing import Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


module_logger = logging.getLogger(__name__)


class TimerLike(Protocol):
    daemon: bool

    def start(self) -> None: ...

    def cancel(self) -> None: ...


TimerFactory = Callable[[float, Callable[[], None]], TimerLike]
LaunchFn = Callable[["CuratorUploadTriggerConfig", str, str], None]


@dataclass(frozen=True)
class CuratorUploadTriggerConfig:
    enabled: bool = False
    url: str = ""
    token: str = ""
    debounce_seconds: float = 90.0
    feedback_debounce_seconds: float = 0.0
    timeout_seconds: float = 20.0

    @classmethod
    def from_env(cls) -> "CuratorUploadTriggerConfig":
        return cls(
            enabled=_env_bool("YKM_CURATOR_TRIGGER_ENABLED", default=False),
            url=os.getenv("YKM_CURATOR_TRIGGER_URL", "").strip(),
            token=os.getenv("YKM_CURATOR_TRIGGER_TOKEN", "").strip(),
            debounce_seconds=_env_float("YKM_CURATOR_TRIGGER_DEBOUNCE_SECONDS", 90.0),
            feedback_debounce_seconds=_env_float(
                "YKM_CURATOR_TRIGGER_FEEDBACK_DEBOUNCE_SECONDS", 0.0
            ),
            timeout_seconds=_env_float("YKM_CURATOR_TRIGGER_TIMEOUT_SECONDS", 20.0),
        )

    @property
    def active(self) -> bool:
        return self.enabled and bool(self.url and self.token)


class CuratorUploadTrigger:
    def __init__(
        self,
        config: CuratorUploadTriggerConfig,
        *,
        launcher: LaunchFn | None = None,
        timer_factory: TimerFactory = threading.Timer,
        logger: logging.Logger = module_logger,
    ) -> None:
        self.config = config
        self._launcher = launch_curator if launcher is None else launcher
        self._timer_factory = timer_factory
        self._logger = logger
        self._lock = threading.Lock()
        self._timer: TimerLike | None = None
        self._pending_event: tuple[str, str] | None = None

        if self.config.enabled and not self.config.active:
            self._logger.warning(
                "Curator trigger enabled without URL/token; trigger disabled"
            )

    def record_upload(self, upload_id: str) -> None:
        self._record_event("upload", upload_id, self.config.debounce_seconds)

    def record_feedback(self, feedback_id: str) -> None:
        self._record_event("feedback", feedback_id, self.config.feedback_debounce_seconds)

    def _record_event(self, event_type: str, event_id: str, debounce_seconds: float) -> None:
        if not self.config.enabled:
            return
        if not self.config.active:
            self._logger.warning(
                "Skipping Curator trigger for %s %s because URL/token is not configured",
                event_type,
                event_id,
            )
            return

        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._pending_event = (event_type, event_id)
            self._timer = self._timer_factory(debounce_seconds, self._launch_pending)
            self._timer.daemon = True
            self._timer.start()

        self._logger.info(
            "Scheduled Curator trigger for %s %s in %.1f seconds",
            event_type,
            event_id,
            debounce_seconds,
        )

    def stop(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._pending_event = None

    def _launch_pending(self) -> None:
        with self._lock:
            pending_event = self._pending_event
            self._pending_event = None
            self._timer = None

        if pending_event is None:
            return

        event_type, event_id = pending_event
        self._logger.info("Launching Curator after trigger for %s %s", event_type, event_id)
        try:
            self._launcher(self.config, event_type, event_id)
        except Exception:
            self._logger.exception("Curator trigger failed for %s %s", event_type, event_id)


def launch_curator(
    config: CuratorUploadTriggerConfig, event_type: str, event_id: str
) -> None:
    request = Request(
        config.url,
        method="POST",
        headers={
            "Authorization": f"Bearer {config.token}",
            "Idempotency-Key": f"ykm:curator-trigger:v1:{event_type}:{event_id}",
        },
    )
    try:
        with urlopen(request, timeout=config.timeout_seconds) as response:
            status = response.status
            response.read()
    except HTTPError as exc:
        raise RuntimeError(f"Curator launch request failed with HTTP {exc.code}") from exc
    except URLError as exc:
        raise RuntimeError(f"Curator launch request failed: {exc.reason}") from exc

    if status < 200 or status >= 300:
        raise RuntimeError(f"Curator launch request failed with HTTP {status}")

    module_logger.info("Curator launch request accepted with HTTP %s", status)


def _env_bool(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    value = float(raw)
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value
