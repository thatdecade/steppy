from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from app_controller import AppController
from config import get_config
from control_api import ControlApiBridge
from main_window import MainWindow


def _resolve_web_root_dir() -> Path:
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


def main() -> int:
    argument_parser = argparse.ArgumentParser(description="Steppy application")
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

    web_root_dir = _resolve_web_root_dir()

    control_bridge = ControlApiBridge(
        bind_host=str(app_config.web_server.host),
        bind_port=int(app_config.web_server.port),
        web_root_dir=web_root_dir,
        debug=bool(parsed_args.web_debug),
        poll_interval_ms=int(parsed_args.poll_interval_ms),
        parent=main_window,
    )

    controller = AppController(main_window=main_window, control_bridge=control_bridge)
    controller.start()

    return int(qt_application.exec())


if __name__ == "__main__":
    raise SystemExit(main())
