"""
config.py

Typed configuration loading and validation for Steppy.

Design goals
- Load exactly one UTF-8 JSON config file
- Validate with pydantic (defaults included)
- Support environment variable overrides
- No other I/O beyond reading the config file (no directory creation)

How to obtain a YouTube Data API key
1) Go to Google Cloud Console.
2) Create (or select) a project.
3) Enable "YouTube Data API v3" for the project.
4) Create an API key in "APIs & Services" -> "Credentials".
5) Put the key in your Steppy config file under: youtube.api_key

Config file location
- If STEPPY_CONFIG_PATH is set, that file is used.
- Otherwise Steppy searches these paths in order and uses the first one that exists:
  1) ./steppy_config.json (current working directory)
  2) <user config dir>/Steppy/Steppy/steppy_config.json
  3) <user config dir>/Steppy/Steppy/config.json

Example config file (steppy_config.json)
{
  "youtube": {
    "api_key": "YOUR_KEY_HERE",
    "region_code": "US",
    "language": "en",
    "safe_search": "none",
    "require_embeddable": true,
    "cache_ttl_seconds": 86400
  },
  "web_server": {
    "host": "0.0.0.0",
    "port": 5177
  },
  "attract": {
    "playlist_id": "",
    "mute": true
  }
}
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from platformdirs import user_config_dir
from pydantic import BaseModel, Field, ValidationError, field_validator


class YouTubeConfig(BaseModel):
    api_key: str = Field(default="", description="YouTube Data API v3 key from Google Cloud Console.")
    region_code: Optional[str] = Field(default=None, description="Example: US")
    language: Optional[str] = Field(default=None, description="Example: en")
    safe_search: str = Field(default="none", description="none, moderate, or strict")
    require_embeddable: bool = Field(default=True, description="Filter search results to embeddable videos.")
    cache_ttl_seconds: int = Field(default=24 * 60 * 60, ge=0, description="Cache time to live in seconds.")

    @field_validator("safe_search")
    @classmethod
    def validate_safe_search(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        allowed = {"none", "moderate", "strict"}
        if normalized not in allowed:
            raise ValueError("safe_search must be one of: none, moderate, strict")
        return normalized

    @field_validator("region_code", "language")
    @classmethod
    def normalize_optional_strings(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        trimmed = value.strip()
        return trimmed or None


class WebServerConfig(BaseModel):
    host: str = Field(default="0.0.0.0", description="Bind address for local web server.")
    port: int = Field(default=5177, ge=1, le=65535, description="Port for local web server.")


class AttractConfig(BaseModel):
    playlist_id: str = Field(default="", description="Optional YouTube playlist id for attract mode.")
    mute: bool = Field(default=True, description="Mute attract playback.")


class AppConfig(BaseModel):
    youtube: YouTubeConfig = Field(default_factory=YouTubeConfig)
    web_server: WebServerConfig = Field(default_factory=WebServerConfig)
    attract: AttractConfig = Field(default_factory=AttractConfig)


def _default_config_candidates() -> List[Path]:
    config_directory = Path(user_config_dir("Steppy", "Steppy"))
    return [
        Path.cwd() / "steppy_config.json",
        config_directory / "steppy_config.json",
        config_directory / "config.json",
    ]


def _resolve_config_path() -> Path:
    explicit_path_text = os.environ.get("STEPPY_CONFIG_PATH", "").strip()
    if explicit_path_text:
        return Path(explicit_path_text)

    for candidate_path in _default_config_candidates():
        if candidate_path.exists():
            return candidate_path

    candidates_text = "\n".join("  - " + str(path) for path in _default_config_candidates())
    raise FileNotFoundError(
        "No Steppy config file found. Create steppy_config.json in one of these locations:\n" + candidates_text
    )


def _read_json_file_utf8(config_path: Path) -> Dict[str, Any]:
    try:
        raw_text = config_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise
    except OSError as exception:
        raise OSError(f"Failed to read config file: {config_path}. Error: {exception}") from exception

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exception:
        raise ValueError(f"Config file is not valid JSON: {config_path}. Error: {exception}") from exception

    if not isinstance(parsed, dict):
        raise ValueError(f"Config file root must be a JSON object: {config_path}")

    return parsed


def _apply_environment_overrides(config_dict: Dict[str, Any]) -> Dict[str, Any]:
    """
    Environment overrides are optional. The config file is the primary source of truth.

    Override variables:
    - STEPPY_YOUTUBE_API_KEY
    - STEPPY_YOUTUBE_REGION_CODE
    - STEPPY_YOUTUBE_LANGUAGE
    - STEPPY_YOUTUBE_SAFE_SEARCH
    - STEPPY_YOUTUBE_REQUIRE_EMBEDDABLE
    - STEPPY_YOUTUBE_CACHE_TTL_SECONDS
    - STEPPY_WEB_HOST
    - STEPPY_WEB_PORT
    - STEPPY_ATTRACT_PLAYLIST_ID
    - STEPPY_ATTRACT_MUTE
    """
    def ensure_nested(config_root: Dict[str, Any], section_name: str) -> Dict[str, Any]:
        section = config_root.get(section_name)
        if isinstance(section, dict):
            return section
        section = {}
        config_root[section_name] = section
        return section

    updated_config = dict(config_dict)

    youtube_section = ensure_nested(updated_config, "youtube")
    web_server_section = ensure_nested(updated_config, "web_server")
    attract_section = ensure_nested(updated_config, "attract")

    def override_string(env_name: str, target_dict: Dict[str, Any], key_name: str) -> None:
        value_text = os.environ.get(env_name, "")
        if value_text.strip():
            target_dict[key_name] = value_text.strip()

    def override_int(env_name: str, target_dict: Dict[str, Any], key_name: str) -> None:
        value_text = os.environ.get(env_name, "").strip()
        if not value_text:
            return
        try:
            target_dict[key_name] = int(value_text)
        except ValueError:
            return

    def override_bool(env_name: str, target_dict: Dict[str, Any], key_name: str) -> None:
        value_text = os.environ.get(env_name, "").strip().lower()
        if not value_text:
            return
        truthy = {"1", "true", "yes", "on"}
        falsy = {"0", "false", "no", "off"}
        if value_text in truthy:
            target_dict[key_name] = True
        elif value_text in falsy:
            target_dict[key_name] = False

    override_string("STEPPY_YOUTUBE_API_KEY", youtube_section, "api_key")
    override_string("STEPPY_YOUTUBE_REGION_CODE", youtube_section, "region_code")
    override_string("STEPPY_YOUTUBE_LANGUAGE", youtube_section, "language")
    override_string("STEPPY_YOUTUBE_SAFE_SEARCH", youtube_section, "safe_search")
    override_bool("STEPPY_YOUTUBE_REQUIRE_EMBEDDABLE", youtube_section, "require_embeddable")
    override_int("STEPPY_YOUTUBE_CACHE_TTL_SECONDS", youtube_section, "cache_ttl_seconds")

    override_string("STEPPY_WEB_HOST", web_server_section, "host")
    override_int("STEPPY_WEB_PORT", web_server_section, "port")

    override_string("STEPPY_ATTRACT_PLAYLIST_ID", attract_section, "playlist_id")
    override_bool("STEPPY_ATTRACT_MUTE", attract_section, "mute")

    return updated_config


def load_config(config_path: Optional[Path] = None) -> Tuple[AppConfig, Path]:
    resolved_path = config_path if config_path is not None else _resolve_config_path()
    json_dict = _read_json_file_utf8(resolved_path)
    json_dict = _apply_environment_overrides(json_dict)

    try:
        config = AppConfig.model_validate(json_dict)
    except ValidationError as exception:
        raise ValueError(f"Config validation failed for {resolved_path}:\n{exception}") from exception

    return config, resolved_path


@lru_cache(maxsize=1)
def get_config() -> Tuple[AppConfig, Path]:
    return load_config()


def to_redacted_json(config: AppConfig) -> str:
    config_dict = config.model_dump()
    youtube_section = config_dict.get("youtube")
    if isinstance(youtube_section, dict):
        api_key_value = str(youtube_section.get("api_key") or "")
        youtube_section["api_key"] = "(set)" if api_key_value else "(missing)"
    return json.dumps(config_dict, ensure_ascii=False, indent=2)

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional


def open_config_json_in_editor(config_path: Optional[Path] = None) -> Path:
    if config_path is None:
        _config, resolved_path = get_config()
    else:
        resolved_path = Path(config_path)

    resolved_path = resolved_path.expanduser().resolve()
    resolved_path.parent.mkdir(parents=True, exist_ok=True)

    if not resolved_path.exists():
        resolved_path.write_text("{}", encoding="utf-8")

    if sys.platform.startswith("win"):
        os.startfile(str(resolved_path))
    elif sys.platform == "darwin":
        subprocess.run(["open", str(resolved_path)], check=False)
    else:
        subprocess.run(["xdg-open", str(resolved_path)], check=False)

    return resolved_path


def main() -> int:
    try:
        config, resolved_path = load_config()
    except Exception as exception:
        error_payload = {"ok": False, "error": str(exception)}
        print(json.dumps(error_payload, ensure_ascii=False, indent=2))
        return 2

    output_payload = {
        "ok": True,
        "config_path": str(resolved_path),
        "config": json.loads(to_redacted_json(config)),
    }
    print(json.dumps(output_payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
