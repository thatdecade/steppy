# -*- coding: utf-8 -*-
########################
# web_player_bridge.py
########################
# Purpose:
# - Qt widget and bridge for embedding and controlling a web based YouTube player.
# - Provides a stable player port interface for load, play, pause, seek, mute, volume and timing state updates.
#
########################
# Key Logic:
# - Emit reliable timing updates for TimingModel.
# - Provide normalized state updates for coordinator logic.
# - Keep JavaScript protocol details internal and provide normalized outputs.
#
########################
# Interfaces:
# Public dataclasses:
# - PlayerStateInfo(state_code: int, state_name: str, player_time_seconds: float,
#                  duration_seconds: Optional[float], video_id: Optional[str], is_ended: bool)
#
# Public classes:
# - class WebPlayerBridge(PyQt6.QtWidgets.QFrame)
#   - Signals:
#     - timeUpdated(float)
#     - stateChanged(PlayerStateInfo)
#     - playerReadyChanged(bool)
#     - errorOccurred(str)
#   - Methods:
#     - load_video(*, video_id_or_url: str, start_seconds: float = 0.0, autoplay: bool = True) -> None
#     - play() -> None
#     - pause() -> None
#     - seek(seconds: float) -> None
#     - set_muted(is_muted: bool) -> None
#     - set_volume(volume_percent: int) -> None
#
# Inputs:
# - Video id or URL and playback commands.
#
# Outputs:
# - Timing and state signals consumed by AppController and TimingModel.
#
########################
# Unit Tests:
# python web_player_bridge.py
########################

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtWidgets import QFrame, QVBoxLayout, QLabel

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView  # type: ignore
    WEBENGINE_AVAILABLE = True
except Exception:
    QWebEngineView = None  # type: ignore
    WEBENGINE_AVAILABLE = False


@dataclass(frozen=True)
class PlayerStateInfo:
    state_code: int
    state_name: str
    player_time_seconds: float
    duration_seconds: Optional[float]
    video_id: Optional[str]
    is_ended: bool


_YT_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{11}$")
_URL_ID_RE = re.compile(r"(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})")


def _extract_video_id(video_id_or_url: str) -> Optional[str]:
    text = str(video_id_or_url or "").strip()
    if _YT_ID_RE.match(text):
        return text
    match = _URL_ID_RE.search(text)
    if match:
        return str(match.group(1))
    return None


class _MockBackend(QObject):
    timeUpdated = pyqtSignal(float)
    stateChanged = pyqtSignal(object)
    playerReadyChanged = pyqtSignal(bool)
    errorOccurred = pyqtSignal(str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._video_id: Optional[str] = None
        self._duration_seconds: Optional[float] = None
        self._is_ready = False
        self._is_playing = False
        self._is_ended = False
        self._player_time_seconds = 0.0

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(50)
        self._tick_timer.timeout.connect(self._on_tick)

    def load_video(self, *, video_id_or_url: str, start_seconds: float = 0.0, autoplay: bool = True) -> None:
        video_id = _extract_video_id(video_id_or_url)
        if video_id is None and str(video_id_or_url).strip().lower() != "test":
            self.errorOccurred.emit("Invalid video id or url")
            return

        self._video_id = video_id or "test"
        self._duration_seconds = 24.0
        self._player_time_seconds = max(0.0, float(start_seconds))
        self._is_ready = True
        self._is_ended = False
        self.playerReadyChanged.emit(True)

        if autoplay:
            self.play()
        else:
            self._emit_state()

        self.timeUpdated.emit(float(self._player_time_seconds))

    def play(self) -> None:
        if not self._is_ready:
            return
        self._is_playing = True
        self._tick_timer.start()
        self._emit_state()

    def pause(self) -> None:
        self._is_playing = False
        self._tick_timer.stop()
        self._emit_state()

    def seek(self, seconds: float) -> None:
        self._player_time_seconds = max(0.0, float(seconds))
        self._is_ended = False
        self.timeUpdated.emit(float(self._player_time_seconds))
        self._emit_state()

    def set_muted(self, is_muted: bool) -> None:
        _ = is_muted

    def set_volume(self, volume_percent: int) -> None:
        _ = volume_percent

    def _emit_state(self) -> None:
        state_name = "paused"
        state_code = 2
        if self._is_ended:
            state_name = "ended"
            state_code = 0
        elif self._is_playing:
            state_name = "playing"
            state_code = 1
        info = PlayerStateInfo(
            state_code=state_code,
            state_name=state_name,
            player_time_seconds=float(self._player_time_seconds),
            duration_seconds=self._duration_seconds,
            video_id=self._video_id,
            is_ended=bool(self._is_ended),
        )
        self.stateChanged.emit(info)

    def _on_tick(self) -> None:
        if not self._is_playing:
            return
        self._player_time_seconds += 0.05
        self.timeUpdated.emit(float(self._player_time_seconds))

        if self._duration_seconds is not None and self._player_time_seconds >= float(self._duration_seconds):
            self._player_time_seconds = float(self._duration_seconds)
            self._is_ended = True
            self.pause()

        self._emit_state()


class WebPlayerBridge(QFrame):
    timeUpdated = pyqtSignal(float)
    stateChanged = pyqtSignal(object)
    playerReadyChanged = pyqtSignal(bool)
    errorOccurred = pyqtSignal(str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

        # For this chunk:
        # - Always provide a working mock backend.
        # - Use WebEngine only if installed and later required.
        self._mock = _MockBackend(self)
        self._mock.timeUpdated.connect(self.timeUpdated.emit)
        self._mock.stateChanged.connect(self.stateChanged.emit)
        self._mock.playerReadyChanged.connect(self.playerReadyChanged.emit)
        self._mock.errorOccurred.connect(self.errorOccurred.emit)

        self._placeholder = QLabel("WebPlayerBridge backend: mock", self)
        self._placeholder.setStyleSheet("color: white; background: #202028; padding: 6px;")
        self._layout.addWidget(self._placeholder)

        self._web_view: Optional[QWebEngineView] = None
        if WEBENGINE_AVAILABLE and QWebEngineView is not None:
            # Keep WebEngine optional for now.
            pass

    def load_video(self, *, video_id_or_url: str, start_seconds: float = 0.0, autoplay: bool = True) -> None:
        # Current chunk behavior:
        # - Use mock backend for deterministic development and tests.
        self._mock.load_video(video_id_or_url=video_id_or_url, start_seconds=start_seconds, autoplay=autoplay)

    def play(self) -> None:
        self._mock.play()

    def pause(self) -> None:
        self._mock.pause()

    def seek(self, seconds: float) -> None:
        self._mock.seek(seconds)

    def set_muted(self, is_muted: bool) -> None:
        self._mock.set_muted(is_muted)

    def set_volume(self, volume_percent: int) -> None:
        self._mock.set_volume(volume_percent)


def main() -> int:
    from PyQt6.QtWidgets import QApplication, QMainWindow

    app = QApplication([])
    window = QMainWindow()
    bridge = WebPlayerBridge()
    window.setCentralWidget(bridge)
    window.resize(640, 360)
    window.show()

    bridge.load_video(video_id_or_url="test", autoplay=True)

    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
