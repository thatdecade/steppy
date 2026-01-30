#paths.py
from __future__ import annotations

import os
from pathlib import Path


def app_root_dir() -> Path:
    """Resolve the application root directory.

    - If STEPPY_ROOT_DIR is set, use it.
    - Else, walk upward from this file looking for common project folders.
    - Else, fall back to cwd.
    """
    environment_override = (os.environ.get("STEPPY_ROOT_DIR") or "").strip()
    if environment_override:
        return Path(environment_override).expanduser().resolve()

    try:
        this_file = Path(__file__).resolve()
    except Exception:
        return Path.cwd().resolve()

    candidate_directories = [this_file.parent, *list(this_file.parents)]
    for candidate in candidate_directories:
        if (candidate / "Charts").exists():
            return candidate
        if (candidate / "assets").exists():
            return candidate

    return Path.cwd().resolve()


def charts_dir() -> Path:
    return app_root_dir() / "Charts"


def charts_auto_dir() -> Path:
    return app_root_dir() / "ChartsAuto"
