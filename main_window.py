"""\
main_window.py

Single window UI surface for idle, playback, and overlay.

Integration
- Loads UI from main_window_ui.py generated from main_window_ui.ui
- Hosts embedded web video player
- Hosts idle overlay (splash and QR card)
- Hosts gameplay overlay as a QWidget layer
- Supports a local demo mode (spacebar toggles IDLE <-> DEMO)

Public API
- show_idle(), hide_idle()
- set_idle_qr(image)
- load_video(video_id), cue_video(video_id), play(), pause(), seek(seconds)
- set_gameplay_overlay_widget(widget)
- set_demo_controls_widget(widget), set_demo_controls_visible(bool)

Standalone usage
python -m main_window
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Optional

from PyQt6.QtCore import QEvent, QPoint, QRect, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QImage, QKeyEvent, QPixmap
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

from config import get_config, open_config_json_in_editor
from main_window_ui import Ui_MainWindow
from web_player_bridge import WebPlayerBridge

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
    demoModeChanged = pyqtSignal(bool)

    def __init__(self, *, kiosk_mode: bool = False, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)

        self._kiosk_mode = bool(kiosk_mode)
        self._apply_kiosk_settings(self._kiosk_mode)

        # Host for the video surface plus overlay layers.
        self._layers_host = QWidget(self.ui.centralwidget)
        self._layers_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        layers_layout = QGridLayout(self._layers_host)
        layers_layout.setContentsMargins(0, 0, 0, 0)
        layers_layout.setSpacing(0)

        # splashFrame is provided by the .ui file.
        self.ui.splashFrame.setParent(self._layers_host)

        self.web_player = WebPlayerBridge(self._layers_host)
        self.web_player.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._overlay_layer = QWidget(self._layers_host)
        self._overlay_layer.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._overlay_layer.setStyleSheet("background: transparent;")
        self._overlay_layer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._overlay_layout = QGridLayout(self._overlay_layer)
        self._overlay_layout.setContentsMargins(0, 0, 0, 0)
        self._overlay_layout.setSpacing(0)

        self._gameplay_overlay_widget: Optional[QWidget] = None

        layers_layout.addWidget(self.web_player, 0, 0)
        layers_layout.addWidget(self._overlay_layer, 0, 0)
        layers_layout.addWidget(self.ui.splashFrame, 0, 0)

        # Root layout from the .ui file.
        if hasattr(self.ui, "rootLayout") and self.ui.rootLayout is not None:
            self.ui.rootLayout.addWidget(self._layers_host, 1)

        # Demo controls host goes under the video surface.
        self._demo_controls_host = QWidget(self.ui.centralwidget)
        self._demo_controls_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._demo_controls_host_layout = QVBoxLayout(self._demo_controls_host)
        self._demo_controls_host_layout.setContentsMargins(0, 0, 0, 0)
        self._demo_controls_host_layout.setSpacing(0)
        self._demo_controls_widget: Optional[QWidget] = None
        self._demo_controls_host.hide()

        if hasattr(self.ui, "rootLayout") and self.ui.rootLayout is not None:
            self.ui.rootLayout.addWidget(self._demo_controls_host, 0)

        self._demo_mode_enabled = False

        self._idle_qr_card: Optional[QFrame] = None
        self._idle_qr_label: Optional[QLabel] = None
        self._idle_url_hint_label: Optional[QLabel] = None
        self._status_label: Optional[QLabel] = None

        self._idle_control_url: str = ""
        self._idle_url_hover_active: bool = False

        self._build_idle_overlay_elements()

        self.ui.actionPreferences.triggered.connect(self.on_action_preferences)
        self.ui.actionFull_Screen.triggered.connect(self.on_action_fullscreen_toggle)
        self.ui.actionExit.triggered.connect(self.on_action_exit)

        app_config, _config_path = get_config()

        control_url, control_qimage = qr_code.generate_control_qr_qimage(
            app_config,
            target_size_px=IDLE_QR_BOX_SIZE_PX,
            fill_color=THEME_BACKGROUND,
            background_color=THEME_OFFWHITE,
        )
        self._set_idle_url_hint(control_url)
        self.set_idle_qr(control_qimage)

        self.setMinimumSize(0, 0)
        self.show_idle()
        self._set_status_text("IDLE  |  F11 fullscreen  |  press space for demo")

    # -------------------------
    # Public helpers for controller
    # -------------------------

    @property
    def web_player_bridge(self) -> WebPlayerBridge:
        return self.web_player

    def set_gameplay_overlay_widget(self, overlay_widget: Optional[QWidget]) -> None:
        if self._gameplay_overlay_widget is overlay_widget:
            return

        if self._gameplay_overlay_widget is not None:
            try:
                self._overlay_layout.removeWidget(self._gameplay_overlay_widget)
            except Exception:
                pass
            self._gameplay_overlay_widget.setParent(None)
            self._gameplay_overlay_widget.deleteLater()

        self._gameplay_overlay_widget = overlay_widget
        if overlay_widget is None:
            return

        overlay_widget.setParent(self._overlay_layer)
        overlay_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._overlay_layout.addWidget(overlay_widget, 0, 0)
        overlay_widget.show()

    def set_demo_controls_widget(self, demo_widget: Optional[QWidget]) -> None:
        if self._demo_controls_widget is demo_widget:
            return

        if self._demo_controls_widget is not None:
            old = self._demo_controls_widget
            try:
                self._demo_controls_host_layout.removeWidget(old)
            except Exception:
                pass
            old.setParent(None)
            old.deleteLater()

        self._demo_controls_widget = demo_widget
        if demo_widget is None:
            return

        demo_widget.setParent(self._demo_controls_host)
        demo_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._demo_controls_host_layout.addWidget(demo_widget)
        demo_widget.show()

    def set_demo_controls_visible(self, visible: bool) -> None:
        if bool(visible):
            self._demo_controls_host.show()
            self._demo_controls_host.raise_()
        else:
            self._demo_controls_host.hide()

    # -------------------------
    # Playback facade
    # -------------------------

    def load_video(self, video_id: str) -> None:
        self.web_player.load_video(video_id, start_seconds=0.0, autoplay=True)

    def cue_video(self, video_id: str) -> None:
        self.web_player.load_video(video_id, start_seconds=0.0, autoplay=False)

    def play(self) -> None:
        self.web_player.play()

    def pause(self) -> None:
        self.web_player.pause()

    def seek(self, seconds: float) -> None:
        self.web_player.seek(float(seconds))

    # -------------------------
    # Menu actions
    # -------------------------

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

    # -------------------------
    # Idle overlay controls
    # -------------------------

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

        splash_image_label.setMouseTracking(True)
        splash_image_label.installEventFilter(self)

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
        url_hint_label.adjustSize()

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
        debug_label.setWordWrap(True)
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
        self._idle_control_url = str(url_text or "").strip()

        if self._idle_url_hint_label is None:
            return

        self._idle_url_hint_label.setText(self._idle_control_url)
        self._idle_url_hint_label.adjustSize()
        self._position_idle_overlays()

    def _open_idle_url_in_browser(self) -> None:
        url_text = (self._idle_control_url or "").strip()
        if not url_text:
            return

        url = QUrl.fromUserInput(url_text)
        if not url.isValid():
            self._set_status_text("Invalid URL: " + url_text)
            return

        QDesktopServices.openUrl(url)

    def _idle_url_rect_global(self) -> Optional[QRect]:
        if self._idle_url_hint_label is None:
            return None

        if not self.ui.splashFrame.isVisible():
            return None

        if not self._idle_url_hint_label.isVisible():
            return None

        top_left_global = self._idle_url_hint_label.mapToGlobal(QPoint(0, 0))
        return QRect(top_left_global, self._idle_url_hint_label.size())

    def eventFilter(self, watched, event) -> bool:
        if watched is self.ui.splashImageLabel and self.ui.splashFrame.isVisible():
            url_rect_global = self._idle_url_rect_global()

            if event.type() == QEvent.Type.Leave:
                if self._idle_url_hover_active:
                    self._idle_url_hover_active = False
                    self.ui.splashImageLabel.unsetCursor()
                return super().eventFilter(watched, event)

            if url_rect_global is not None and event.type() == QEvent.Type.MouseMove:
                mouse_global = event.globalPosition().toPoint()
                is_over_url = url_rect_global.contains(mouse_global)

                if is_over_url != self._idle_url_hover_active:
                    self._idle_url_hover_active = is_over_url
                    if is_over_url and not self._kiosk_mode:
                        self.ui.splashImageLabel.setCursor(Qt.CursorShape.PointingHandCursor)
                    else:
                        self.ui.splashImageLabel.unsetCursor()

            if url_rect_global is not None and event.type() == QEvent.Type.MouseButtonRelease:
                if event.button() == Qt.MouseButton.LeftButton:
                    mouse_global = event.globalPosition().toPoint()
                    if url_rect_global.contains(mouse_global):
                        self._open_idle_url_in_browser()
                        return True

        return super().eventFilter(watched, event)

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
            max_debug_width = int(max(240, frame_width - IDLE_DEBUG_MARGIN_LEFT_PX - 24))
            debug_label.setMaximumWidth(max_debug_width)
            debug_label.adjustSize()
            debug_size = debug_label.size()
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

    # -------------------------
    # Key handling
    # -------------------------

    def keyPressEvent(self, event: Optional[QKeyEvent]) -> None:
        if event is None:
            return

        if event.key() == Qt.Key.Key_Space:
            self._demo_mode_enabled = not self._demo_mode_enabled
            if self._demo_mode_enabled:
                self.hide_idle()
                self.set_demo_controls_visible(True)
                self.demoModeChanged.emit(True)
                self._set_status_text("DEMO  |  space toggles  |  F11 fullscreen  |  Esc exits fullscreen")
            else:
                self.set_demo_controls_visible(False)
                self.show_idle()
                self.demoModeChanged.emit(False)
                self._set_status_text("IDLE  |  F11 fullscreen  |  press space for demo")
            event.accept()
            return

        if event.key() == Qt.Key.Key_Escape:
            if self.isFullScreen():
                self.showNormal()
                event.accept()
                return

        if event.key() == Qt.Key.Key_F11:
            self.on_action_fullscreen_toggle()
            event.accept()
            return

        super().keyPressEvent(event)


def main() -> int:
    application_arguments = sys.argv if sys.argv and sys.argv[0] else ["steppy"]
    application = QApplication(application_arguments)

    window = MainWindow(kiosk_mode=False)
    window.resize(1536, 1024)
    window.show()

    demo_state = _DemoState(is_idle=True, fake_time_seconds=0.0)

    # This demo loop is intentionally minimal. AppController owns the real tick timers.
    from PyQt6.QtCore import QTimer

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
