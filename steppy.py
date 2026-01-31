# -*- coding: utf-8 -*-
########################
# steppy.py
########################
# Purpose:
# - Desktop application entrypoint.
# - Loads config, builds MainWindow, starts ControlApiBridge, and starts AppController.
#
# Design notes:
# - Keep CLI arguments limited to operational toggles (kiosk, fullscreen, debug, poll interval).
# - Treat web_server.py as stable. Use ControlApiBridge as the adapter.
#
########################
# Interfaces:
# Public functions:
# - build_argument_parser() -> argparse.ArgumentParser
# - main() -> int
#
# Inputs:
# - CLI args:
#   - --kiosk
#   - --fullscreen
#   - --web-debug
#   - --poll-interval-ms <int>
#
# Outputs:
# - Runs the Qt application event loop.
#
########################
# Main Entry:
# python steppy.py
########################

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QCoreApplication
from PyQt6.QtWidgets import QApplication

import config as config_module
import control_api
import main_window
import app_controller


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Steppy desktop application")
    parser.add_argument("--kiosk", action="store_true", help="Hide window chrome and reduce distractions.")
    parser.add_argument("--fullscreen", action="store_true", help="Start in fullscreen mode.")
    parser.add_argument("--web-debug", action="store_true", help="Run the embedded web server in debug mode.")
    parser.add_argument(
        "--poll-interval-ms",
        type=int,
        default=200,
        help="Polling interval for ControlApiBridge (milliseconds).",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Show the on-screen demo controls widget.",
    )
    return parser


def _resolve_web_root_dir() -> Path:
    """
    Resolve the directory that contains web assets served by the embedded Flask server.

    This is intentionally conservative:
    - If a 'web' directory exists next to this file, use it.
    - Otherwise, fall back to the project root.
    """
    entry_file_dir = Path(__file__).resolve().parent
    candidate_dir = entry_file_dir / "web"
    if candidate_dir.exists() and candidate_dir.is_dir():
        return candidate_dir
    return entry_file_dir


def main() -> int:
    args = build_argument_parser().parse_args()

    # This attribute reduces OpenGL related issues in some WebEngine environments.
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

    app_config, _config_path = config_module.get_config()

    application = QApplication(sys.argv)

    window = main_window.MainWindow()
    window.set_demo_mode_enabled(bool(args.demo))

    if args.kiosk:
        window.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)

    if args.fullscreen:
        window.showFullScreen()
    else:
        window.resize(1180, 900)
        window.show()

    control_bridge = control_api.ControlApiBridge(
        bind_host=str(app_config.web_server.host),
        bind_port=int(app_config.web_server.port),
        web_root_dir=_resolve_web_root_dir(),
        debug=bool(args.web_debug),
        poll_interval_ms=int(args.poll_interval_ms),
        parent=window,
    )
    control_bridge.start()

    controller = app_controller.AppController(main_window=window, control_bridge=control_bridge)
    controller.start()
    controller.set_demo_mode_enabled(bool(args.demo))

    return int(application.exec())


if __name__ == "__main__":
    raise SystemExit(main())
