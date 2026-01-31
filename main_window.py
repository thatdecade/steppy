# -*- coding: utf-8 -*-
########################
# main_window.py
########################
# Purpose:
# - Primary Qt window and UI host.
# - Owns its menus and local window behaviors, and hosts a single central gameplay frame (idle or gameplay).
#
# Design notes:
# - MainWindow should not decide demo mode or emit application mode switches.
# - MainWindow may subscribe to InputRouter and control status signals via external wiring.
# - Central frame hosting is the primary UI contract: attach or remove the current QFrame/QWidget.
#
########################
# Interfaces:
# Public classes:
# - class MainWindow(PyQt6.QtWidgets.QMainWindow)
#   - UI hosting:
#     - set_gameplay_overlay_widget(widget: Optional[QWidget]) -> None
#     - set_demo_controls_widget(widget: Optional[QWidget]) -> None
#     - set_demo_controls_visible(is_visible: bool) -> None
#     - show_idle() -> None
#     - hide_idle() -> None
#     - set_idle_qr(qimage: QImage) -> None
#   - Playback passthrough helpers:
#     - load_video(video_id_or_url: str, start_seconds: float = 0.0, autoplay: bool = True) -> None
#     - play() -> None, pause() -> None, seek(seconds: float) -> None
#   - State helpers:
#     - current_video_id() -> Optional[str]
#     - set_current_video_id(video_id: Optional[str]) -> None
#     - is_playing_state() -> bool
#     - set_demo_mode_enabled(is_enabled: bool) -> None
#   - Menu actions:
#     - on_action_fullscreen_toggle(), on_action_preferences(), on_action_exit()
#
# Signals (current implementation):
# - demoModeChanged(bool)
#
# Inputs:
# - QKeyEvent, menu actions, and externally wired control signals.
#
# Outputs:
# - Manages Qt widgets and delegates playback to the embedded WebPlayerBridge.
#
########################
# Unit Tests:
# pip install PyQt6 qrcode
# - Keep as manual UI smoke:
#   - python main_window.py
# - Prefer AppController integration tests for most behaviors, since MainWindow should stay thin.
########################

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

        # Host for the layer stack: player, overlay layer, splash (idle).
        self._layers_host = QWidget(self.ui.centralwidget)
        self._layers_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        layers_layout = QGridLayout(self._layers_host)
        layers_layout.setContentsMargins(0, 0, 0, 0)
        layers_layout.setSpacing(0)

        # The splashFrame and splashImageLabel are defined in the .ui and are the authoritative idle layout.
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

        # The generated UI includes rootLayout on centralwidget.
        if hasattr(self.ui, "rootLayout") and self.ui.rootLayout is not None:
            self.ui.rootLayout.addWidget(self._layers_host)

        # Idle overlay widgets are parented to splashFrame (idle only).
        self._idle_qr_card: Optional[QFrame] = None
        self._idle_qr_label: Optional[QLabel] = None
        self._idle_url_hint_label: Optional[QLabel] = None
        self._idle_status_label: Optional[QLabel] = None

        self._idle_control_url: str = ""
        self._idle_url_hover_active: bool = False

        # Demo controls are hosted in a single fixed card at the bottom.
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

        self._current_video_id: Optional[str] = None
        self._last_player_state_name: str = "unknown"

        # Optional key press callback (legacy integration hook).
        self._key_press_handler: Optional[Callable[[int], bool]] = None

        # Gameplay overlay widget (Play/Learning overlay from the gameplay chunk).
        self._gameplay_overlay_widget: Optional[QWidget] = None

        self._build_idle_overlay_elements()

        # Menu wiring uses action names from the .ui.
        if hasattr(self.ui, "actionPreferences"):
            self.ui.actionPreferences.triggered.connect(self.on_action_preferences)
        if hasattr(self.ui, "actionFull_Screen"):
            self.ui.actionFull_Screen.triggered.connect(self.on_action_fullscreen_toggle)
        if hasattr(self.ui, "actionExit"):
            self.ui.actionExit.triggered.connect(self.on_action_exit)

        # Generate QR for the idle card. Uses qr_code module contract from the design plan.
        app_config, _config_path = get_config()
        try:
            control_url = qr_code.build_control_url(app_config)
            qimage, qr_result = qr_code.generate_control_qr_qimage(
                url=control_url,
                fill_color=THEME_BACKGROUND,
                background_color=THEME_OFFWHITE,
            )
            if qr_result.ok:
                self._set_idle_url_hint(qr_result.url)
                self.set_idle_qr(qimage)
            else:
                self._set_idle_url_hint(control_url)
                self._set_idle_status_text("QR unavailable: " + str(qr_result.error or "unknown"))
        except Exception as exc:
            self._set_idle_status_text("QR unavailable: " + str(exc))

        self.web_player.stateChanged.connect(self._on_player_state_changed)

        self.show_idle()
        self.set_idle_state_text("IDLE  |  F11 fullscreen  |  space demo")

    # -----------------
    # Public API (design plan)
    # -----------------

    def player_bridge(self) -> WebPlayerBridge:
        return self.web_player

    def current_video_id(self) -> Optional[str]:
        return self._current_video_id

    def set_current_video_id(self, video_id: Optional[str]) -> None:
        cleaned = (video_id or "").strip()
        self._current_video_id = cleaned if cleaned else None

    def is_playing_state(self) -> bool:
        return self._last_player_state_name == "playing"

    def set_demo_mode_enabled(self, is_enabled: bool) -> None:
        # MainWindow does not decide mode transitions. This only controls visibility.
        self.set_demo_controls_visible(bool(is_enabled))

    def set_key_press_handler(self, handler: Optional[Callable[[int], bool]]) -> None:
        self._key_press_handler = handler

    def set_demo_controls_widget(self, demo_controls_widget: Optional[QWidget]) -> None:
        if self._demo_controls_widget is demo_controls_widget:
            return

        if self._demo_controls_widget is not None:
            old_widget = self._demo_controls_widget
            old_widget.setParent(None)
            old_widget.deleteLater()

        self._demo_controls_widget = demo_controls_widget
        if demo_controls_widget is None:
            self._demo_controls_host.hide()
            return

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

    def set_demo_controls_visible(self, is_visible: bool) -> None:
        if self._demo_controls_widget is None:
            self._demo_controls_host.hide()
            return

        if bool(is_visible):
            self._demo_controls_host.show()
            self._demo_controls_host.raise_()
            self._position_demo_controls()
        else:
            self._demo_controls_host.hide()

    def set_gameplay_overlay_widget(self, widget: Optional[QWidget]) -> None:
        if self._gameplay_overlay_widget is widget:
            return

        if self._gameplay_overlay_widget is not None:
            old_overlay = self._gameplay_overlay_widget
            self._overlay_layout.removeWidget(old_overlay)
            old_overlay.setParent(None)
            old_overlay.deleteLater()

        self._gameplay_overlay_widget = widget
        if widget is None:
            return

        widget.setParent(self._overlay_layer)
        self._overlay_layout.addWidget(widget, 0, 0)

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

        target_width = int(self._idle_qr_label.width()) or IDLE_QR_BOX_SIZE_PX
        target_height = int(self._idle_qr_label.height()) or IDLE_QR_BOX_SIZE_PX

        pixmap = QPixmap.fromImage(image)
        scaled = pixmap.scaled(
            target_width,
            target_height,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._idle_qr_label.setPixmap(scaled)
        self._idle_qr_label.setText("")

    def load_video(self, video_id_or_url: str, start_seconds: float = 0.0, autoplay: bool = True) -> None:
        # WebPlayerBridge.load_video is keyword-only for video_id_or_url.
        self.web_player.load_video(video_id_or_url=video_id_or_url, start_seconds=float(start_seconds), autoplay=bool(autoplay))

    def play(self) -> None:
        self.web_player.play()

    def pause(self) -> None:
        self.web_player.pause()

    def seek(self, seconds: float) -> None:
        self.web_player.seek(float(seconds))

    def set_muted(self, is_muted: bool) -> None:
        self.web_player.set_muted(bool(is_muted))

    def set_volume(self, volume_percent: int) -> None:
        self.web_player.set_volume(int(volume_percent))

    # Extra convenience hooks used by the Desktop Shell orchestrator.
    def set_idle_state_text(self, text: str) -> None:
        self._set_idle_status_text(text)

    def set_gameplay_state_text(self, text: str) -> None:
        # Keep gameplay debug text out of the idle card; use the status bar.
        if self.statusBar() is not None:
            self.statusBar().showMessage(str(text or "").strip())
        # If idle is visible, also show it in the idle debug label.
        if self.ui.splashFrame.isVisible():
            self._set_idle_status_text(text)

    # -----------------
    # Menu actions
    # -----------------

    def on_action_fullscreen_toggle(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def on_action_preferences(self) -> None:
        try:
            opened_path = open_config_json_in_editor()
        except TypeError:
            # Backward compatibility if the function still expects a path.
            config_path = get_config()[1]
            opened_path = open_config_json_in_editor(config_path)

        self._set_idle_status_text("Opened config: " + str(opened_path))

    def on_action_exit(self) -> None:
        self.close()

    # -----------------
    # Internal wiring
    # -----------------

    def _on_player_state_changed(self, player_state_info: object) -> None:
        try:
            # WebPlayerBridge publishes PlayerStateInfo.state_name.
            state_name = str(getattr(player_state_info, "state_name", "unknown")).strip().lower()
            self._last_player_state_name = state_name or "unknown"
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

        # Idle debug/state label (top-left).
        status_label = QLabel("", splash_frame)
        status_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        status_label.setStyleSheet(
            "background: rgba(5, 3, 19, 160);"
            f"color: {THEME_CYAN};"
            "padding: 6px 10px;"
            "border: 1px solid rgba(172, 228, 252, 80);"
            "border-radius: 10px;"
            "font-size: 12px;"
        )
        status_label.adjustSize()

        self._idle_qr_card = qr_card
        self._idle_qr_label = qr_label
        self._idle_url_hint_label = url_hint_label
        self._idle_status_label = status_label

        self._position_idle_overlays()

    def _set_idle_url_hint(self, url_text: str) -> None:
        self._idle_control_url = str(url_text or "").strip()

        if self._idle_url_hint_label is None:
            return

        self._idle_url_hint_label.setText(self._idle_control_url)
        self._idle_url_hint_label.adjustSize()
        self._position_idle_overlays()

    def _set_idle_status_text(self, text: str) -> None:
        if self._idle_status_label is None:
            return
        self._idle_status_label.setText(str(text or "").strip())
        self._idle_status_label.adjustSize()
        self._position_idle_overlays()

    def _open_idle_url_in_browser(self) -> None:
        url_text = (self._idle_control_url or "").strip()
        if not url_text:
            return

        url = QUrl.fromUserInput(url_text)
        if not url.isValid():
            self._set_idle_status_text("Invalid URL: " + url_text)
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

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
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

        if self._idle_status_label is not None:
            status_label = self._idle_status_label
            status_size = status_label.sizeHint()
            status_width = max(1, status_size.width())
            status_height = max(1, status_size.height())
            status_label.setGeometry(IDLE_DEBUG_MARGIN_LEFT_PX, IDLE_DEBUG_MARGIN_TOP_PX, status_width, status_height)
            status_label.raise_()

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
