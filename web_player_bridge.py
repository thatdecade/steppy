"""
web_player_bridge.py

Bridge between Python (PyQt6) and a YouTube IFrame player hosted inside QWebEngine.

Responsibilities:
- Load a local HTML page that hosts the YouTube IFrame player
- Provide playback control (load, play, pause, seek, mute)
- Provide player time polling (player_time_seconds) and state events
- Use QWebChannel for JavaScript -> Python callbacks

"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qs, urlparse

from PyQt6.QtCore import QObject, QTimer, QUrl, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineCore import QWebEngineCertificateError, QWebEnginePage, QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView


_DEFAULT_HTML = """<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta
      name="viewport"
      content="width=device-width, initial-scale=1, maximum-scale=1"
    />
    <title>Steppy Web Player</title>
    <style>
      html, body {
        margin: 0;
        padding: 0;
        background: #000;
        height: 100%;
        overflow: hidden;
      }
      #player {
        width: 100%;
        height: 100%;
      }
    </style>

    <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
    <script src="https://www.youtube.com/iframe_api"></script>

    <script>
      "use strict";

      let qtBridge = null;
      let youTubePlayer = null;
      let youTubePlayerReady = false;

      function safeCallPython(methodName, argsArray) {
        try {
          if (!qtBridge) return;
          const method = qtBridge[methodName];
          if (typeof method !== "function") return;
          method.apply(qtBridge, argsArray || []);
        } catch (error) {
          // Intentionally ignore to keep playback stable.
        }
      }

      function mapPlayerState(stateCode) {
        // Mirror YouTube IFrame API numeric codes.
        // -1 unstarted, 0 ended, 1 playing, 2 paused, 3 buffering, 5 cued
        return stateCode;
      }

      function onYouTubeIframeAPIReady() {
        youTubePlayer = new YT.Player("player", {
          width: "100%",
          height: "100%",
          videoId: "",
          playerVars: {
            autoplay: 0,
            controls: 0,
            disablekb: 1,
            fs: 0,
            modestbranding: 1,
            rel: 0,
            playsinline: 1
          },
          events: {
            onReady: function () {
              youTubePlayerReady = true;
              safeCallPython("notifyPlayerReady", []);
            },
            onStateChange: function (event) {
              const stateCode = mapPlayerState(event.data);
              let currentTimeSeconds = 0.0;
              try {
                currentTimeSeconds = youTubePlayer.getCurrentTime() || 0.0;
              } catch (error) {
                currentTimeSeconds = 0.0;
              }
              safeCallPython("notifyPlayerState", [stateCode, currentTimeSeconds]);
            },
            onError: function (event) {
              safeCallPython("notifyPlayerError", [event.data]);
            }
          }
        });
      }

      window.steppyPlayer = {
        isReady: function () {
          return !!youTubePlayerReady;
        },

        loadVideo: function (videoId, startSeconds) {
          if (!youTubePlayerReady || !youTubePlayer) return;
          const startTime = (typeof startSeconds === "number") ? startSeconds : 0.0;
          youTubePlayer.loadVideoById({ videoId: videoId, startSeconds: startTime });
        },

        cueVideo: function (videoId, startSeconds) {
          if (!youTubePlayerReady || !youTubePlayer) return;
          const startTime = (typeof startSeconds === "number") ? startSeconds : 0.0;
          youTubePlayer.cueVideoById({ videoId: videoId, startSeconds: startTime });
        },

        play: function () {
          if (!youTubePlayerReady || !youTubePlayer) return;
          youTubePlayer.playVideo();
        },

        pause: function () {
          if (!youTubePlayerReady || !youTubePlayer) return;
          youTubePlayer.pauseVideo();
        },

        seek: function (timeSeconds, allowSeekAhead) {
          if (!youTubePlayerReady || !youTubePlayer) return;
          const allowAhead = (allowSeekAhead !== false);
          youTubePlayer.seekTo(timeSeconds, allowAhead);
        },

        mute: function () {
          if (!youTubePlayerReady || !youTubePlayer) return;
          youTubePlayer.mute();
        },

        unmute: function () {
          if (!youTubePlayerReady || !youTubePlayer) return;
          youTubePlayer.unMute();
        },

        setVolume: function (volumePercent) {
          if (!youTubePlayerReady || !youTubePlayer) return;
          youTubePlayer.setVolume(volumePercent);
        },

        getCurrentTime: function () {
          if (!youTubePlayerReady || !youTubePlayer) return null;
          try {
            return youTubePlayer.getCurrentTime();
          } catch (error) {
            return null;
          }
        }
      };

      function setupQtChannel() {
        if (typeof QWebChannel === "undefined") {
          return;
        }
        new QWebChannel(qt.webChannelTransport, function (channel) {
          qtBridge = channel.objects.qtBridge;
          safeCallPython("notifyQtChannelReady", []);
        });
      }

      document.addEventListener("DOMContentLoaded", setupQtChannel);
    </script>
  </head>

  <body>
    <div id="player"></div>
  </body>
