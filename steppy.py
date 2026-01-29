"""
steppy.py

Real entrypoint that launches the full application. Supports demo mode for offline development.

Integration
- Creates QApplication
- Loads config and paths
- Instantiates controller and main window
- Starts embedded Flask web server via control_api.ControlApiBridge
- Wires control signals and starts the Qt event loop

TODO (pending integration with other modules)
- Replace local path discovery with paths.py (not available yet).
- Replace direct MainWindow wiring with AppController wiring from app_controller.py (not available yet).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import QApplication

from config import get_config
from control_api import ControlApiBridge, ControlStatus
from main_window import MainWindow


@dataclass
class _RuntimeState:
    last_state: str = "UNKNOWN"
    last_video_id: Optional[str] = None


def _resolve_web_root_dir() -> Path:
    """Resolve the web assets directory.

    The standalone web_server.py picks a default of <web_server.py dir>/assets.
    steppy.py tries a few sensible candidates, then falls back.

    TODO: Replace this with paths.py once it exists.
    """

    candidates: list[Path] = []

    try:
        candidates.append(Path(__file__).resolve().parent / "assets")
    except Exception:
        pass

    try:
        import web_server

        candidates.append(Path(web_server.__file__).resolve().parent / "assets")
    except Exception:
        pass

    candidates.append(Path.cwd() / "assets")

    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return candidate

    if candidates:
        return candidates[0]

    return Path.cwd()


def _apply_status_to_window(*, main_window: MainWindow, status: ControlStatus, runtime_state: _RuntimeState) -> None:
    state_value = (status.state or "").strip().upper()

    if state_value == "IDLE":
        main_window.show_idle()
        try:
            main_window.pause()
        except Exception:
            pass

    elif state_value == "PLAYING":
        main_window.hide_idle()
        if status.video_id and status.video_id != runtime_state.last_video_id:
            main_window.load_video(status.video_id)
            runtime_state.last_video_id = status.video_id
        try:
            main_window.play()
        except Exception:
            pass

    elif state_value == "PAUSED":
        main_window.hide_idle()
        if status.video_id and status.video_id != runtime_state.last_video_id:
            main_window.load_video(status.video_id)
            runtime_state.last_video_id = status.video_id
        try:
            main_window.pause()
        except Exception:
            pass

    runtime_state.last_state = state_value or "UNKNOWN"


def main() -> int:
    argument_parser = argparse.ArgumentParser(description="Steppy application")
    argument_parser.add_argument("--demo", action="store_true", help="Do not start web server or control bridge.")
    argument_parser.add_argument("--kiosk", action="store_true", help="Enable kiosk window flags and hide cursor.")
    argument_parser.add_argument("--fullscreen", action="store_true", help="Start in fullscreen.")
    argument_parser.add_argument("--web-debug", action="store_true", help="Enable Flask debug mode.")
    argument_parser.add_argument(
        "--poll-interval-ms",
        type=int,
        default=200,
        help="In-process control status polling interval (Qt timer).",
    )
    parsed_args = argument_parser.parse_args()

    app_config, _config_path = get_config()

    qt_application = QApplication(sys.argv)

    main_window = MainWindow(kiosk_mode=bool(parsed_args.kiosk))
    main_window.resize(1536, 1024)
    main_window.show()

    if parsed_args.fullscreen:
        main_window.showFullScreen()

    if parsed_args.demo:
        return int(qt_application.exec())

    web_root_dir = _resolve_web_root_dir()

    runtime_state = _RuntimeState()

    control_bridge = ControlApiBridge(
        bind_host=str(app_config.web_server.host),
        bind_port=int(app_config.web_server.port),
        web_root_dir=web_root_dir,
        debug=bool(parsed_args.web_debug),
        poll_interval_ms=int(parsed_args.poll_interval_ms),
        parent=main_window,
    )

    def on_video_changed(status_object: object) -> None:
        status = status_object if isinstance(status_object, ControlStatus) else None
        if status is None:
            return
        _apply_status_to_window(main_window=main_window, status=status, runtime_state=runtime_state)

    def on_state_changed(_state_text: str) -> None:
        status = control_bridge.last_status()
        if status is None:
            return
        _apply_status_to_window(main_window=main_window, status=status, runtime_state=runtime_state)

    def on_error_changed(error_text: str) -> None:
        cleaned = (error_text or "").strip()
        if not cleaned:
            return
        print("Control API error: " + cleaned, file=sys.stderr)

    control_bridge.video_changed.connect(on_video_changed)
    control_bridge.state_changed.connect(on_state_changed)
    control_bridge.error_changed.connect(on_error_changed)

    control_bridge.start()

    return int(qt_application.exec())


if __name__ == "__main__":
    raise SystemExit(main())
