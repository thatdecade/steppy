"""\
theme.py

Theme asset resolution for Steppy.

Steppy uses a StepMania-style theme folder layout:

<themes_root_dir>/<theme_name>/
  BGAnimations/
  Fonts/
  Graphics/
  Languages/
  Other/
  Scripts/
  Sounds/
  ThemeInfo.ini
  metrics.ini

Steppy does not attempt to interpret metrics.ini or Lua scripts.
It only loads static assets (images, fonts, sounds) by file path.

Design goals
- Resolve a configured theme directory (with optional fallback)
- Provide simple helpers to locate assets by relative path
- Avoid importing Qt in this module
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

from config import AppConfig, get_config


@dataclass(frozen=True)
class ThemePaths:
    themes_root_dir: Path
    theme_dir: Path
    fallback_theme_dir: Optional[Path]
    config_path: Path


class ThemeError(RuntimeError):
    pass


class Theme:
    def __init__(self, theme_paths: ThemePaths) -> None:
        self._theme_paths = theme_paths

    @property
    def paths(self) -> ThemePaths:
        return self._theme_paths

    def resolve_asset_path(self, relative_path: str, *, allow_fallback: bool = True) -> Optional[Path]:
        """Return a filesystem path to an asset, or None if it does not exist."""
        normalized_path_text = (relative_path or "").replace("\\\\", "/").strip().lstrip("/")
        if not normalized_path_text:
            return None

        candidate_path = self._theme_paths.theme_dir / normalized_path_text
        if candidate_path.exists():
            return candidate_path

        if allow_fallback and self._theme_paths.fallback_theme_dir is not None:
            fallback_candidate_path = self._theme_paths.fallback_theme_dir / normalized_path_text
            if fallback_candidate_path.exists():
                return fallback_candidate_path

        return None

    def resolve_first_existing(self, relative_paths: Iterable[str], *, allow_fallback: bool = True) -> Optional[Path]:
        for relative_path in relative_paths:
            resolved_path = self.resolve_asset_path(relative_path, allow_fallback=allow_fallback)
            if resolved_path is not None:
                return resolved_path
        return None


def _resolve_theme_paths_from_config(app_config: AppConfig, config_path: Path) -> ThemePaths:
    themes_root_dir_text = (app_config.theme.themes_root_dir or "").strip() or "Themes"
    theme_name = (app_config.theme.theme_name or "").strip() or "SteppyDefault"
    fallback_theme_name = (app_config.theme.fallback_theme_name or "").strip() or ""

    config_directory = config_path.expanduser().resolve().parent

    themes_root_dir = Path(themes_root_dir_text)
    if not themes_root_dir.is_absolute():
        themes_root_dir = (config_directory / themes_root_dir).resolve()

    theme_dir = (themes_root_dir / theme_name).resolve()

    fallback_theme_dir: Optional[Path] = None
    if fallback_theme_name:
        candidate_fallback_theme_dir = (themes_root_dir / fallback_theme_name).resolve()
        if candidate_fallback_theme_dir.exists():
            fallback_theme_dir = candidate_fallback_theme_dir

    return ThemePaths(
        themes_root_dir=themes_root_dir,
        theme_dir=theme_dir,
        fallback_theme_dir=fallback_theme_dir,
        config_path=config_path,
    )


@lru_cache(maxsize=1)
def get_theme() -> Theme:
    try:
        app_config, config_path = get_config()
    except Exception as exception:
        raise ThemeError("Failed to load config for theme resolution: " + str(exception)) from exception
    theme_paths = _resolve_theme_paths_from_config(app_config, config_path)

    if not theme_paths.theme_dir.exists():
        raise ThemeError(
            "Theme directory does not exist: "
            + str(theme_paths.theme_dir)
            + " (themes_root_dir="
            + str(theme_paths.themes_root_dir)
            + ")"
        )

    return Theme(theme_paths)


def list_available_themes(*, include_hidden: bool = False) -> list[str]:
    try:
        app_config, config_path = get_config()
    except Exception:
        return []

    theme_paths = _resolve_theme_paths_from_config(app_config, config_path)

    if not theme_paths.themes_root_dir.exists():
        return []

    theme_names: list[str] = []
    for entry in sorted(theme_paths.themes_root_dir.iterdir()):
        if not entry.is_dir():
            continue
        if not include_hidden and entry.name.startswith("."):
            continue
        theme_names.append(entry.name)
    return theme_names
