"""\
main_window.py

Single window UI surface for idle, playback, and overlay.

Integration
- Loads UI from main_window_ui.py (generated from main_window_ui.ui).
- Hosts embedded web video player.
- Hosts idle overlay (splash and QR card) above everything when idle.
- Hosts gameplay overlay widget above the player.
- Hosts demo controls panel (local only) when demo mode is enabled.

Behavior
- Normal launch: web server is running, app starts in IDLE.
- Press Space while idle to toggle demo mode:
  - Enables on screen controls.
  - Hides idle splash.
  - Press Space again to return to IDLE.

The window does not own the gameplay session. That lives in AppController.
"""

from __future__ import annotations

from dataclasses import dataclass
import sys
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

        # Web player
        self.web_player = WebPlayerBridge(self._layers_host)
        self.web_player.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # Backward compatible alias for older code.
        self._web_player = self.web_player

        # Overlay layer (gameplay overlay widgets live here).
        self._overlay_layer = QWidget(self._layers_host)
        self._overlay_layer.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._overlay_layer.setStyleSheet("background: transparent;")
        self._overlay_layer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # Demo controls container layer.
        self._demo_controls_layer = QWidget(self._layers_host)
        self._demo_controls_layer.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self._demo_controls_layer.setStyleSheet("background: transparent;")
        self._demo_controls_layer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._demo_controls_layer.hide()

        layers_layout.addWidget(self.web_player, 0, 0)
        layers_layout.addWidget(self._overlay_layer, 0, 0)
        layers_layout.addWidget(self._demo_controls_layer, 0, 0)
        layers_layout.addWidget(self.ui.splashFrame, 0, 0)

        if hasattr(self.ui, "rootLayout") and self.ui.rootLayout is not None:
            self.ui.rootLayout.addWidget(self._layers_host)

        self._idle_qr_card: Optional[QFrame] = None
        self._idle_qr_label: Optional[QLabel] = None
        self._idle_url_hint_label: Optional[QLabel] = None
        self._status_label: Optional[QLabel] = None

        self._idle_control_url: str = ""
        self._idle_url_hover_active: bool = False

        self._demo_mode_enabled = False
        self._demo_controls_widget: Optional[QWidget] = None
        self._key_input_handler = None

        self._current_video_id: Optional[str] = None
        self._last_player_state_name = "unstarted"

        self._build_idle_overlay_elements()

        self.ui.actionPreferences.triggered.connect(self.on_action_preferences)
        self.ui.actionFull_Screen.triggered.connect(self.on_action_fullscreen_toggle)
        self.ui.actionExit.triggered.connect(self.on_action_exit)

        # Track player state for is_playing_state.
        self.web_player.stateChanged.connect(self._on_player_state_changed)

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
        self._set_status_text("IDLE  |  F11 fullscreen  |  press space for demo")

    # -------------------------
    # Properties expected by controller
    # -------------------------

    @property
    def current_video_id(self) -> Optional[str]:
        return self._current_video_id

    def set_current_video_id(self, video_id: Optional[str]) -> None:
        cleaned = (video_id or "").strip() or None
        self._current_video_id = cleaned

    def is_playing_state(self) -> bool:
        return str(self._last_player_state_name).strip().lower() == "playing"

    def set_key_input_handler(self, handler) -> None:
        self._key_input_handler = handler

    # -------------------------
    # Video controls
    # -------------------------

    def load_video(self, video_id: str, *, start_seconds: float = 0.0, autoplay: bool = True) -> None:
        self.web_player.load_video(video_id, start_seconds=float(start_seconds), autoplay=bool(autoplay))

    def play(self) -> None:
        self.web_player.play()

    def pause(self) -> None:
        self.web_player.pause()

    def seek(self, seconds: float) -> None:
        self.web_player.seek(float(seconds))

    # -------------------------
    # Overlay wiring
    # -------------------------

    def set_gameplay_overlay_widget(self, overlay_widget: Optional[QWidget]) -> None:
        """Replace the gameplay overlay widget hosted in the overlay layer."""
        # Clear existing children (but keep the layer widget itself).
        for child in list(self._overlay_layer.children()):
            if isinstance(child, QWidget):
                child.setParent(None)
                child.deleteLater()

        if overlay_widget is None:
            return

        overlay_widget.setParent(self._overlay_layer)
        overlay_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        layout = self._overlay_layer.layout()
        if layout is None:
            layout = QGridLayout(self._overlay_layer)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)

        if isinstance(layout, QGridLayout):
            layout.addWidget(overlay_widget, 0, 0)

        overlay_widget.show()
        overlay_widget.raise_()

    def set_demo_controls_widget(self, demo_controls_widget: Optional[QWidget]) -> None:
        # Clear existing
        for child in list(self._demo_controls_layer.children()):
            if isinstance(child, QWidget):
                child.setParent(None)
                child.deleteLater()

        self._demo_controls_widget = demo_controls_widget
        if demo_controls_widget is None:
            return

        demo_controls_widget.setParent(self._demo_controls_layer)
        demo_controls_widget.show()

        layout = self._demo_controls_layer.layout()
        if layout is None:
            layout = QGridLayout(self._demo_controls_layer)
            layout.setContentsMargins(16, 16, 16, 16)
            layout.setSpacing(0)

        if isinstance(layout, QGridLayout):
            layout.addWidget(demo_controls_widget, 0, 0, Qt.AlignmentFlag.AlignBottom)

        demo_controls_widget.raise_()

    def set_demo_controls_visible(self, visible: bool) -> None:
        self._demo_controls_layer.setVisible(bool(visible))
        if visible:
            self._demo_controls_layer.raise_()

    # -------------------------
    # Idle splash
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

    # -------------------------
    # Actions
    # -------------------------

    def on_action_fullscreen_toggle(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def on_action_preferences(self) -> None:
        config_path = get_config()[1]
        opened_path = open_config_json_in_editor(config_path)
        self._set_status_text("Opened config: " + str(opened_path))

    def on_action_exit(self) -> None:
        self.close()

    # -------------------------
    # Internal helpers
    # -------------------------

    def _on_player_state_changed(self, player_state_info: object) -> None:
        try:
            self._last_player_state_name = str(getattr(player_state_info, "name", "unknown"))
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
        debug_label.setMaximumWidth(720)
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
            debug_label.setMaximumWidth(max(260, frame_width - 120))
            debug_size = debug_label.sizeHint()
            debug_width = max(1, min(debug_size.width(), frame_width - 48))
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

        if event.key() == Qt.Key.Key_Escape:
            if self.isFullScreen():
                self.showNormal()
                event.accept()
                return

        if event.key() == Qt.Key.Key_F11:
            self.on_action_fullscreen_toggle()
            event.accept()
            return

        if event.key() == Qt.Key.Key_Space:
            if self.ui.splashFrame.isVisible() and not self._demo_mode_enabled:
                self._demo_mode_enabled = True
                self.hide_idle()
                self.set_demo_controls_visible(True)
                self.demoModeChanged.emit(True)
                event.accept()
                return

            if self._demo_mode_enabled:
                self._demo_mode_enabled = False
                self.set_demo_controls_visible(False)
                self.show_idle()
                self.demoModeChanged.emit(False)
                self._set_status_text("IDLE  |  F11 fullscreen  |  press space for demo")
                event.accept()
                return

        # Gameplay keys are routed to AppController.
        handler = self._key_input_handler
        if callable(handler):
            try:
                handled = bool(handler(int(event.key())))
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

    # Minimal UI demo loop (kept for quick layout testing).
    demo_state = _DemoState(is_idle=True, fake_time_seconds=0.0)

    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
