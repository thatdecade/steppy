# input_router.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Set

from PyQt6.QtCore import QObject, QEvent, Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QLineEdit, QTextEdit, QWidget

from chart_models import LaneInputEvent
from game_clock import GameClock


@dataclass(frozen=True)
class KeyEventPayload:
    key: int
    is_pressed: bool
    is_auto_repeat: bool


class InputRouter(QObject):
    laneInputEvent = pyqtSignal(object)  # LaneInputEvent

    def __init__(self, game_clock: GameClock, *, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._game_clock = game_clock

        self._key_to_lane: Dict[int, int] = {
            int(Qt.Key.Key_A): 0,
            int(Qt.Key.Key_S): 1,
            int(Qt.Key.Key_W): 2,
            int(Qt.Key.Key_D): 3,
        }

        self._pressed_lanes: Set[int] = set()
        self._installed_on_application: Optional[QApplication] = None

        self._ignore_when_typing: bool = True

    def set_ignore_when_typing(self, ignore_when_typing: bool) -> None:
        self._ignore_when_typing = bool(ignore_when_typing)

    def pressed_lanes(self) -> Set[int]:
        return set(self._pressed_lanes)

    def install_on_application(self, qt_application: QApplication) -> None:
        if self._installed_on_application is not None:
            return
        self._installed_on_application = qt_application
        qt_application.installEventFilter(self)

    def remove_from_application(self) -> None:
        if self._installed_on_application is None:
            return
        self._installed_on_application.removeEventFilter(self)
        self._installed_on_application = None

    # Future integration: connect a Qt signal to this slot to feed key events from control_api.
    def on_key_event(self, key: int, is_pressed: bool, is_auto_repeat: bool) -> None:
        payload = KeyEventPayload(key=int(key), is_pressed=bool(is_pressed), is_auto_repeat=bool(is_auto_repeat))
        self._handle_key_payload(payload)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()
        if event_type not in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            return False

        if self._ignore_when_typing and self._focus_is_text_entry():
            return False

        if not isinstance(event, QKeyEvent):
            return False

        is_pressed = (event_type == QEvent.Type.KeyPress)
        payload = KeyEventPayload(
            key=int(event.key()),
            is_pressed=bool(is_pressed),
            is_auto_repeat=bool(event.isAutoRepeat()),
        )
        self._handle_key_payload(payload)
        return False

    def _handle_key_payload(self, payload: KeyEventPayload) -> None:
        if payload.is_auto_repeat:
            return

        if payload.key not in self._key_to_lane:
            return

        lane = int(self._key_to_lane[payload.key])

        if payload.is_pressed:
            if lane in self._pressed_lanes:
                return
            self._pressed_lanes.add(lane)
        else:
            if lane not in self._pressed_lanes:
                return
            self._pressed_lanes.remove(lane)

        lane_input_event = LaneInputEvent(
            time_seconds=float(self._game_clock.song_time_seconds()),
            lane=lane,
            is_pressed=bool(payload.is_pressed),
        )
        self.laneInputEvent.emit(lane_input_event)

    def _focus_is_text_entry(self) -> bool:
        application = QApplication.instance()
        if application is None:
            return False

        focus_widget = application.focusWidget()
        if focus_widget is None:
            return False

        if isinstance(focus_widget, (QLineEdit, QTextEdit)):
            return True

        # Some custom widgets embed QLineEdit children.
        if isinstance(focus_widget, QWidget):
            if focus_widget.findChild(QLineEdit) is not None:
                return True

        return False
