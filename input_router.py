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
#   - Ignore auto repeat.
#   - Track pressed keys to avoid duplicate presses.
# - Time source is injected as a callable returning song time in seconds.
#
########################
# Interfaces:
# Public classes:
# - class InputRouter(PyQt6.QtCore.QObject)
#   - Signals:
#     - inputEvent(gameplay_models.InputEvent)
#   - Methods:
#     - handle_key_press(event: QKeyEvent) -> bool
#     - handle_key_release(event: QKeyEvent) -> bool
#     - clear_pressed_keys() -> None
#     - reset_stats() -> None
#
# Inputs:
# - Raw QKeyEvent from the Qt event loop.
#
# Outputs:
# - Normalized lane input events consumed by JudgeEngine and overlay_renderer.
#
########################

from __future__ import annotations

from typing import Callable, Dict, Optional, Set

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent


def _build_default_key_to_lane_map() -> Dict[int, int]:
    """
    Default lane mapping for a four lane chart.

    Lane indexes:
      0 = Left
      1 = Down
      2 = Up
      3 = Right

    Accepted keys:
      - Arrow keys: Left, Down, Up, Right
      - WASD keys: A, S, W, D
    """
    key_to_lane: Dict[int, int] = {}

    def bind(key_constant: int, lane_index: int) -> None:
        key_to_lane[int(key_constant)] = int(lane_index)

    # Arrow keys
    bind(Qt.Key.Key_Left, 0)
    bind(Qt.Key.Key_Down, 1)
    bind(Qt.Key.Key_Up, 2)
    bind(Qt.Key.Key_Right, 3)

    # WASD
    bind(Qt.Key.Key_A, 0)
    bind(Qt.Key.Key_S, 1)
    bind(Qt.Key.Key_W, 2)
    bind(Qt.Key.Key_D, 3)

    return key_to_lane


class InputRouter(QObject):
    """
    Central keyboard router for gameplay lane input.

    This object never judges timing. Its only job is to:
      - map keys to lane indexes
      - attach the current song time from the injected time provider
      - emit a gameplay_models.InputEvent for each valid press
    """

    # We use "object" here to avoid importing gameplay_models at type check time.
    # At runtime we construct gameplay_models.InputEvent instances.
    inputEvent = pyqtSignal(object)

    def __init__(
        self,
        song_time_provider: Callable[[], float],
        parent: Optional[QObject] = None,
        key_to_lane_map: Optional[Dict[int, int]] = None,
    ) -> None:
        """
        song_time_provider:
            Callable that returns the current song time in seconds.
            GameplayHarnessWindow passes TimingModel.song_time_seconds.
        parent:
            Optional QObject parent.
        key_to_lane_map:
            Optional override for the key map. If omitted the default map
            includes both arrow keys and WASD.
        """
        super().__init__(parent)

        self._song_time_provider: Callable[[], float] = song_time_provider
        self._key_to_lane: Dict[int, int] = (
            dict(key_to_lane_map) if key_to_lane_map is not None else _build_default_key_to_lane_map()
        )

        # Press tracking for debounce and focus loss handling.
        self._pressed_keys: Set[int] = set()

        # Simple stats for future debugging or overlays.
        self._total_presses: int = 0
        self._ignored_presses: int = 0

    # ------------------------------------------------------------------
    # Public API used by gameplay_harness
    # ------------------------------------------------------------------

    def handle_key_press(self, event: QKeyEvent) -> bool:
        """
        Handle a Qt key press.

        Returns True if this router consumed the event, False otherwise.
        """
        key_code = int(event.key())

        # Ignore auto repeat so holding a key does not spam input events.
        if event.isAutoRepeat():
            if key_code in self._key_to_lane:
                self._ignored_presses += 1
                return True
            return False

        # Ignore second press while still held.
        if key_code in self._pressed_keys:
            if key_code in self._key_to_lane:
                self._ignored_presses += 1
                return True
            return False

        self._pressed_keys.add(key_code)

        lane_index = self._key_to_lane.get(key_code)
        if lane_index is None:
            # Not a gameplay key.
            return False

        self._total_presses += 1
        self._emit_input_event_for_lane(lane_index)
        return True

    def handle_key_release(self, event: QKeyEvent) -> bool:
        """
        Handle a Qt key release.

        Returns True if this router consumed the event, False otherwise.
        """
        key_code = int(event.key())

        if event.isAutoRepeat():
            return key_code in self._key_to_lane

        if key_code in self._pressed_keys:
            self._pressed_keys.discard(key_code)

        return key_code in self._key_to_lane

    def clear_pressed_keys(self) -> None:
        """
        Clear pressed state for all keys.

        Called by the harness on focus loss or window deactivation.
        """
        self._pressed_keys.clear()

    def reset_stats(self) -> None:
        """
        Reset debugging counters. Does not change pressed key state.
        """
        self._total_presses = 0
        self._ignored_presses = 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _emit_input_event_for_lane(self, lane_index: int) -> None:
        """
        Construct and emit a gameplay_models.InputEvent for the given lane.
        """
        # Import here to avoid any import cycle at module import time.
        import gameplay_models  # type: ignore

        try:
            song_time_seconds = float(self._song_time_provider())
        except Exception:
            song_time_seconds = 0.0

        input_event = gameplay_models.InputEvent(
            time_seconds=song_time_seconds,
            lane=int(lane_index),
        )
        self.inputEvent.emit(input_event)

    # ------------------------------------------------------------------
    # Introspection helpers (not used by the harness today)
    # ------------------------------------------------------------------

    @property
    def key_to_lane_map(self) -> Dict[int, int]:
        return dict(self._key_to_lane)

    @property
    def total_presses(self) -> int:
        return self._total_presses

    @property
    def ignored_presses(self) -> int:
        return self._ignored_presses


def _run_unit_tests() -> None:
    """
    Small self test for the default key map.

    A real test harness imports gameplay_models and uses a QApplication,
    so this is intentionally minimal.
    """
    router = InputRouter(lambda: 1.25)

    # Check a couple of representative mappings.
    assert router._key_to_lane[int(Qt.Key.Key_A)] == 0
    assert router._key_to_lane[int(Qt.Key.Key_Left)] == 0
    assert router._key_to_lane[int(Qt.Key.Key_S)] == 1
    assert router._key_to_lane[int(Qt.Key.Key_D)] == 3
    assert router._key_to_lane[int(Qt.Key.Key_Up)] == 2


if __name__ == "__main__":
    _run_unit_tests()
    print("input_router.py: ok")
