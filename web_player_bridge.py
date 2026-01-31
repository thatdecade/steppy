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
#     - playbackEnded(PlayerStateInfo)
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
from typing import Any, Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal, QUrl
from PyQt6.QtWebEngineCore import QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QFrame, QLabel, QStackedLayout, QVBoxLayout, QWidget


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


def _friendly_error_for_iframe_api_code(error_code: int) -> str:
    # YouTube IFrame API error codes:
    # 2 invalid parameter value
    # 5 HTML5 player error
    # 100 video not found / removed
    # 101 and 150 embedding not allowed
    mapping = {
        2: "Invalid video id or parameter",
        5: "HTML5 player error",
        100: "Video not found or removed",
        101: "Embedding disabled for this video",
        150: "Embedding disabled for this video",
    }
    return mapping.get(int(error_code), f"YouTube player error code {int(error_code)}")


@dataclass(frozen=True)
class PlayerStateInfo:
    state_code: int
    state_name: str
    player_time_seconds: float
    duration_seconds: Optional[float]
    video_id: Optional[str]
    is_ended: bool


class _WebEngineBackend(QObject):
    timeUpdated = pyqtSignal(float)
    stateChanged = pyqtSignal(object)
    playerReadyChanged = pyqtSignal(bool)
    errorOccurred = pyqtSignal(str)
    playbackEnded = pyqtSignal(object)

    def __init__(self, view: QWebEngineView) -> None:
        super().__init__()
        self._view = view

        self._html_loaded = False
        self._has_pending_video_request = False

        self._video_id: Optional[str] = None
        self._duration_seconds: Optional[float] = None
        self._last_emitted_time_seconds: Optional[float] = None
        self._last_emitted_state_code: Optional[int] = None
        self._last_emitted_ready: Optional[bool] = None
        self._last_emitted_error_code: Optional[int] = None

        self._poll_timer = QTimer()
        self._poll_timer.setInterval(50)
        self._poll_timer.timeout.connect(self._poll_snapshot)

        self._pending_js_calls: list[str] = []

        self._view.loadFinished.connect(self._on_load_finished)

        self._configure_view_settings()
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
        self._has_pending_video_request = True

        start_seconds_value = max(0.0, float(start_seconds))
        autoplay_flag = "true" if bool(autoplay) else "false"

        js = (
            "window.steppyRequestLoadVideo("
            + repr(extracted_video_id)
            + ", "
            + repr(start_seconds_value)
            + ", "
            + autoplay_flag
            + ");"
        )
        self._run_js_or_queue(js)

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

    def _configure_view_settings(self) -> None:
        settings = self._view.settings()

        # Match the old stable harness behavior.
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True)

    def _load_bootstrap_html(self) -> None:
        # setHtml() uses base_url as the document URL.
        # Keep the origin on localhost to avoid youtube.com origin edge cases.
        base_url = QUrl("http://localhost/")

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
let steppyApiReady = false;
let steppyReady = false;
let steppyPendingLoad = null;
let steppyLastErrorCode = null;

function _createPlayerIfNeeded(initialVideoId) {
  if (!steppyApiReady) { return; }
  if (steppyPlayer !== null) { return; }

  try {
    steppyPlayer = new YT.Player('player', {
      width: '100%',
      height: '100%',
      videoId: initialVideoId,
      playerVars: {
        playsinline: 1,
        autoplay: 0,
        controls: 1,
        rel: 0,
        origin: window.location.origin
      },
      events: {
        'onReady': function(event) {
          steppyReady = true;
          _applyPendingLoadIfAny();
        },
        'onError': function(event) {
          try { steppyLastErrorCode = event.data; } catch (e) { steppyLastErrorCode = null; }
        },
        'onStateChange': function(event) {
          // State is polled by Python.
        }
      }
    });
  } catch (e) {
    steppyPlayer = null;
  }
}

function _applyPendingLoadIfAny() {
  if (!steppyApiReady) { return; }
  if (!steppyPendingLoad) { return; }

  const request = steppyPendingLoad;
  steppyPendingLoad = null;

  _createPlayerIfNeeded(request.videoId);
  if (!steppyPlayer) { return; }

  try {
    steppyPlayer.loadVideoById({videoId: request.videoId, startSeconds: request.startSeconds});
    if (!request.autoplay) { steppyPlayer.pauseVideo(); }
  } catch (e) {}
}

window.onYouTubeIframeAPIReady = function() {
  steppyApiReady = true;
  if (steppyPendingLoad) {
    _applyPendingLoadIfAny();
  } else {
    _createPlayerIfNeeded('dQw4w9WgXcQ');
    try { if (steppyPlayer) { steppyPlayer.pauseVideo(); } } catch (e) {}
  }
};

window.steppyRequestLoadVideo = function(videoId, startSeconds, autoplay) {
  steppyPendingLoad = {
    videoId: String(videoId || ''),
    startSeconds: Number(startSeconds || 0),
    autoplay: !!autoplay
  };
  _applyPendingLoadIfAny();
};

window.steppyPlay = function() {
  if (steppyPlayer) { try { steppyPlayer.playVideo(); } catch (e) {} }
};

window.steppyPause = function() {
  if (steppyPlayer) { try { steppyPlayer.pauseVideo(); } catch (e) {} }
};

window.steppySeek = function(seconds) {
  if (steppyPlayer) { try { steppyPlayer.seekTo(Number(seconds || 0), true); } catch (e) {} }
};

