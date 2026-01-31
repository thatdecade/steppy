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
import os
import re
from typing import Any, Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal, QUrl  # type: ignore
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QFrame, QLabel, QStackedLayout, QVBoxLayout, QWidget
from PyQt6.QtWebEngineWidgets import QWebEngineView  # type: ignore


_YOUTUBE_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{11}$")


def _extract_video_id(video_id_or_url: str) -> Optional[str]:
    text = str(video_id_or_url or "").strip()
    if not text:
        return None
    if _YOUTUBE_ID_REGEX.match(text):
        return text

    # Common URL shapes:
    # - https://www.youtube.com/watch?v=<id>
    # - https://youtu.be/<id>
    # - https://www.youtube.com/embed/<id>
    match = re.search(r"[?&]v=([a-zA-Z0-9_-]{11})", text)
    if match:
        return match.group(1)
    match = re.search(r"youtu\.be/([a-zA-Z0-9_-]{11})", text)
    if match:
        return match.group(1)
    match = re.search(r"/embed/([a-zA-Z0-9_-]{11})", text)
    if match:
        return match.group(1)
    return None


def _state_name_for_youtube_state_code(state_code: int) -> str:
    # YouTube IFrame API state codes:
    # -1 unstarted, 0 ended, 1 playing, 2 paused, 3 buffering, 5 video cued
    mapping = {
        -1: "unstarted",
        0: "ended",
        1: "playing",
        2: "paused",
        3: "buffering",
        5: "cued",
    }
    return mapping.get(int(state_code), "unknown")


@dataclass(frozen=True)
class PlayerStateInfo:
    state_code: int
    state_name: str
    player_time_seconds: float
    duration_seconds: Optional[float]
    video_id: Optional[str]
    is_ended: bool


class _MockBackend(QObject):
    """Lightweight timer backend that only emits time and state updates.

    This backend exists purely as a fallback when WebEngine is not usable or when
    the input cannot be parsed as a YouTube id. It does not draw a video.
    """

    timeUpdated = pyqtSignal(float)
    stateChanged = pyqtSignal(object)
    playerReadyChanged = pyqtSignal(bool)
    errorOccurred = pyqtSignal(str)

    def __init__(self, *, time_step_seconds: float = 1.0 / 60.0) -> None:
        super().__init__()
        self._time_step_seconds = float(time_step_seconds)
        self._video_id: Optional[str] = None
        self._duration_seconds: Optional[float] = None
        self._player_time_seconds = 0.0
        self._is_ready = False
        self._state_code = -1
        self._tick_timer = QTimer()
        self._tick_timer.setInterval(int(round(self._time_step_seconds * 1000.0)))
        self._tick_timer.timeout.connect(self._on_tick)

    def backend_name(self) -> str:
        return "mock"

    def is_ready(self) -> bool:
        return bool(self._is_ready)

    def load_video(self, *, video_id_or_url: str, start_seconds: float, autoplay: bool) -> None:
        requested_video_id = str(video_id_or_url or "").strip() or None
        if requested_video_id is None:
            self.errorOccurred.emit("Missing video id")
            return

        self._video_id = requested_video_id
        self._duration_seconds = None
        self._player_time_seconds = max(0.0, float(start_seconds))
        self._is_ready = True
        self._state_code = 1 if bool(autoplay) else 2
        self.playerReadyChanged.emit(True)
        self._emit_state_changed()

        self.timeUpdated.emit(self._player_time_seconds)
        if bool(autoplay):
            self._tick_timer.start()
        else:
            self._tick_timer.stop()

    def play(self) -> None:
        if self._video_id is None:
            return
        self._state_code = 1
        self._emit_state_changed()
        self._tick_timer.start()

    def pause(self) -> None:
        if self._video_id is None:
            return
        self._state_code = 2
        self._emit_state_changed()
        self._tick_timer.stop()

    def seek(self, seconds: float) -> None:
        if self._video_id is None:
            return
        self._player_time_seconds = max(0.0, float(seconds))
        self.timeUpdated.emit(self._player_time_seconds)
        self._emit_state_changed()

    def set_muted(self, is_muted: bool) -> None:
        _ = is_muted

    def set_volume(self, volume_percent: int) -> None:
        _ = volume_percent

    def _emit_state_changed(self) -> None:
        state_code = int(self._state_code)
        state_name = _state_name_for_youtube_state_code(state_code)
        state_info = PlayerStateInfo(
            state_code=state_code,
            state_name=state_name,
            player_time_seconds=float(self._player_time_seconds),
            duration_seconds=self._duration_seconds,
            video_id=self._video_id,
            is_ended=(state_code == 0),
        )
        self.stateChanged.emit(state_info)

    def _on_tick(self) -> None:
        if self._video_id is None:
            return
        if int(self._state_code) != 1:
            return
        self._player_time_seconds += float(self._time_step_seconds)
        self.timeUpdated.emit(float(self._player_time_seconds))


