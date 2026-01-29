"""
steppy.py

Real entrypoint that launches the full application. Supports demo mode for offline development.

Integration
- Creates QApplication
- Loads config and paths
- Instantiates controller and main window
- Starts Flask web server in background
- Wires control polling and starts the Qt event loop

TODO (pending integration with other modules)
- Replace local path discovery with paths.py (not available yet).
- Replace polling bridge with AppController wiring from app_controller.py (not available yet).
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from config import get_config
from control_api import ControlApiClient, ControlApiError, ControlStatus
from main_window import MainWindow


@dataclass
class _RuntimeState:
    last_state: str = "UNKNOWN"
    last_video_id: Optional[str] = None
    last_seen_ok: bool = False
    last_error_text: Optional[str] = None


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


def _start_web_server_in_background(*, bind_host: str, bind_port: int, web_root_dir: Path, debug: bool) -> None:
    """Start the Flask server in a daemon thread."""

    import web_server

    web_server_config = web_server.WebServerConfig(
        host=str(bind_host),
        port=int(bind_port),
        web_root_dir=Path(web_root_dir),
        debug=bool(debug),
    )

    flask_app = web_server.create_flask_app(web_server_config)

    def run_server() -> None:
        flask_app.run(
            host=web_server_config.host,
            port=web_server_config.port,
            debug=web_server_config.debug,
            use_reloader=False,
            threaded=False,
        )

    server_thread = threading.Thread(target=run_server, name="steppy-web-server", daemon=True)
    server_thread.start()


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
        try:
            main_window.play()
        except Exception:
            pass

    elif state_value == "PAUSED":
        main_window.hide_idle()
        if status.video_id and status.video_id != runtime_state.last_video_id:
            main_window.load_video(status.video_id)
        try:
            main_window.pause()
        except Exception:
            pass

    runtime_state.last_state = state_value or "UNKNOWN"
    runtime_state.last_video_id = status.video_id
    runtime_state.last_seen_ok = bool(status.ok)
    runtime_state.last_error_text = status.error


def main() -> int:
    argument_parser = argparse.ArgumentParser(description="Steppy application")
    argument_parser.add_argument("--demo", action="store_true", help="Do not start web server or poll control API.")
    argument_parser.add_argument("--kiosk", action="store_true", help="Enable kiosk window flags and hide cursor.")
    argument_parser.add_argument("--fullscreen", action="store_true", help="Start in fullscreen.")
    argument_parser.add_argument("--web-debug", action="store_true", help="Enable Flask debug mode.")
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

    _start_web_server_in_background(
        bind_host=str(app_config.web_server.host),
        bind_port=int(app_config.web_server.port),
        web_root_dir=web_root_dir,
        debug=bool(parsed_args.web_debug),
    )

    control_client = ControlApiClient(
        host=str(app_config.web_server.host),
        port=int(app_config.web_server.port),
        timeout_seconds=0.20,
    )

    runtime_state = _RuntimeState()

    poll_timer = QTimer(main_window)
    poll_timer.setInterval(200)

    def poll_control_status() -> None:
        try:
            status = control_client.get_status()
        except ControlApiError:
            return
        except Exception:
            return

        if not status.ok:
            return

        try:
            _apply_status_to_window(main_window=main_window, status=status, runtime_state=runtime_state)
        except Exception:
            return

    poll_timer.timeout.connect(poll_control_status)
    poll_timer.start()

    time.sleep(0.05)

    return int(qt_application.exec())


if __name__ == "__main__":
    raise SystemExit(main())