window.steppySetMuted = function(isMuted) {
  if (!steppyPlayer) { return; }
  try {
    if (isMuted) { steppyPlayer.mute(); } else { steppyPlayer.unMute(); }
  } catch (e) {}
};

window.steppySetVolume = function(volumePercent) {
  if (!steppyPlayer) { return; }
  try { steppyPlayer.setVolume(Number(volumePercent || 0)); } catch (e) {}
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

  let errorCode = null;
  try {
    if (typeof(steppyLastErrorCode) === 'number') {
      errorCode = steppyLastErrorCode;
    }
  } catch (e) {}

  return {
    api_ready: !!steppyApiReady,
    ready: !!steppyReady,
    state_code: stateCode,
    time_seconds: currentTime,
    duration_seconds: duration,
    error_code: errorCode
  };
};
</script>
</body>
</html>
"""
        try:
            self._view.setHtml(html, base_url)
        except Exception as exc:
            self.errorOccurred.emit(f"Failed to initialize web player HTML: {exc!r}")

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
        if not self._has_pending_video_request:
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

        error_code_raw = result.get("error_code", None)
        error_code_value: Optional[int]
        if error_code_raw is None:
            error_code_value = None
        else:
            try:
                error_code_value = int(error_code_raw)
            except Exception:
                error_code_value = None

        if error_code_value is not None:
            if self._last_emitted_error_code is None or int(error_code_value) != int(self._last_emitted_error_code):
                self._last_emitted_error_code = int(error_code_value)
                self.errorOccurred.emit(_friendly_error_for_iframe_api_code(int(error_code_value)))

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

            if is_ended:
                self.playbackEnded.emit(state_info)


class WebPlayerBridge(QFrame):
    timeUpdated = pyqtSignal(float)
    stateChanged = pyqtSignal(object)
    playerReadyChanged = pyqtSignal(bool)
    errorOccurred = pyqtSignal(str)
    playbackEnded = pyqtSignal(object)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent=parent)

        self._web_view: Optional[QWebEngineView] = None
        self._web_backend: Optional[_WebEngineBackend] = None
        self._active_backend_name = "webengine"

        self._status_label = QLabel("WebPlayerBridge: initializing")
        self._status_label.setWordWrap(True)

        self._stack_layout = QStackedLayout()
        self._status_widget = QWidget()
        status_layout = QVBoxLayout()
        status_layout.setContentsMargins(10, 10, 10, 10)
        status_layout.addWidget(self._status_label)
        status_layout.addStretch(1)
        self._status_widget.setLayout(status_layout)
        self._stack_layout.addWidget(self._status_widget)

        try:
            self._web_view = QWebEngineView()
            self._web_backend = _WebEngineBackend(self._web_view)
            self._stack_layout.addWidget(self._web_view)

            self._web_backend.timeUpdated.connect(self.timeUpdated)
            self._web_backend.stateChanged.connect(self.stateChanged)
            self._web_backend.playerReadyChanged.connect(self.playerReadyChanged)
            self._web_backend.errorOccurred.connect(self.errorOccurred)
            self._web_backend.errorOccurred.connect(self._on_backend_error)

            # New: forward playbackEnded from backend to public signal.
            self._web_backend.playbackEnded.connect(self.playbackEnded)

            self._stack_layout.setCurrentIndex(1)
            self._status_label.setText("WebPlayerBridge backend: webengine")
        except Exception as exc:
            self._active_backend_name = "unavailable"
            self._stack_layout.setCurrentIndex(0)
            self._status_label.setText(f"WebPlayerBridge failed to initialize: {exc!r}")

        root_layout = QVBoxLayout()
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.addLayout(self._stack_layout)
        self.setLayout(root_layout)

    def backend_name(self) -> str:
        return str(self._active_backend_name)

    def load_video(self, *, video_id_or_url: str, start_seconds: float = 0.0, autoplay: bool = True) -> None:
        if self._web_backend is None:
            self.errorOccurred.emit("Web player backend is unavailable")
            return

        requested_text = str(video_id_or_url or "").strip()
        if not requested_text:
            self.errorOccurred.emit("Missing video id")
            return

        if requested_text.strip().lower() == "test":
            requested_text = "dQw4w9WgXcQ"

        self._status_label.setText("WebPlayerBridge backend: webengine")
        self._stack_layout.setCurrentIndex(1)
        self._web_backend.load_video(
            video_id_or_url=requested_text,
            start_seconds=float(start_seconds),
            autoplay=bool(autoplay),
        )

    def play(self) -> None:
        if self._web_backend is None:
            return
        self._web_backend.play()

    def pause(self) -> None:
        if self._web_backend is None:
            return
        self._web_backend.pause()

    def seek(self, seconds: float) -> None:
        if self._web_backend is None:
            return
        self._web_backend.seek(float(seconds))

    def set_muted(self, is_muted: bool) -> None:
        if self._web_backend is None:
            return
        self._web_backend.set_muted(bool(is_muted))

    def set_volume(self, volume_percent: int) -> None:
        if self._web_backend is None:
            return
        self._web_backend.set_volume(int(volume_percent))

    def _on_backend_error(self, message: str) -> None:
        self._status_label.setText(f"WebPlayerBridge error: {message}")


def main() -> int:
    import sys
    from PyQt6.QtCore import QCoreApplication, Qt
    from PyQt6.QtWidgets import QApplication

    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

    app = QApplication(sys.argv)

    widget = WebPlayerBridge()
    widget.resize(960, 540)
    widget.show()

    widget.load_video(video_id_or_url="test", autoplay=False)

    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
