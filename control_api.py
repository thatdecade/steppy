"""
control_api.py

Qt-first control integration for Steppy.

Purpose
- Own the embedded Flask web server (web_server.py) inside the Qt process.
- Provide a Qt QObject that emits signals derived from control state.
- Avoid HTTP self-polling so the local Flask access log stays quiet when no browser is connected.

How it works
- Starts the Flask development server in a daemon thread so phones or tablets can control Steppy.
- Polls /api/status using Flask's in-process test client (no TCP, no access log noise).
- Diffs the status snapshots and emits signals for the parts that changed.

Public API
- ControlStatus
- ControlApiBridge
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

import web_server


@dataclass(frozen=True)
class ControlStatus:
    ok: bool
    state: str
    video_id: Optional[str]
    video_title: Optional[str]
    channel_title: Optional[str]
    thumbnail_url: Optional[str]
    duration_seconds: Optional[int]
    elapsed_seconds: Optional[float]
    difficulty: Optional[str]
    error: Optional[str] = None

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ControlStatus":
        ok_value = bool(payload.get("ok", False))
        error_text = str(payload.get("error") or "").strip() or None
        state_value = str(payload.get("state") or "").strip() or "UNKNOWN"

        def normalize_optional_text(value: Any) -> Optional[str]:
            if not isinstance(value, str):
                return None
            trimmed = value.strip()
            return trimmed or None

        video_id_value = normalize_optional_text(payload.get("video_id"))
        video_title_value = normalize_optional_text(payload.get("video_title"))
        channel_title_value = normalize_optional_text(payload.get("channel_title"))
        thumbnail_url_value = normalize_optional_text(payload.get("thumbnail_url"))
        difficulty_value = normalize_optional_text(payload.get("difficulty"))

        duration_seconds_value = payload.get("duration_seconds")
        duration_seconds_parsed: Optional[int] = None
        if isinstance(duration_seconds_value, int):
            duration_seconds_parsed = max(0, int(duration_seconds_value))

        elapsed_seconds_value = payload.get("elapsed_seconds")
        elapsed_seconds_parsed: Optional[float] = None
        if isinstance(elapsed_seconds_value, (int, float)):
            elapsed_seconds_parsed = float(max(0.0, float(elapsed_seconds_value)))

        return cls(
            ok=ok_value,
            state=state_value,
            video_id=video_id_value,
            video_title=video_title_value,
            channel_title=channel_title_value,
            thumbnail_url=thumbnail_url_value,
            duration_seconds=duration_seconds_parsed,
            elapsed_seconds=elapsed_seconds_parsed,
            difficulty=difficulty_value,
            error=error_text,
        )


class ControlApiBridge(QObject):
    """Qt bridge that owns the web server and emits signals based on control state.

    Signals are emitted on the Qt thread. The Flask server runs in a background thread.
    """

    status_updated = pyqtSignal(object)
    state_changed = pyqtSignal(str)
    video_changed = pyqtSignal(object)
    difficulty_changed = pyqtSignal(str)
    error_changed = pyqtSignal(str)

    def __init__(
        self,
        *,
        bind_host: str,
        bind_port: int,
        web_root_dir: Path,
        debug: bool = False,
        poll_interval_ms: int = 200,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)

        self._bind_host = str(bind_host)
        self._bind_port = int(bind_port)
        self._web_root_dir = Path(web_root_dir)
        self._debug = bool(debug)
        self._poll_interval_ms = int(max(50, poll_interval_ms))

        self._flask_application = web_server.create_flask_app(
            web_server.WebServerConfig(
                host=self._bind_host,
                port=self._bind_port,
                web_root_dir=self._web_root_dir,
                debug=self._debug,
            )
        )

        self._server_thread: Optional[threading.Thread] = None

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(self._poll_interval_ms)
        self._poll_timer.timeout.connect(self._poll_once)

        self._test_client_lock = threading.Lock()

        self._last_status: Optional[ControlStatus] = None
        self._last_state_value: Optional[str] = None
        self._last_video_id_value: Optional[str] = None
        self._last_difficulty_value: Optional[str] = None
        self._last_error_value: Optional[str] = None

    @property
    def flask_application(self):
        return self._flask_application

    def start(self) -> None:
        self.start_server()
        self.start_polling()

    def start_server(self) -> None:
        if self._server_thread is not None:
            return

        def run_server() -> None:
            self._flask_application.run(
                host=self._bind_host,
                port=self._bind_port,
                debug=self._debug,
                use_reloader=False,
                threaded=False,
            )

        self._server_thread = threading.Thread(
            target=run_server,
            name="steppy-web-server",
            daemon=True,
        )
        self._server_thread.start()

    def start_polling(self) -> None:
        if self._poll_timer.isActive():
            return
        self._poll_timer.start()
        self._poll_once()

    def stop_polling(self) -> None:
        if self._poll_timer.isActive():
            self._poll_timer.stop()

    def last_status(self) -> Optional[ControlStatus]:
        return self._last_status

    def _poll_once(self) -> None:
        status = self._read_status_in_process()
        self._last_status = status

        state_value = (status.state or "").strip().upper()
        video_id_value = status.video_id
        difficulty_value = status.difficulty
        error_text = status.error or ""

        video_changed = video_id_value != self._last_video_id_value
        state_changed = bool(state_value) and state_value != self._last_state_value
        difficulty_changed = difficulty_value is not None and difficulty_value != self._last_difficulty_value
        error_changed = error_text != (self._last_error_value or "")

        if video_changed:
            self._last_video_id_value = video_id_value
            self.video_changed.emit(status)

        if difficulty_changed and difficulty_value is not None:
            self._last_difficulty_value = difficulty_value
            self.difficulty_changed.emit(difficulty_value)

        if state_changed:
            self._last_state_value = state_value
            self.state_changed.emit(state_value)

        if error_changed:
            self._last_error_value = error_text
            self.error_changed.emit(error_text)

        self.status_updated.emit(status)

    def _read_status_in_process(self) -> ControlStatus:
        """Call /api/status using Flask's in-process test client.

        This avoids TCP traffic and avoids Werkzeug access log noise.
        """
        try:
            with self._test_client_lock:
                with self._flask_application.test_client() as test_client:
                    response = test_client.get("/api/status")
                    payload = response.get_json(silent=True)

            if isinstance(payload, dict):
                return ControlStatus.from_dict(payload)

            return ControlStatus(
                ok=False,
                state="ERROR",
                video_id=None,
                video_title=None,
                channel_title=None,
                thumbnail_url=None,
                duration_seconds=None,
                elapsed_seconds=None,
                difficulty=None,
                error="Invalid status payload",
            )
        except Exception as exception:
            return ControlStatus(
                ok=False,
                state="ERROR",
                video_id=None,
                video_title=None,
                channel_title=None,
                thumbnail_url=None,
                duration_seconds=None,
                elapsed_seconds=None,
                difficulty=None,
                error=str(exception),
            )
