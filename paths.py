# -*- coding: utf-8 -*-
########################
# paths.py
########################
# Purpose:
# - Central filesystem path helpers for the app.
# - Defines where charts and auto-generated charts live relative to the project root.
#
# Design notes:
# - Keep path derivation consistent across modules.
# - No Qt usage. Return pathlib.Path only.
#
########################
# Interfaces:
# Public functions:
# - app_root_dir() -> pathlib.Path
# - charts_dir() -> pathlib.Path
# - charts_auto_dir() -> pathlib.Path
#
# Inputs:
# - None (derived from the launched Python entrypoint file location).
#
# Outputs:
# - Paths used by library_index.py and chart_engine.py.
#
########################

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional


def _entrypoint_file_path() -> Optional[Path]:
    """Best-effort resolution of the launched Python entrypoint file.

    This follows the project rule for this chunk:
    Use the directory of the .py file launched as the application root.
    """
    main_module = sys.modules.get("__main__")
    main_file = getattr(main_module, "__file__", None)
    if main_file:
        return Path(str(main_file)).resolve()

    argv0 = str(sys.argv[0] or "").strip()
    if argv0 and argv0 not in {"-c", "-m"}:
        try:
            return Path(argv0).resolve()
        except Exception:
            return None

    return None


def app_root_dir() -> Path:
    """Return the application root directory for local chart storage.

    For this chunk, the root is the directory containing the launched .py file.
    """
    entrypoint_path = _entrypoint_file_path()
    if entrypoint_path is not None:
        return entrypoint_path.parent

    return Path.cwd().resolve()


def charts_dir() -> Path:
    """Return the curated charts root directory (not created automatically)."""
    return app_root_dir() / "Charts"


def charts_auto_dir() -> Path:
    """Return the auto charts root directory (not created automatically)."""
    return app_root_dir() / "ChartsAuto"