</html>
"""


@dataclass(frozen=True)
class PlayerStateInfo:
    code: int
    name: str


def _player_state_from_code(state_code: int) -> PlayerStateInfo:
    mapping = {
        -1: "unstarted",
        0: "ended",
        1: "playing",
        2: "paused",
        3: "buffering",
        5: "cued",
    }
    return PlayerStateInfo(code=state_code, name=mapping.get(state_code, f"unknown({state_code})"))


def extract_youtube_video_id(user_text: str) -> Optional[str]:
    trimmed_text = (user_text or "").strip()
    if not trimmed_text:
        return None

    # If it looks like a bare id, accept it.
    if "://" not in trimmed_text and "youtube" not in trimmed_text and "youtu.be" not in trimmed_text:
        return trimmed_text

    parsed_url = urlparse(trimmed_text)
    host = (parsed_url.netloc or "").lower()
    path = parsed_url.path or ""

    # youtu.be/<id>
    if host.endswith("youtu.be"):
        candidate = path.lstrip("/").split("/")[0].strip()
        return candidate or None

    # youtube.com/watch?v=<id>
    if "youtube.com" in host:
        if path.startswith("/watch"):
            query_values = parse_qs(parsed_url.query or "")
            candidate_list = query_values.get("v") or []
            candidate = (candidate_list[0] if candidate_list else "").strip()
            return candidate or None

        # youtube.com/shorts/<id>
        if path.startswith("/shorts/"):
            candidate = path.split("/shorts/", 1)[1].split("/", 1)[0].strip()
            return candidate or None

        # youtube.com/embed/<id>
        if path.startswith("/embed/"):
            candidate = path.split("/embed/", 1)[1].split("/", 1)[0].strip()
            return candidate or None

    return None


class _QtChannelBridge(QObject):
    qtChannelReady = pyqtSignal()
    playerReady = pyqtSignal()
    playerState = pyqtSignal(int, float)
    playerError = pyqtSignal(int)

    @pyqtSlot()
    def notifyQtChannelReady(self) -> None:
        self.qtChannelReady.emit()

    @pyqtSlot()
    def notifyPlayerReady(self) -> None:
        self.playerReady.emit()

    @pyqtSlot(int, float)
    def notifyPlayerState(self, state_code: int, current_time_seconds: float) -> None:
        self.playerState.emit(int(state_code), float(current_time_seconds))

    @pyqtSlot(int)
    def notifyPlayerError(self, error_code: int) -> None:
        self.playerError.emit(int(error_code))


class SteppyWebEnginePage(QWebEnginePage):
    certificateErrorText = pyqtSignal(str)

    def certificateError(self, certificate_error: QWebEngineCertificateError) -> bool:
        try:
            url_text = certificate_error.url().toString()
        except Exception:
            url_text = "(unknown url)"

        try:
            description_text = certificate_error.errorDescription()
        except Exception:
            description_text = "(no description)"

        try:
            overridable_text = "yes" if certificate_error.isOverridable() else "no"
        except Exception:
            overridable_text = "(unknown)"

        message = (
            "TLS certificate error while loading "
            + url_text
            + ": "
            + description_text
            + " (overridable: "
            + overridable_text
            + ")"
        )
        self.certificateErrorText.emit(message)

        # Do not ignore certificate errors.
        return False


class WebPlayerBridge(QWidget):
    timeUpdated = pyqtSignal(float)
    stateChanged = pyqtSignal(object)  # PlayerStateInfo
    playerReadyChanged = pyqtSignal(bool)
    errorOccurred = pyqtSignal(str)

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        html_content: Optional[str] = None,
        base_url: Optional[QUrl] = None,
        time_poll_interval_msec: int = 50,
    ) -> None:
        super().__init__(parent)

        self._web_view = QWebEngineView(self)
        self._web_view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)

        self._web_page = SteppyWebEnginePage(self._web_view)
        self._web_view.setPage(self._web_page)

        self._web_view.settings().setAttribute(
            QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False
        )

        self._channel_bridge_object = _QtChannelBridge()
        self._web_channel = QWebChannel(self._web_view.page())
        self._web_channel.registerObject("qtBridge", self._channel_bridge_object)
        self._web_view.page().setWebChannel(self._web_channel)

        self._player_ready = False
        self._last_player_state: Optional[PlayerStateInfo] = None
        self._latest_player_time_seconds: float = 0.0

        self._pending_video_id: Optional[str] = None
        self._pending_start_seconds: float = 0.0
        self._pending_autoplay: bool = True

        self._time_poll_timer = QTimer(self)
        self._time_poll_timer.setInterval(int(time_poll_interval_msec))
        self._time_poll_timer.timeout.connect(self._poll_player_time)

        self._web_page.certificateErrorText.connect(self.errorOccurred.emit)

        self._channel_bridge_object.playerReady.connect(self._on_player_ready)
        self._channel_bridge_object.playerState.connect(self._on_player_state)
        self._channel_bridge_object.playerError.connect(self._on_player_error)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._web_view)
        self.setLayout(layout)

        html_to_load = html_content if html_content is not None else _DEFAULT_HTML
        effective_base_url = base_url if base_url is not None else QUrl("http://localhost/")
        self._web_view.setHtml(html_to_load, effective_base_url)

    @property
    def player_time_seconds(self) -> float:
        return float(self._latest_player_time_seconds)

    @property
    def is_player_ready(self) -> bool:
        return bool(self._player_ready)

    def load_video(self, video_id: str, start_seconds: float = 0.0, *, autoplay: bool = True) -> None:
        if not video_id:
            return

        self._pending_video_id = video_id
        self._pending_start_seconds = float(start_seconds)
        self._pending_autoplay = bool(autoplay)

        if self._player_ready:
            self._apply_pending_load()

    def play(self) -> None:
        self._run_player_javascript(
            "window.steppyPlayer && window.steppyPlayer.play && window.steppyPlayer.play();"
        )

    def pause(self) -> None:
        self._run_player_javascript(
            "window.steppyPlayer && window.steppyPlayer.pause && window.steppyPlayer.pause();"
        )

    def seek(self, time_seconds: float, *, allow_seek_ahead: bool = True) -> None:
        safe_time_seconds = float(max(0.0, time_seconds))
        allow_seek_ahead_literal = "true" if allow_seek_ahead else "false"
        javascript = (
            "window.steppyPlayer && window.steppyPlayer.seek && "
            f"window.steppyPlayer.seek({safe_time_seconds}, {allow_seek_ahead_literal});"
        )
        self._run_player_javascript(javascript)

    def set_muted(self, muted: bool) -> None:
        if muted:
            self._run_player_javascript(
                "window.steppyPlayer && window.steppyPlayer.mute && window.steppyPlayer.mute();"
            )
        else:
            self._run_player_javascript(
                "window.steppyPlayer && window.steppyPlayer.unmute && window.steppyPlayer.unmute();"
            )

    def set_volume(self, volume_percent: int) -> None:
        safe_volume = int(max(0, min(100, int(volume_percent))))
        self._run_player_javascript(
            "window.steppyPlayer && window.steppyPlayer.setVolume && "
            f"window.steppyPlayer.setVolume({safe_volume});"
        )

    def _run_player_javascript(self, javascript: str) -> None:
        try:
            self._web_view.page().runJavaScript(javascript)
        except Exception as exception:
            self.errorOccurred.emit(f"runJavaScript failed: {exception}")

    def _apply_pending_load(self) -> None:
        if not self._pending_video_id:
            return

        video_id_json = json.dumps(self._pending_video_id)
        start_seconds = float(self._pending_start_seconds)

        if self._pending_autoplay:
            javascript = (
                "window.steppyPlayer && window.steppyPlayer.loadVideo && "
                f"window.steppyPlayer.loadVideo({video_id_json}, {start_seconds});"
            )
        else:
            javascript = (
                "window.steppyPlayer && window.steppyPlayer.cueVideo && "
                f"window.steppyPlayer.cueVideo({video_id_json}, {start_seconds});"
            )

        self._run_player_javascript(javascript)

    def _on_player_ready(self) -> None:
        self._player_ready = True
        self.playerReadyChanged.emit(True)

        if not self._time_poll_timer.isActive():
            self._time_poll_timer.start()

        self._apply_pending_load()

    def _on_player_state(self, state_code: int, current_time_seconds: float) -> None:
        self._latest_player_time_seconds = float(current_time_seconds)
        self.timeUpdated.emit(self._latest_player_time_seconds)

        state_info = _player_state_from_code(int(state_code))
        if self._last_player_state is None or self._last_player_state.code != state_info.code:
            self._last_player_state = state_info
            self.stateChanged.emit(state_info)

    def _on_player_error(self, error_code: int) -> None:
        # YouTube player error codes are not the same as network errors.
        self.errorOccurred.emit(f"YouTube player error code: {int(error_code)}")

    def _poll_player_time(self) -> None:
        if not self._player_ready:
            return

        javascript = (
            "window.steppyPlayer && window.steppyPlayer.getCurrentTime "
            "? window.steppyPlayer.getCurrentTime() : null;"
        )

        def on_time_value(value: object) -> None:
            if value is None:
                return
            try:
                time_seconds = float(value)
            except (TypeError, ValueError):
                return

            self._latest_player_time_seconds = time_seconds
            self.timeUpdated.emit(time_seconds)

        try:
            self._web_view.page().runJavaScript(javascript, on_time_value)
        except Exception as exception:
            self.errorOccurred.emit(f"time poll failed: {exception}")


class _DebugWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Steppy Web Player Bridge Debug")

        self._player_bridge = WebPlayerBridge(self)

        self._status_label = QLabel("status: (waiting)")
        self._time_label = QLabel("time: 0.00")

        self._video_input = QLineEdit()
        self._video_input.setPlaceholderText("YouTube video id or URL (example: dQw4w9WgXcQ or https://youtu.be/dQw4w9WgXcQ)")

        self._start_seconds_input = QSpinBox()
        self._start_seconds_input.setRange(0, 24 * 60 * 60)
        self._start_seconds_input.setValue(0)

        self._seek_seconds_input = QSpinBox()
        self._seek_seconds_input.setRange(0, 24 * 60 * 60)
        self._seek_seconds_input.setValue(30)

        load_button = QPushButton("Load")
        play_button = QPushButton("Play")
        pause_button = QPushButton("Pause")
        seek_button = QPushButton("Seek")
        mute_button = QPushButton("Mute")
        unmute_button = QPushButton("Unmute")

        load_button.clicked.connect(self._on_load_clicked)
        play_button.clicked.connect(self._player_bridge.play)
        pause_button.clicked.connect(self._player_bridge.pause)
        seek_button.clicked.connect(self._on_seek_clicked)
        mute_button.clicked.connect(lambda: self._player_bridge.set_muted(True))
        unmute_button.clicked.connect(lambda: self._player_bridge.set_muted(False))

        self._player_bridge.stateChanged.connect(self._on_state_changed)
        self._player_bridge.timeUpdated.connect(self._on_time_updated)
        self._player_bridge.errorOccurred.connect(self._on_error)

        controls_row = QHBoxLayout()
        controls_row.addWidget(QLabel("video:"))
        controls_row.addWidget(self._video_input, 1)
        controls_row.addWidget(QLabel("start:"))
        controls_row.addWidget(self._start_seconds_input)
        controls_row.addWidget(load_button)

        buttons_row = QHBoxLayout()
        buttons_row.addWidget(play_button)
        buttons_row.addWidget(pause_button)
        buttons_row.addWidget(QLabel("seek to:"))
        buttons_row.addWidget(self._seek_seconds_input)
        buttons_row.addWidget(seek_button)
        buttons_row.addStretch(1)
        buttons_row.addWidget(mute_button)
        buttons_row.addWidget(unmute_button)

        status_row = QHBoxLayout()
        status_row.addWidget(self._status_label, 1)
        status_row.addStretch(1)
        status_row.addWidget(self._time_label)

        layout = QVBoxLayout()
        layout.addLayout(controls_row)
        layout.addLayout(buttons_row)
        layout.addLayout(status_row)
        layout.addWidget(self._player_bridge, 1)
        self.setLayout(layout)

        self.resize(1100, 700)

    def _on_load_clicked(self) -> None:
        user_value = self._video_input.text().strip()
        extracted_video_id = extract_youtube_video_id(user_value)
        if not extracted_video_id:
            self._status_label.setText("status: could not extract a YouTube video id from input")
            return

        start_seconds = float(self._start_seconds_input.value())
        self._status_label.setText("status: loading video id " + extracted_video_id)
        self._player_bridge.load_video(extracted_video_id, start_seconds=start_seconds, autoplay=True)

    def _on_seek_clicked(self) -> None:
        seek_seconds = float(self._seek_seconds_input.value())
        self._player_bridge.seek(seek_seconds)

    def _on_state_changed(self, state_info: PlayerStateInfo) -> None:
        self._status_label.setText("status: player state = " + state_info.name)

    def _on_time_updated(self, time_seconds: float) -> None:
        self._time_label.setText(f"time: {time_seconds:.2f}")

    def _on_error(self, message: str) -> None:
        self._status_label.setText("status: " + message)


def main() -> int:
    application_arguments = sys.argv if sys.argv and sys.argv[0] else ["steppy"]
    application = QApplication(application_arguments)

    debug_window = _DebugWindow()
    debug_window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
