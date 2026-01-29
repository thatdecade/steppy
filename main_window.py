"""
main_window.py

Single window UI surface for idle, playback, and overlay.

Integration
- Loads UI from main_window_ui.py generated from main_window_ui.ui
- Hosts embedded web video player (WebPlayerBridge)
- Hosts an overlay layer above the player (placeholder for overlay_renderer.py)
- Hosts an idle overlay above everything (splash image + QR)
- Provides non-interactive fullscreen kiosk behavior (optional)

Public API
- show_idle(), hide_idle()
- set_idle_qr(image: QImage)
- load_video(video_id: str), play(), pause(), seek(seconds: float)

Standalone usage
python -m main_window

No command line arguments are required or used.
The standalone window starts in idle mode and toggles between idle and playback on a timer for layout testing.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Optional

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QImage, QKeyEvent, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QLabel,
    QMainWindow,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from main_window_ui import Ui_MainWindow
from web_player_bridge import WebPlayerBridge


_THEME_BACKGROUND = "#050313"
_THEME_PRIMARY = "#5F42AB"
_THEME_CYAN = "#ACE4FC"
_THEME_PINK = "#F48CE4"
_THEME_MUTED_MAGENTA = "#A4346C"
_THEME_OFFWHITE = "#F3F0FC"


@dataclass(frozen=True)
class _DemoState:
    is_idle: bool
    fake_time_seconds: float


class MainWindow(QMainWindow):
    """
    Main kiosk window for Steppy.

    This module owns the visual layering:
    - Base layer: WebPlayerBridge (YouTube playback)
    - Overlay layer: placeholder widget for overlay_renderer.py output
    - Top layer: idle overlay (splash image + QR card)

    The rest of the app should treat this as a narrow surface with simple commands.
    """

    def __init__(self, *, kiosk_mode: bool = False, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        self._ui = Ui_MainWindow()
        self._ui.setupUi(self)

        self._kiosk_mode = bool(kiosk_mode)
        self._apply_kiosk_settings(self._kiosk_mode)

        # Create a host widget with overlay-capable layout and move the splash frame into it.
        self._layers_host = QWidget(self._ui.centralwidget)
        self._layers_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        layers_layout = QGridLayout(self._layers_host)
        layers_layout.setContentsMargins(0, 0, 0, 0)
        layers_layout.setSpacing(0)

        # Remove splash frame from the UI root layout and re-parent into the overlay grid.
        if hasattr(self._ui, "rootLayout") and self._ui.rootLayout is not None:
            try:
                self._ui.rootLayout.removeWidget(self._ui.splashFrame)
            except Exception:
                pass

        self._ui.splashFrame.setParent(self._layers_host)

        # Base layer: web player.
        self._web_player = WebPlayerBridge(self._layers_host)
        self._web_player.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # Overlay layer: placeholder for overlay_renderer.py.
        self._overlay_layer = QWidget(self._layers_host)
        self._overlay_layer.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._overlay_layer.setStyleSheet("background: transparent;")
        self._overlay_layer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # Add in Z-order: player (bottom), overlay (middle), idle (top).
        layers_layout.addWidget(self._web_player, 0, 0)
        layers_layout.addWidget(self._overlay_layer, 0, 0)
        layers_layout.addWidget(self._ui.splashFrame, 0, 0)

        # Put the layers host back into the central layout.
        if hasattr(self._ui, "rootLayout") and self._ui.rootLayout is not None:
            self._ui.rootLayout.addWidget(self._layers_host)

        # Build idle overlay contents (QR card over the splash image).
        self._qr_label: QLabel
        self._url_hint_label: QLabel
        self._status_debug_label: QLabel
        self._build_idle_overlay()

        # Default: show idle.
        self.show_idle()

    # Public API

    def show_idle(self) -> None:
        self._ui.splashFrame.show()
        self._ui.splashFrame.raise_()

    def hide_idle(self) -> None:
        self._ui.splashFrame.hide()

    def set_idle_qr(self, image: QImage) -> None:
        if image.isNull():
            self._qr_label.clear()
            self._qr_label.setText("QR unavailable")
            return

        target_width = max(1, self._qr_label.width())
        target_height = max(1, self._qr_label.height())

        pixmap = QPixmap.fromImage(image)
        scaled = pixmap.scaled(
            target_width,
            target_height,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        self._qr_label.setPixmap(scaled)
        self._qr_label.setText("")

    def load_video(self, video_id: str) -> None:
        self._web_player.load_video(video_id, start_seconds=0.0, autoplay=True)

    def play(self) -> None:
        self._web_player.play()

    def pause(self) -> None:
        self._web_player.pause()

    def seek(self, seconds: float) -> None:
        self._web_player.seek(float(seconds))

    # Internals

    def _apply_kiosk_settings(self, enabled: bool) -> None:
        if enabled:
            self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
            self.setCursor(Qt.CursorShape.BlankCursor)
        else:
            self.setWindowFlag(Qt.WindowType.FramelessWindowHint, False)
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
            self.unsetCursor()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _build_idle_overlay(self) -> None:
        """
        Replaces the splash frame layout so we can overlay a QR "card" on top of the splash image.
        """
        splash_frame = self._ui.splashFrame
        splash_image = self._ui.splashImageLabel

        old_layout = splash_frame.layout()
        if old_layout is not None:
            while old_layout.count():
                item = old_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.setParent(None)
            old_layout.deleteLater()

        grid = QGridLayout(splash_frame)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)

        splash_image.setParent(splash_frame)
        splash_image.setScaledContents(True)
        splash_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(splash_image, 0, 0)

        # QR card anchored bottom-right.
        card = QFrame(splash_frame)
        card.setObjectName("qrCard")
        card.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        card.setStyleSheet(
            "QFrame#qrCard {"
            f"  background-color: {_THEME_OFFWHITE};"
            "  border-radius: 14px;"
            "}"
        )

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 18, 18, 18)
        card_layout.setSpacing(10)

        title = QLabel("Scan to control", card)
        title.setStyleSheet(f"color: {_THEME_PRIMARY}; font-size: 18px; font-weight: 700;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._qr_label = QLabel(card)
        self._qr_label.setFixedSize(300, 300)
        self._qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_label.setStyleSheet(
            "background: white; border: 1px solid rgba(0,0,0,0.12); border-radius: 10px;"
        )
        self._qr_label.setText("QR pending")

        self._url_hint_label = QLabel("http://<host>:<port>/", card)
        self._url_hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._url_hint_label.setStyleSheet("color: #050313; font-size: 12px;")

        accent = QLabel("Steppy", card)
        accent.setAlignment(Qt.AlignmentFlag.AlignCenter)
        accent.setStyleSheet(f"color: {_THEME_MUTED_MAGENTA}; font-size: 12px; font-weight: 600;")

        card_layout.addWidget(title)
        card_layout.addWidget(self._qr_label, 0, Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self._url_hint_label)
        card_layout.addWidget(accent)

        # Place card in bottom-right with margins.
        card_container = QWidget(splash_frame)
        card_container.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        container_layout = QVBoxLayout(card_container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.addWidget(card, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)

        container_layout.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
        grid.addWidget(card_container, 0, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
        grid.setContentsMargins(40, 40, 40, 40)

        # Small debug/status label top-left (useful during demo mode).
        self._status_debug_label = QLabel("", splash_frame)
        self._status_debug_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._status_debug_label.setStyleSheet(
            "background: rgba(5,3,19,0.55);"
            f"color: {_THEME_CYAN};"
            "padding: 6px 10px;"
            "border-radius: 8px;"
            "font-size: 12px;"
        )
        self._status_debug_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self._status_debug_label, 0, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

    # Debug helpers (not part of the app-facing API)

    def _set_debug_text(self, text: str) -> None:
        if self._status_debug_label is not None:
            self._status_debug_label.setText(text)

    def keyPressEvent(self, event: Optional[QKeyEvent]) -> None:
        if event is None:
            return

        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return

        if event.key() == Qt.Key.Key_Space:
            if self._ui.splashFrame.isVisible():
                self.hide_idle()
            else:
                self.show_idle()
            return

        if event.key() == Qt.Key.Key_F11:
            if self.isFullScreen():
                self.showNormal()
            else:
                self.showFullScreen()
            return

        super().keyPressEvent(event)


def main() -> int:
    application_arguments = sys.argv if sys.argv and sys.argv[0] else ["steppy"]
    application = QApplication(application_arguments)

    window = MainWindow(kiosk_mode=False)
    window.resize(1536, 1024)
    window.show()

    # Demo mode: toggle idle/playback on a timer, with a fake clock display.
    demo_state = _DemoState(is_idle=True, fake_time_seconds=0.0)

    demo_tick_timer = QTimer(window)
    demo_tick_timer.setInterval(100)

    def on_tick() -> None:
        nonlocal demo_state
        if demo_state.is_idle:
            window._set_debug_text("IDLE  |  space toggles  |  F11 fullscreen  |  Esc quit")
            return

        demo_state = _DemoState(is_idle=False, fake_time_seconds=demo_state.fake_time_seconds + 0.1)
        window._set_debug_text(f"PLAYING (demo)  |  t={demo_state.fake_time_seconds:0.1f}s")

    demo_tick_timer.timeout.connect(on_tick)
    demo_tick_timer.start()

    toggle_timer = QTimer(window)
    toggle_timer.setInterval(5000)

    def on_toggle() -> None:
        nonlocal demo_state
        if demo_state.is_idle:
            demo_state = _DemoState(is_idle=False, fake_time_seconds=0.0)
            window.hide_idle()
            # Optional: load a known video for layout testing.
            # If you do not want network activity during this demo, comment out this line.
            window.load_video("dQw4w9WgXcQ")
            window.play()
        else:
            demo_state = _DemoState(is_idle=True, fake_time_seconds=0.0)
            window.pause()
            window.show_idle()

    toggle_timer.timeout.connect(on_toggle)
    toggle_timer.start()

    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
