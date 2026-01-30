"""\
main_window.py

Single window UI surface for idle, playback, and overlay.

Integration
- Loads UI from main_window_ui.py generated from main_window_ui.ui.
- Hosts embedded web video player.
- Hosts gameplay overlay above the player.
- Hosts an idle overlay (splash and QR card) above everything when idle.
- Optional demo controls panel can be toggled while idle.

Public API used by AppController
- show_idle(), hide_idle()
- set_idle_qr(image)
- set_current_video_id(video_id)
- current_video_id (property)
- load_video(video_id, autoplay=False)
- play(), pause(), seek(seconds)
- set_gameplay_overlay_widget(widget)
- set_demo_controls_widget(widget)
- set_demo_controls_visible(visible)
- is_playing_state()
- set_key_press_handler(handler)

Standalone usage
python -m main_window

No command line arguments are required or used.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Callable, Optional

from PyQt6.QtCore import QEvent, QPoint, QRect, QTimer, Qt, QUrl, pyqtSignal
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

DEMO_PANEL_MARGIN_PX = 18


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

        self._layers_host = QWidget(self.ui.centralwidget)
        self._layers_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        layers_layout = QGridLayout(self._layers_host)
        layers_layout.setContentsMargins(0, 0, 0, 0)
        layers_layout.setSpacing(0)

        self.ui.splashFrame.setParent(self._layers_host)

        self.web_player = WebPlayerBridge(self._layers_host)
        # Backward compatible alias, some older code might still access _web_player.
        self._web_player = self.web_player

        self.web_player.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._overlay_layer = QWidget(self._layers_host)
        self._overlay_layer.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._overlay_layer.setStyleSheet("background: transparent;")
        self._overlay_layer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._overlay_layout = QGridLayout(self._overlay_layer)
        self._overlay_layout.setContentsMargins(0, 0, 0, 0)
        self._overlay_layout.setSpacing(0)

        layers_layout.addWidget(self.web_player, 0, 0)
        layers_layout.addWidget(self._overlay_layer, 0, 0)
        layers_layout.addWidget(self.ui.splashFrame, 0, 0)

        if hasattr(self.ui, "rootLayout") and self.ui.rootLayout is not None:
            self.ui.rootLayout.addWidget(self._layers_host)

        self._idle_qr_card: Optional[QFrame] = None
        self._idle_qr_label: Optional[QLabel] = None
        self._idle_url_hint_label: Optional[QLabel] = None
        self._status_label: Optional[QLabel] = None

        self._idle_control_url: str = ""
        self._idle_url_hover_active: bool = False

        self._demo_controls_host = QFrame(self._layers_host)
        self._demo_controls_host.setObjectName("demoControlsHost")
        self._demo_controls_host.setStyleSheet(
            "QFrame#demoControlsHost {"
            "  background: rgba(5, 3, 19, 200);"
            "  border: 1px solid rgba(172, 228, 252, 80);"
            "  border-radius: 14px;"
            "}"
        )
        self._demo_controls_host.hide()

        self._demo_controls_widget: Optional[QWidget] = None
        self._demo_mode_active: bool = False

        self._current_video_id: str = ""
        self._last_player_state_name: str = "unknown"

        self._key_press_handler: Optional[Callable[[int], bool]] = None

        self._gameplay_overlay_widget: Optional[QWidget] = None

        self._build_idle_overlay_elements()

        self.ui.actionPreferences.triggered.connect(self.on_action_preferences)
        self.ui.actionFull_Screen.triggered.connect(self.on_action_fullscreen_toggle)
        self.ui.actionExit.triggered.connect(self.on_action_exit)

        # If config load fails, let it raise.
        app_config, _config_path = get_config()

        control_url, control_qimage = qr_code.generate_control_qr_qimage(
            app_config,
            target_size_px=IDLE_QR_BOX_SIZE_PX,
            fill_color=THEME_BACKGROUND,
            background_color=THEME_OFFWHITE,
        )
        self._set_idle_url_hint(control_url)
        self.set_idle_qr(control_qimage)

        self.web_player.stateChanged.connect(self._on_player_state_changed)

        self.show_idle()
        self._set_status_text("IDLE  |  F11 fullscreen  |  space demo")

    # -----------------
    # Public API
    # -----------------

    @property
    def current_video_id(self) -> str:
        return self._current_video_id

    def set_current_video_id(self, video_id: str) -> None:
        self._current_video_id = (video_id or "").strip()

    def is_playing_state(self) -> bool:
        return self._last_player_state_name == "playing"

    def set_key_press_handler(self, handler: Optional[Callable[[int], bool]]) -> None:
        self._key_press_handler = handler

    def set_demo_controls_widget(self, demo_controls_widget: QWidget) -> None:
        if self._demo_controls_widget is demo_controls_widget:
            return

        if self._demo_controls_widget is not None:
            old_widget = self._demo_controls_widget
            old_widget.setParent(None)
            old_widget.deleteLater()

        self._demo_controls_widget = demo_controls_widget
        demo_controls_widget.setParent(self._demo_controls_host)

        host_layout = self._demo_controls_host.layout()
        if host_layout is None:
            host_layout = QVBoxLayout(self._demo_controls_host)
            host_layout.setContentsMargins(0, 0, 0, 0)
            host_layout.setSpacing(0)
        else:
            while host_layout.count():
                item = host_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.setParent(None)

        host_layout.addWidget(demo_controls_widget)
        self._position_demo_controls()

    def set_demo_controls_visible(self, visible: bool) -> None:
        if self._demo_controls_widget is None:
            self._demo_controls_host.hide()
            return

        if bool(visible):
            self._demo_controls_host.show()
            self._demo_controls_host.raise_()
            self._position_demo_controls()
        else:
            self._demo_controls_host.hide()

    def set_gameplay_overlay_widget(self, overlay_widget: QWidget) -> None:
        if self._gameplay_overlay_widget is overlay_widget:
            return

        if self._gameplay_overlay_widget is not None:
            old_overlay = self._gameplay_overlay_widget
            self._overlay_layout.removeWidget(old_overlay)
            old_overlay.setParent(None)
            old_overlay.deleteLater()

        self._gameplay_overlay_widget = overlay_widget
        overlay_widget.setParent(self._overlay_layer)
        self._overlay_layout.addWidget(overlay_widget, 0, 0)

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

    def load_video(self, video_id: str, *, autoplay: bool = False) -> None:
        self.web_player.load_video(video_id, start_seconds=0.0, autoplay=bool(autoplay))

    def play(self) -> None:
        self.web_player.play()

    def pause(self) -> None:
        self.web_player.pause()

    def seek(self, seconds: float) -> None:
        self.web_player.seek(float(seconds))

    # -----------------
    # Menu actions
    # -----------------

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

    # -----------------
    # Internal wiring
    # -----------------

    def _on_player_state_changed(self, player_state_info: object) -> None:
        try:
            self._last_player_state_name = str(getattr(player_state_info, "name", "unknown")).strip().lower() or "unknown"
        except Exception:
            self._last_player_state_name = "unknown"

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

        # Capture click and hover events on the full splash surface.
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
            debug_size = debug_label.sizeHint()
            debug_width = max(1, debug_size.width())
            debug_height = max(1, debug_size.height())
            debug_label.setGeometry(IDLE_DEBUG_MARGIN_LEFT_PX, IDLE_DEBUG_MARGIN_TOP_PX, debug_width, debug_height)
            debug_label.raise_()

    def _position_demo_controls(self) -> None:
        if self._demo_controls_widget is None:
            return

        host_width = max(1, self._layers_host.width())
        host_height = max(1, self._layers_host.height())

        available_width = max(1, host_width - (DEMO_PANEL_MARGIN_PX * 2))

        desired_width = min(available_width, 1100)
        desired_height = self._demo_controls_host.sizeHint().height()
        if desired_height <= 0:
            desired_height = 190

        target_x = int((host_width - desired_width) * 0.5)
        target_y = int(host_height - desired_height - DEMO_PANEL_MARGIN_PX)

        target_x = max(DEMO_PANEL_MARGIN_PX, target_x)
        target_y = max(DEMO_PANEL_MARGIN_PX, target_y)

        self._demo_controls_host.setGeometry(target_x, target_y, int(desired_width), int(desired_height))
        self._demo_controls_host.raise_()

    def _set_status_text(self, text: str) -> None:
        if self._status_label is None:
            return
        self._status_label.setText(text)
        self._status_label.adjustSize()
        self._position_idle_overlays()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._position_idle_overlays()
        self._position_demo_controls()

    def keyPressEvent(self, event: Optional[QKeyEvent]) -> None:
        if event is None:
            return

        if event.key() == Qt.Key.Key_Space:
            # Space toggles demo mode only when idle is visible, or when demo is already active.
            if self.ui.splashFrame.isVisible() and not self._demo_mode_active:
                self._demo_mode_active = True
                self.hide_idle()
                self.set_demo_controls_visible(True)
                self.demoModeChanged.emit(True)
                event.accept()
                return

            if self._demo_mode_active:
                self._demo_mode_active = False
                self.set_demo_controls_visible(False)
                # Return to idle. AppController will re-apply control state after demo is disabled.
                self.pause()
                self.seek(0.0)
                self.show_idle()
                self.demoModeChanged.emit(False)
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

        if self._key_press_handler is not None:
            try:
                handled = bool(self._key_press_handler(int(event.key())))
            except Exception:
                handled = False
            if handled:
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
