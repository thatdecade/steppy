"""
main_window.py

Single window UI surface for idle, playback, and overlay.

Integration
- Loads UI from main_window_ui.py generated from main_window_ui.ui
- Hosts embedded web video player
- Hosts idle overlay (splash and QR card) above everything when idle
- Provides non-interactive fullscreen kiosk behavior

Public API
- show_idle(), hide_idle()
- set_idle_qr(image)
- load_video(video_id), play(), pause(), seek(seconds)

Standalone usage
python -m main_window

No command line arguments are required or used.
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

from config import get_config, open_config_json_in_editor

import qr_code


THEME_BACKGROUND = "#050313"
THEME_CYAN = "#ACE4FC"
THEME_PINK = "#F48CE4"
THEME_MUTED_MAGENTA = "#A4346C"
THEME_OFFWHITE = "#F3F0FC"

IDLE_CARD_MARGIN_RIGHT_PX = 72
IDLE_CARD_MARGIN_BOTTOM_PX = 72
IDLE_DEBUG_MARGIN_LEFT_PX = 24
IDLE_DEBUG_MARGIN_TOP_PX = 20

IDLE_QR_BOX_SIZE_PX = 260


@dataclass(frozen=True)
class _DemoState:
    is_idle: bool
    fake_time_seconds: float


class MainWindow(QMainWindow):
    def __init__(self, *, kiosk_mode: bool = False, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)

        self._kiosk_mode = bool(kiosk_mode)
        self._apply_kiosk_settings(self._kiosk_mode)

        self._layers_host = QWidget(self.ui.centralwidget)
        self._layers_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        layers_layout = QGridLayout(self._layers_host)
        layers_layout.setContentsMargins(0, 0, 0, 0)
        layers_layout.setSpacing(0)

        self.ui.splashFrame.setParent(self._layers_host)

        self._web_player = WebPlayerBridge(self._layers_host)
        self._web_player.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._overlay_layer = QWidget(self._layers_host)
        self._overlay_layer.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._overlay_layer.setStyleSheet("background: transparent;")
        self._overlay_layer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        layers_layout.addWidget(self._web_player, 0, 0)
        layers_layout.addWidget(self._overlay_layer, 0, 0)
        layers_layout.addWidget(self.ui.splashFrame, 0, 0)

        if hasattr(self.ui, "rootLayout") and self.ui.rootLayout is not None:
            self.ui.rootLayout.addWidget(self._layers_host)

        self._idle_qr_card: Optional[QFrame] = None
        self._idle_qr_label: Optional[QLabel] = None
        self._idle_url_hint_label: Optional[QLabel] = None
        self._status_label: Optional[QLabel] = None

        self._build_idle_overlay_elements()
        
        self.ui.actionPreferences.triggered.connect(self.on_action_preferences)
        self.ui.actionFull_Screen.triggered.connect(self.on_action_fullscreen_toggle)
        self.ui.actionExit       .triggered.connect(self.on_action_exit)

        # If config load fails, let it raise. You asked for hard failures.
        app_config, _config_path = get_config()

        control_url, control_qimage = qr_code.generate_control_qr_qimage(
            app_config,
            target_size_px=IDLE_QR_BOX_SIZE_PX,
            fill_color=THEME_BACKGROUND,
            background_color=THEME_OFFWHITE,
        )
        self._set_idle_url_hint(control_url)
        self.set_idle_qr(control_qimage)

        self.show_idle()
        self._set_status_text("IDLE  |  F11 fullscreen  |  press space to test ")
        
    def on_action_fullscreen_toggle(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()
        return

    def on_action_preferences(self) -> None:
        config_path = get_config()[1]
        opened_path = open_config_json_in_editor(config_path)
        self._set_status_text("Opened config: " + str(opened_path))

    def on_action_exit(self) -> None:
        self.close()

    def show_idle(self) -> None:
        self.ui.splashFrame.show()
        self.ui.splashFrame.raise_()
        self._position_idle_overlays()

    def hide_idle(self) -> None:
        self.ui.splashFrame.hide()

    def set_idle_qr(self, image: QImage) -> None:
        if self._idle_qr_label is None:
            return

        if image.isNull():
            self._idle_qr_label.clear()
            self._idle_qr_label.setText("QR unavailable")
            return

        target_width = max(1, self._idle_qr_label.width())
        target_height = max(1, self._idle_qr_label.height())

        pixmap = QPixmap.fromImage(image)
        scaled = pixmap.scaled(
            target_width,
            target_height,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._idle_qr_label.setPixmap(scaled)
        self._idle_qr_label.setText("")

    def load_video(self, video_id: str) -> None:
        self._web_player.load_video(video_id, start_seconds=0.0, autoplay=True)

    def play(self) -> None:
        self._web_player.play()

    def pause(self) -> None:
        self._web_player.pause()

    def seek(self, seconds: float) -> None:
        self._web_player.seek(float(seconds))

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

    def _build_idle_overlay_elements(self) -> None:
        splash_frame = self.ui.splashFrame
        splash_image_label = self.ui.splashImageLabel

        existing_layout = splash_frame.layout()
        if existing_layout is None:
            splash_layout = QVBoxLayout(splash_frame)
            splash_layout.setContentsMargins(0, 0, 0, 0)
            splash_layout.setSpacing(0)
            splash_layout.addWidget(splash_image_label)
        else:
            while existing_layout.count():
                item = existing_layout.takeAt(0)
                widget = item.widget()
                if widget is not None and widget is not splash_image_label:
                    widget.setParent(None)
            existing_layout.setContentsMargins(0, 0, 0, 0)
            existing_layout.setSpacing(0)
            if splash_image_label.parent() is not splash_frame:
                splash_image_label.setParent(splash_frame)
            if existing_layout.count() == 0:
                existing_layout.addWidget(splash_image_label)

        splash_image_label.setScaledContents(True)
        splash_image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        qr_card = QFrame(splash_frame)
        qr_card.setObjectName("idleQrCard")
        qr_card.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        qr_card.setStyleSheet(
            "QFrame#idleQrCard {"
            "  background: qlineargradient(x1:0, y1:0, x2:1, y2:1,"
            "    stop:0 rgba(5, 3, 19, 235),"
            "    stop:1 rgba(95, 66, 171, 80)"
            "  );"
            "  border: 2px solid rgba(172, 228, 252, 150);"
            "  border-radius: 18px;"
            "}"
        )

        qr_card_layout = QVBoxLayout(qr_card)
        qr_card_layout.setContentsMargins(18, 18, 18, 16)
        qr_card_layout.setSpacing(10)

        title_label = QLabel("Scan to control", qr_card)
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setStyleSheet(f"color: {THEME_CYAN}; font-size: 18px; font-weight: 700;")

        subtitle_label = QLabel("Local network only", qr_card)
        subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle_label.setStyleSheet(f"color: {THEME_PINK}; font-size: 12px; font-weight: 600;")

        qr_label = QLabel(qr_card)
        qr_label.setFixedSize(IDLE_QR_BOX_SIZE_PX, IDLE_QR_BOX_SIZE_PX)
        qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        qr_label.setStyleSheet(
            f"background: {THEME_OFFWHITE};"
            f"color: {THEME_BACKGROUND};"
            "border: 2px solid rgba(244, 140, 228, 160);"
            "border-radius: 12px;"
        )
        qr_label.setText("QR pending")

        url_hint_label = QLabel("http://<host>:<port>/", qr_card)
        url_hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        url_hint_label.setStyleSheet(f"color: {THEME_OFFWHITE}; font-size: 12px;")

        footer_label = QLabel("Steppy", qr_card)
        footer_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        footer_label.setStyleSheet(f"color: {THEME_MUTED_MAGENTA}; font-size: 12px; font-weight: 700;")

        qr_card_layout.addWidget(title_label)
        qr_card_layout.addWidget(subtitle_label)
        qr_card_layout.addWidget(qr_label, 0, Qt.AlignmentFlag.AlignCenter)
        qr_card_layout.addWidget(url_hint_label)
        qr_card_layout.addWidget(footer_label)

        qr_card.adjustSize()

        debug_label = QLabel("", splash_frame)
        debug_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        debug_label.setStyleSheet(
            "background: rgba(5, 3, 19, 160);"
            f"color: {THEME_CYAN};"
            "padding: 6px 10px;"
            "border: 1px solid rgba(172, 228, 252, 80);"
            "border-radius: 10px;"
            "font-size: 12px;"
        )
        debug_label.adjustSize()

        self._idle_qr_card = qr_card
        self._idle_qr_label = qr_label
        self._idle_url_hint_label = url_hint_label
        self._status_label = debug_label

        self._position_idle_overlays()

    def _set_idle_url_hint(self, url_text: str) -> None:
        if self._idle_url_hint_label is None:
            return
        self._idle_url_hint_label.setText(url_text)
        self._idle_url_hint_label.adjustSize()
        self._position_idle_overlays()

    def _position_idle_overlays(self) -> None:
        if self._idle_qr_card is None:
            return

        splash_frame = self.ui.splashFrame
        frame_width = max(1, splash_frame.width())
        frame_height = max(1, splash_frame.height())

        qr_card = self._idle_qr_card
        qr_card_size = qr_card.sizeHint()
        qr_card_width = max(1, qr_card_size.width())
        qr_card_height = max(1, qr_card_size.height())

        target_x = frame_width - qr_card_width - IDLE_CARD_MARGIN_RIGHT_PX
        target_y = frame_height - qr_card_height - IDLE_CARD_MARGIN_BOTTOM_PX

        target_x = max(0, target_x)
        target_y = max(0, target_y)

        qr_card.setGeometry(target_x, target_y, qr_card_width, qr_card_height)
        qr_card.raise_()

        if self._status_label is not None:
            debug_label = self._status_label
            debug_size = debug_label.sizeHint()
            debug_width = max(1, debug_size.width())
            debug_height = max(1, debug_size.height())
            debug_label.setGeometry(IDLE_DEBUG_MARGIN_LEFT_PX, IDLE_DEBUG_MARGIN_TOP_PX, debug_width, debug_height)
            debug_label.raise_()
            
    def _set_status_text(self, text: str) -> None:
        if self._status_label is None:
            return
        self._status_label.setText(text)
        self._status_label.adjustSize()
        self._position_idle_overlays()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._position_idle_overlays()

    def keyPressEvent(self, event: Optional[QKeyEvent]) -> None:
        if event is None:
            return

        if event.key() == Qt.Key.Key_Space:
            if self.ui.splashFrame.isVisible():
                self.hide_idle()
                self._set_status_text("PLAYING (demo layout only)")
            else:
                self.show_idle()
                self._set_status_text("IDLE  |  space toggles  |  F11 fullscreen  |  Esc quit")
            return
          
        if event.key() == Qt.Key.Key_Escape:
            if self.isFullScreen():
                self.showNormal()
            return

        if event.key() == Qt.Key.Key_F11:
            self.on_action_fullscreen_toggle()

        super().keyPressEvent(event)

def main() -> int:
    application_arguments = sys.argv if sys.argv and sys.argv[0] else ["steppy"]
    application = QApplication(application_arguments)

    window = MainWindow(kiosk_mode=False)
    window.resize(1536, 1024)
    window.show()

    demo_state = _DemoState(is_idle=True, fake_time_seconds=0.0)

    tick_timer = QTimer(window)
    tick_timer.setInterval(100)

    def on_tick() -> None:
        nonlocal demo_state
        if demo_state.is_idle:
            return
        demo_state = _DemoState(is_idle=False, fake_time_seconds=demo_state.fake_time_seconds + 0.1)

    tick_timer.timeout.connect(on_tick)
    tick_timer.start()

    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
