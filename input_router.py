"""\
input_router.py

Keyboard input mapping for the standalone gameplay harness.

Mapping
- W: lane 0
- A: lane 1
- S: lane 2
- D: lane 3

Design goals
- Debounce key repeat
- Emit timestamped lane events using the gameplay clock
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Optional

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtCore import Qt

from gameplay_models import InputEvent


TimeProvider = Callable[[], float]


@dataclass
class InputRouterStats:
    total_keypresses: int = 0
    last_lane: Optional[int] = None


class InputRouter(QObject):
    inputEvent = pyqtSignal(object)  # InputEvent

    def __init__(self, song_time_provider: TimeProvider, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._song_time_provider = song_time_provider
        self._enabled = True

        self._key_to_lane: Dict[int, int] = {
            int(Qt.Key.Key_W): 0,
            int(Qt.Key.Key_A): 1,
            int(Qt.Key.Key_S): 2,
            int(Qt.Key.Key_D): 3,
        }

        self._pressed_keys: set[int] = set()
        self._stats = InputRouterStats()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        if not self._enabled:
            self._pressed_keys.clear()

    def stats(self) -> InputRouterStats:
        return self._stats

    def handle_key_press(self, key_event: QKeyEvent) -> bool:
        if not self._enabled:
            return False

        if key_event.isAutoRepeat():
            return False

        key_code = int(key_event.key())
        lane = self._key_to_lane.get(key_code)
        if lane is None:
            return False

        if key_code in self._pressed_keys:
            return True

        self._pressed_keys.add(key_code)

        input_time_seconds = float(self._song_time_provider())
        input_event = InputEvent(time_seconds=input_time_seconds, lane=int(lane))
        self._stats.total_keypresses += 1
        self._stats.last_lane = int(lane)

        self.inputEvent.emit(input_event)
        return True

    def handle_key_release(self, key_event: QKeyEvent) -> bool:
        if key_event.isAutoRepeat():
            return False

        key_code = int(key_event.key())
        if key_code in self._pressed_keys:
            self._pressed_keys.remove(key_code)

        return key_code in self._key_to_lane