class _WebEngineBackend(QObject):
    """Backend that embeds the YouTube IFrame API in a QWebEngineView."""

    timeUpdated = pyqtSignal(float)
    stateChanged = pyqtSignal(object)
    playerReadyChanged = pyqtSignal(bool)
    errorOccurred = pyqtSignal(str)

    def __init__(self, view: "QWebEngineView") -> None:
        super().__init__()
        self._view = view
        self._html_loaded = False
        self._has_active_video = False

        self._video_id: Optional[str] = None
        self._duration_seconds: Optional[float] = None
        self._last_emitted_time_seconds: Optional[float] = None
        self._last_emitted_state_code: Optional[int] = None
        self._last_emitted_ready: Optional[bool] = None

        self._poll_timer = QTimer()
        self._poll_timer.setInterval(50)
        self._poll_timer.timeout.connect(self._poll_snapshot)

        self._pending_js_calls: list[str] = []

        self._view.loadFinished.connect(self._on_load_finished)
        self._load_bootstrap_html()

    def backend_name(self) -> str:
        return "webengine"

    def is_ready(self) -> bool:
        return bool(self._last_emitted_ready)

    def load_video(self, *, video_id_or_url: str, start_seconds: float, autoplay: bool) -> None:
        extracted_video_id = _extract_video_id(video_id_or_url)
        if extracted_video_id is None:
            self.errorOccurred.emit("Could not parse YouTube video id from input")
            return

        self._video_id = extracted_video_id
        self._duration_seconds = None
        self._has_active_video = True

        start_seconds_value = max(0.0, float(start_seconds))
        autoplay_flag = "true" if bool(autoplay) else "false"

        js = (
            "window.steppyLoadVideo("
            + repr(extracted_video_id)
            + ", "
            + repr(start_seconds_value)
            + ", "
            + autoplay_flag
            + ");"
        )
        self._run_js_or_queue(js)

        # Ensure we start polling after a load request, even if autoplay is False.
        self._poll_timer.start()
        self._poll_snapshot()

    def play(self) -> None:
        self._run_js_or_queue("window.steppyPlay();")
        self._poll_timer.start()

    def pause(self) -> None:
        self._run_js_or_queue("window.steppyPause();")
        self._poll_snapshot()

    def seek(self, seconds: float) -> None:
        seconds_value = max(0.0, float(seconds))
        self._run_js_or_queue("window.steppySeek(" + repr(seconds_value) + ");")
        self._poll_snapshot()

    def set_muted(self, is_muted: bool) -> None:
        muted_flag = "true" if bool(is_muted) else "false"
        self._run_js_or_queue("window.steppySetMuted(" + muted_flag + ");")

    def set_volume(self, volume_percent: int) -> None:
        volume_value = int(max(0, min(100, int(volume_percent))))
        self._run_js_or_queue("window.steppySetVolume(" + repr(volume_value) + ");")

    def _load_bootstrap_html(self) -> None:
        # This HTML keeps all protocol details internal. Python only calls the
        # window.steppy* functions and polls window.steppyGetSnapshot().
        html = r"""
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
html, body { margin:0; padding:0; width:100%; height:100%; background:#111; overflow:hidden; }
#player { width:100%; height:100%; }
</style>
</head>
<body>
<div id="player"></div>
<script src="https://www.youtube.com/iframe_api"></script>
<script>
let steppyPlayer = null;
let steppyReady = false;

function _ensurePlayer(videoId, startSeconds, autoplay) {
  if (steppyPlayer !== null) {
    try {
      steppyPlayer.loadVideoById({videoId: videoId, startSeconds: startSeconds});
      if (!autoplay) { steppyPlayer.pauseVideo(); }
    } catch (e) {}
    return;
  }
  try {
    steppyPlayer = new YT.Player('player', {
      width: '100%',
      height: '100%',
      videoId: videoId,
      playerVars: {
        playsinline: 1,
        autoplay: autoplay ? 1 : 0,
        controls: 1,
        start: Math.floor(startSeconds)
      },
      events: {
        'onReady': function(event) {
          steppyReady = true;
        },
        'onStateChange': function(event) {
          // state is polled by Python, so no direct bridge here.
        }
      }
    });
  } catch (e) {
    // Leave steppyReady false. Python will surface a ready false snapshot.
  }
}

window.steppyLoadVideo = function(videoId, startSeconds, autoplay) {
  _ensurePlayer(videoId, startSeconds, autoplay);
};

window.steppyPlay = function() {
  if (steppyPlayer) { try { steppyPlayer.playVideo(); } catch (e) {} }
};

window.steppyPause = function() {
  if (steppyPlayer) { try { steppyPlayer.pauseVideo(); } catch (e) {} }
};

window.steppySeek = function(seconds) {
  if (steppyPlayer) { try { steppyPlayer.seekTo(seconds, true); } catch (e) {} }
};

window.steppySetMuted = function(isMuted) {
  if (!steppyPlayer) { return; }
  try {
    if (isMuted) { steppyPlayer.mute(); } else { steppyPlayer.unMute(); }
  } catch (e) {}
};

window.steppySetVolume = function(volumePercent) {
  if (!steppyPlayer) { return; }
  try { steppyPlayer.setVolume(volumePercent); } catch (e) {}
};

window.steppyGetSnapshot = function() {
  let stateCode = -2;
  let currentTime = 0.0;
  let duration = null;
  try {
    if (steppyPlayer) {
      stateCode = steppyPlayer.getPlayerState();
      currentTime = steppyPlayer.getCurrentTime();
      duration = steppyPlayer.getDuration();
    }
  } catch (e) {}
  if (typeof(duration) !== 'number' || !(duration > 0)) {
    duration = null;
  }
  return {
    ready: !!steppyReady,
    state_code: stateCode,
    time_seconds: currentTime,
    duration_seconds: duration
  };
};
</script>
</body>
</html>
"""
        base_url = QUrl("https://www.youtube.com")
        try:
            self._view.setHtml(html, base_url)
        except Exception as exc:
            self.errorOccurred.emit(f"Failed to initialize web player: {exc!r}")

    def _on_load_finished(self, ok: bool) -> None:
        self._html_loaded = bool(ok)
        if not ok:
            self.errorOccurred.emit("Web player failed to load")
            return

        pending_calls = list(self._pending_js_calls)
        self._pending_js_calls.clear()
        for js in pending_calls:
            self._view.page().runJavaScript(js)

        self._poll_timer.start()
        self._poll_snapshot()

    def _run_js_or_queue(self, js: str) -> None:
        if not self._html_loaded:
            self._pending_js_calls.append(str(js))
            return
        try:
            self._view.page().runJavaScript(str(js))
        except Exception as exc:
            self.errorOccurred.emit(f"Web player JS call failed: {exc!r}")

    def _poll_snapshot(self) -> None:
        if not self._html_loaded:
            return
        if not self._has_active_video:
            return

        try:
            self._view.page().runJavaScript("window.steppyGetSnapshot();", self._on_snapshot_result)
        except Exception as exc:
            self.errorOccurred.emit(f"Web player snapshot poll failed: {exc!r}")

    def _on_snapshot_result(self, result: Any) -> None:
        if not isinstance(result, dict):
            return

        ready_value = bool(result.get("ready", False))
        state_code_value = int(result.get("state_code", -2) or -2)
        time_seconds_value = float(result.get("time_seconds", 0.0) or 0.0)
        duration_value_raw = result.get("duration_seconds", None)

        duration_seconds_value: Optional[float]
        if duration_value_raw is None:
            duration_seconds_value = None
        else:
            try:
                duration_seconds_value = float(duration_value_raw)
                if not (duration_seconds_value > 0.0):
                    duration_seconds_value = None
            except Exception:
                duration_seconds_value = None

        if self._last_emitted_ready is None or ready_value != bool(self._last_emitted_ready):
            self._last_emitted_ready = bool(ready_value)
            self.playerReadyChanged.emit(bool(ready_value))

        if self._last_emitted_time_seconds is None or abs(time_seconds_value - float(self._last_emitted_time_seconds)) > 1e-6:
            self._last_emitted_time_seconds = float(time_seconds_value)
            self.timeUpdated.emit(float(time_seconds_value))

        state_changed = self._last_emitted_state_code is None or int(state_code_value) != int(self._last_emitted_state_code)
        duration_changed = False
        if duration_seconds_value is not None:
            if self._duration_seconds is None or abs(duration_seconds_value - float(self._duration_seconds)) > 1e-6:
                duration_changed = True
                self._duration_seconds = float(duration_seconds_value)

        if state_changed or duration_changed:
            self._last_emitted_state_code = int(state_code_value)
            state_name = _state_name_for_youtube_state_code(state_code_value)
            is_ended = (int(state_code_value) == 0)
            state_info = PlayerStateInfo(
                state_code=int(state_code_value),
                state_name=state_name,
                player_time_seconds=float(time_seconds_value),
                duration_seconds=self._duration_seconds,
                video_id=self._video_id,
                is_ended=bool(is_ended),
            )
            self.stateChanged.emit(state_info)


