# -*- coding: utf-8 -*-
from __future__ import annotations

########################
# control_api.py
########################
# Purpose:
# - Qt-first control integration layer.
# - Owns the embedded Flask server (web_server.py) inside the Qt process.
# - Polls /api/status using Flask in-process test client and emits Qt signals.
#
# Test Notes:
# - In this module, control_api is tested as a contract adapter for web_server:
#   - ControlStatus.from_dict normalization.
#   - Polling behavior and typed signal emission.
# - In Desktop shell and orchestration, it is tested for AppController reactions to status changes.
#
########################
# Design notes:
# - Treat web_server.py as stable. This bridge adapts to it, not the other way around.
# - Emit only typed, normalized status (ControlStatus). No raw dicts across module boundaries.
# - Polling interval is bounded. Minimum poll interval is enforced.
#
########################
# Interfaces:
# Public dataclasses:
# - ControlStatus(ok: bool, state: str, video_id: Optional[str], video_title: Optional[str], channel_title: Optional[str],
#                thumbnail_url: Optional[str], duration_seconds: Optional[int], elapsed_seconds: Optional[float],
#                difficulty: Optional[str], error: Optional[str] = None)
#   - ControlStatus.from_dict(payload: dict[str, Any]) -> ControlStatus
#
# Public classes:
# - class ControlApiBridge(QObject)
#   - Signals:
#     - status_updated(ControlStatus)
#     - state_changed(str)
#     - error_changed(str)
#     - video_changed(ControlStatus) [legacy]
#     - difficulty_changed(str) [legacy]
#   - Methods:
#     - flask_application() -> flask.Flask
#     - start() -> None
#     - start_server() -> None
#     - start_polling() -> None
#     - stop_polling() -> None
#     - last_status() -> Optional[ControlStatus]
#
########################

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from flask import Flask

from web_server import WebServerConfig, create_flask_app


try:
    from PyQt6.QtCore import QObject as _QtQObject  # type: ignore
    from PyQt6.QtCore import QTimer as _QtTimer  # type: ignore
    from PyQt6.QtCore import pyqtSignal as _QtSignal  # type: ignore

    _HAVE_QT = True
except Exception:
    _QtQObject = object  # type: ignore
    _QtTimer = None  # type: ignore
    _QtSignal = None  # type: ignore
    _HAVE_QT = False


class _FallbackSignal:
    def __init__(self) -> None:
        self._subscribers: list[Callable[..., None]] = []

    def connect(self, callback: Callable[..., None]) -> None:
        self._subscribers.append(callback)

    def emit(self, *args: Any) -> None:
        for callback in list(self._subscribers):
            try:
                callback(*args)
            except Exception:
                continue


def _make_signal(*signal_args: Any, **signal_kwargs: Any) -> Any:
    if _HAVE_QT and _QtSignal is not None:
        return _QtSignal(*signal_args, **signal_kwargs)
    return _FallbackSignal()


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
    def from_dict(cls, payload: dict[str, Any]) -> "ControlStatus":
        ok_value = bool(payload.get("ok", False))
        state_value = str(payload.get("state") or "IDLE").strip().upper() or "IDLE"

        video_id_value = payload.get("video_id")
        video_title_value = payload.get("video_title")
        channel_title_value = payload.get("channel_title")
        thumbnail_url_value = payload.get("thumbnail_url")
        duration_seconds_value = payload.get("duration_seconds")
        elapsed_seconds_value = payload.get("elapsed_seconds")
        difficulty_value = payload.get("difficulty")
        error_value = payload.get("error")

        return cls(
            ok=ok_value,
            state=state_value,
            video_id=str(video_id_value).strip() if isinstance(video_id_value, str) and video_id_value.strip() else None,
            video_title=str(video_title_value).strip()
            if isinstance(video_title_value, str) and video_title_value.strip()
            else None,
            channel_title=str(channel_title_value).strip()
            if isinstance(channel_title_value, str) and channel_title_value.strip()
            else None,
            thumbnail_url=str(thumbnail_url_value).strip()
            if isinstance(thumbnail_url_value, str) and thumbnail_url_value.strip()
            else None,
            duration_seconds=int(duration_seconds_value) if isinstance(duration_seconds_value, int) else None,
            elapsed_seconds=float(elapsed_seconds_value) if isinstance(elapsed_seconds_value, (int, float)) else None,
            difficulty=str(difficulty_value).strip()
            if isinstance(difficulty_value, str) and difficulty_value.strip()
            else None,
            error=str(error_value).strip() if isinstance(error_value, str) and error_value.strip() else None,
        )


