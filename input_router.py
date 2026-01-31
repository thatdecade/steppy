# -*- coding: utf-8 -*-
########################
# input_router.py
########################
# Purpose:
# - Single keyboard listener for gameplay lane input.
# - Translates QKeyEvent into gameplay_models.InputEvent and emits a Qt signal.
#
# Design notes:
# - This must be the only lane input source. No duplicate key mapping elsewhere.
# - Debounce rules:
#   - Ignore auto-repeat.
#   - Track pressed keys to avoid duplicate presses.
# - Time source is injected as a callable returning song time in seconds.
#
########################
# Interfaces:
# Public dataclasses:
# - InputRouterStats(total_keypresses: int = 0, last_lane: Optional[int] = None)
#
# Public classes:
# - class InputRouter(PyQt6.QtCore.QObject)
#   - Signals:
#     - inputEvent(InputEvent)  (emitted as object typed signal)
#   - __init__(song_time_provider: Callable[[], float], parent: Optional[QObject] = None)
#   - set_enabled(is_enabled: bool) -> None
#   - stats() -> InputRouterStats
#   - handle_key_press(key_event: PyQt6.QtGui.QKeyEvent) -> bool
#   - handle_key_release(key_event: PyQt6.QtGui.QKeyEvent) -> bool
#
# Inputs:
# - QKeyEvent from a window eventFilter or keyPressEvent/keyReleaseEvent.
# - song_time_provider() -> float (typically TimingModel.song_time_seconds)
#
# Outputs:
# - Emits inputEvent(InputEvent) for JudgeEngine and GameplayOverlayWidget subscribers.
#
########################

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Optional, Set

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtCore import Qt

import gameplay_models


@dataclass(frozen=True)
class InputRouterStats:
    total_keypresses: int = 0
    last_lane: Optional[int] = None


class InputRouter(QObject):
    inputEvent = pyqtSignal(object)

    def __init__(
        self,
        song_time_provider: Callable[[], float],
        *,
        key_to_lane: Optional[Dict[int, int]] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._song_time_provider = song_time_provider
        self._is_enabled = True
        self._pressed_keys: Set[int] = set()
        self._total_keypresses = 0
        self._last_lane: Optional[int] = None
        self._key_to_lane = dict(key_to_lane) if key_to_lane is not None else self._build_default_key_map()

    def set_enabled(self, is_enabled: bool) -> None:
        self._is_enabled = bool(is_enabled)
        if not self._is_enabled:
            self.clear_pressed_keys()

    def clear_pressed_keys(self) -> None:
        # Recommended default: clear on disable or focus-loss to avoid stuck keys.
        self._pressed_keys.clear()

    def stats(self) -> InputRouterStats:
        return InputRouterStats(total_keypresses=int(self._total_keypresses), last_lane=self._last_lane)

    def handle_key_press(self, key_event: QKeyEvent) -> bool:
        if not self._is_enabled:
            return False

        if key_event.isAutoRepeat():
            return True

        key_value = int(key_event.key())
        lane = self._key_to_lane.get(key_value)
        if lane is None:
            return False

        if key_value in self._pressed_keys:
            return True

        self._pressed_keys.add(key_value)
        self._total_keypresses += 1
        self._last_lane = int(lane)

        event = gameplay_models.InputEvent(time_seconds=float(self._song_time_provider()), lane=int(lane))
        self.inputEvent.emit(event)
        return True

    def handle_key_release(self, key_event: QKeyEvent) -> bool:
        if key_event.isAutoRepeat():
            return True

        key_value = int(key_event.key())
        lane = self._key_to_lane.get(key_value)
        if lane is None:
            return False

        if key_value in self._pressed_keys:
            self._pressed_keys.remove(key_value)
        return True

    def _build_default_key_map(self) -> Dict[int, int]:
        return {
            int(Qt.Key.Key_A): 0,
            int(Qt.Key.Key_Left): 0,
            int(Qt.Key.Key_S): 1,
            int(Qt.Key.Key_Down): 1,
            int(Qt.Key.Key_W): 2,
            int(Qt.Key.Key_Up): 2,
            int(Qt.Key.Key_D): 3,
            int(Qt.Key.Key_Right): 3,
        }


def _run_unit_tests() -> None:
    # Qt objects are hard to unit test without a QApplication.
    # This test only validates default key map behavior.
    router = InputRouter(lambda: 1.25)
    assert router._key_to_lane[int(Qt.Key.Key_A)] == 0
    assert router._key_to_lane[int(Qt.Key.Key_Up)] == 2


if __name__ == "__main__":
    _run_unit_tests()
    print("input_router.py: ok")