class WebPlayerBridge(QFrame):
    """Qt widget that exposes a stable player control API over a YouTube player."""

    timeUpdated = pyqtSignal(float)
    stateChanged = pyqtSignal(object)
    playerReadyChanged = pyqtSignal(bool)
    errorOccurred = pyqtSignal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent=parent)

        self._web_view: Optional["QWebEngineView"] = None
        self._web_backend: Optional[_WebEngineBackend] = None
        self._mock_backend = _MockBackend()

        self._active_backend_name = "mock"

        # Display widgets:
        self._mock_label = QLabel("WebPlayerBridge backend: mock")
        self._mock_label.setWordWrap(True)
        self._mock_label.setFont(QFont("Consolas", 10))
        self._mock_label.setMinimumHeight(80)

        self._stack_layout = QStackedLayout()
        self._mock_widget = QWidget()
        mock_layout = QVBoxLayout()
        mock_layout.setContentsMargins(10, 10, 10, 10)
        mock_layout.addWidget(self._mock_label)
        mock_layout.addStretch(1)
        self._mock_widget.setLayout(mock_layout)
        self._stack_layout.addWidget(self._mock_widget)

        if self._should_enable_webengine():
            self._web_view = QWebEngineView()
            self._web_backend = _WebEngineBackend(self._web_view)
            self._stack_layout.addWidget(self._web_view)

        root_layout = QVBoxLayout()
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.addLayout(self._stack_layout)
        self.setLayout(root_layout)

        self._connect_backend_signals(self._mock_backend)
        if self._web_backend is not None:
            self._connect_backend_signals(self._web_backend)

        self._set_active_backend("mock")

    def _should_enable_webengine(self) -> bool:
        # WebEngine is always preferred when available.
        # The environment is no longer used to force a mock backend.
        return True

    def _connect_backend_signals(self, backend: QObject) -> None:
        backend.timeUpdated.connect(self.timeUpdated)  # type: ignore[attr-defined]
        backend.stateChanged.connect(self.stateChanged)  # type: ignore[attr-defined]
        backend.playerReadyChanged.connect(self.playerReadyChanged)  # type: ignore[attr-defined]
        backend.errorOccurred.connect(self.errorOccurred)  # type: ignore[attr-defined]

    def backend_name(self) -> str:
        return str(self._active_backend_name)

    def load_video(self, *, video_id_or_url: str, start_seconds: float = 0.0, autoplay: bool = True) -> None:
        requested_text = str(video_id_or_url or "").strip()
        if not requested_text:
            self.errorOccurred.emit("Missing video id")
            return

        normalized_text = requested_text.lower()

        # Special test path now uses a real YouTube id so harness runs exercise the real backend.
        if normalized_text == "test":
            effective_video_id = "dQw4w9WgXcQ"
        else:
            extracted_video_id = _extract_video_id(requested_text)
            if extracted_video_id is None:
                # If the id cannot be parsed, fall back to the lightweight timer backend
                # so chart timing can still be exercised.
                self._set_active_backend("mock")
                self._mock_backend.load_video(
                    video_id_or_url=requested_text,
                    start_seconds=start_seconds,
                    autoplay=autoplay,
                )
                return
            effective_video_id = extracted_video_id

        if self._web_backend is not None and self._should_enable_webengine():
            self._set_active_backend("webengine")
            self._web_backend.load_video(
                video_id_or_url=effective_video_id,
                start_seconds=start_seconds,
                autoplay=autoplay,
            )
            return

        # Last resort: if the web backend is not available, keep using the timer backend.
        self._set_active_backend("mock")
        self._mock_backend.load_video(
            video_id_or_url=requested_text,
            start_seconds=start_seconds,
            autoplay=autoplay,
        )

    def play(self) -> None:
        if self._active_backend_name == "webengine" and self._web_backend is not None:
            self._web_backend.play()
            return
        self._mock_backend.play()

    def pause(self) -> None:
        if self._active_backend_name == "webengine" and self._web_backend is not None:
            self._web_backend.pause()
            return
        self._mock_backend.pause()

    def seek(self, seconds: float) -> None:
        if self._active_backend_name == "webengine" and self._web_backend is not None:
            self._web_backend.seek(seconds)
            return
        self._mock_backend.seek(seconds)

    def set_muted(self, is_muted: bool) -> None:
        if self._active_backend_name == "webengine" and self._web_backend is not None:
            self._web_backend.set_muted(is_muted)
            return
        self._mock_backend.set_muted(is_muted)

    def set_volume(self, volume_percent: int) -> None:
        if self._active_backend_name == "webengine" and self._web_backend is not None:
            self._web_backend.set_volume(volume_percent)
            return
        self._mock_backend.set_volume(volume_percent)

    def _set_active_backend(self, backend_name: str) -> None:
        backend_name_text = str(backend_name or "").strip().lower()
        if backend_name_text == "webengine" and self._web_backend is not None:
            self._active_backend_name = "webengine"
            self._stack_layout.setCurrentIndex(1)
            return

        self._active_backend_name = "mock"
        self._stack_layout.setCurrentIndex(0)


def main() -> int:
    import sys
    from PyQt6.QtWidgets import QApplication

    app = QApplication(sys.argv)

    widget = WebPlayerBridge()
    widget.resize(960, 540)
    widget.show()

    # Simple smoke behavior: start with the special test id.
    # This now maps to the real YouTube id dQw4w9WgXcQ.
    widget.load_video(video_id_or_url="test", autoplay=False)

    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