class ControlApiBridge(_QtQObject):  # type: ignore[misc]
    status_updated = _make_signal(object)
    state_changed = _make_signal(str)
    error_changed = _make_signal(str)

    # Legacy signals kept for compatibility with older wiring.
    video_changed = _make_signal(object)
    difficulty_changed = _make_signal(str)

    def __init__(
        self,
        *,
        bind_host: str,
        bind_port: int,
        web_root_dir: Path,
        debug: bool = False,
        poll_interval_ms: int = 200,
        parent: Optional[Any] = None,
    ) -> None:
        if _HAVE_QT:
            super().__init__(parent)
        else:
            super().__init__()

        self._bind_host = str(bind_host)
        self._bind_port = int(bind_port)
        self._web_root_dir = Path(web_root_dir).resolve()
        self._debug = bool(debug)

        self._poll_interval_ms = max(50, int(poll_interval_ms))

        self._flask_app: Flask = create_flask_app(
            WebServerConfig(host=self._bind_host, port=self._bind_port, web_root_dir=self._web_root_dir, debug=self._debug)
        )

        self._server_thread: Optional[threading.Thread] = None

        self._poll_timer = None
        self._poll_thread: Optional[threading.Thread] = None
        self._poll_stop_event = threading.Event()

        self._test_client_lock = threading.Lock()
        self._read_lock = threading.Lock()

        self._last_status: Optional[ControlStatus] = None
        self._last_state_value: str = ""
        self._last_error_value: str = ""
        self._last_video_id_value: str = ""
        self._last_difficulty_value: str = ""

    def flask_application(self) -> Flask:
        return self._flask_app

    def start(self) -> None:
        self.start_server()
        self.start_polling()

    def start_server(self) -> None:
        if self._server_thread is not None:
            return

        def run_server() -> None:
            self._flask_app.run(
                host=self._bind_host,
                port=self._bind_port,
                debug=self._debug,
                use_reloader=False,
            )

        server_thread = threading.Thread(target=run_server, name="SteppyWebServer", daemon=True)
        server_thread.start()
        self._server_thread = server_thread

    def start_polling(self) -> None:
        if _HAVE_QT and _QtTimer is not None:
            if self._poll_timer is None:
                self._poll_timer = _QtTimer(self)  # type: ignore
                self._poll_timer.setInterval(self._poll_interval_ms)  # type: ignore
                self._poll_timer.timeout.connect(self._on_poll_tick_qt)  # type: ignore
            self._poll_timer.start()  # type: ignore
            return

        if self._poll_thread is not None:
            return

        self._poll_stop_event.clear()
        poll_thread = threading.Thread(target=self._poll_loop_fallback, name="SteppyControlPoll", daemon=True)
        poll_thread.start()
        self._poll_thread = poll_thread

    def stop_polling(self) -> None:
        if _HAVE_QT and self._poll_timer is not None:
            self._poll_timer.stop()  # type: ignore
            return
        self._poll_stop_event.set()
        self._poll_thread = None

    def last_status(self) -> Optional[ControlStatus]:
        with self._read_lock:
            return self._last_status

    # Polling internals

    def _on_poll_tick_qt(self) -> None:
        status = self._read_status_in_process()
        self._maybe_emit_status(status)

    def _poll_loop_fallback(self) -> None:
        interval_seconds = self._poll_interval_ms / 1000.0
        next_time = time.time()
        while not self._poll_stop_event.is_set():
            now = time.time()
            if now < next_time:
                time.sleep(min(0.05, next_time - now))
                continue
            next_time = now + interval_seconds
            status = self._read_status_in_process()
            self._maybe_emit_status(status)

    def _read_status_in_process(self) -> ControlStatus:
        try:
            with self._test_client_lock:
                client = self._flask_app.test_client()
                response = client.get("/api/status")
            json_payload = response.get_json(silent=True) or {}
            if not isinstance(json_payload, dict):
                json_payload = {"ok": False, "state": "ERROR", "error": "Invalid status payload"}
            return ControlStatus.from_dict(json_payload)
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

    def _maybe_emit_status(self, status: ControlStatus) -> None:
        with self._read_lock:
            self._last_status = status

        state_value = status.state
        error_text = status.error or ""
        video_id_value = status.video_id or ""
        difficulty_value = status.difficulty or ""

        if state_value != self._last_state_value:
            self._last_state_value = state_value
            self.state_changed.emit(state_value)

        if error_text != self._last_error_value:
            self._last_error_value = error_text
            self.error_changed.emit(error_text)

        if video_id_value != self._last_video_id_value:
            self._last_video_id_value = video_id_value
            self.video_changed.emit(status)

        if difficulty_value != self._last_difficulty_value:
            self._last_difficulty_value = difficulty_value
            self.difficulty_changed.emit(difficulty_value)

        self.status_updated.emit(status)
