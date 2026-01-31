# -*- coding: utf-8 -*-
from __future__ import annotations

########################
# config.py
########################
# Purpose:
# - Typed configuration loading and validation for Steppy.
# - Loads exactly one UTF-8 JSON config file and validates with pydantic.
# - Supports environment variable overrides for key fields.
#
# Test Notes:
# - In this module, config is tested for web-control correctness:
#   - Web server host, port, and YouTube settings drive server and search behavior.
# - In Desktop shell and orchestration, it is tested for app startup correctness.
#
########################
# Design notes:
# - This module should be the only source of runtime configuration objects (AppConfig).
# - Validation errors must be explicit (no silent fallback).
# - Avoid extra side effects; only read config unless explicitly opening an editor file.
#
########################
# Interfaces:
# Public pydantic models:
# - class YouTubeConfig
# - class WebServerConfig
# - class AttractConfig
# - class AppConfig
#
# Public functions:
# - load_config() -> tuple[AppConfig, pathlib.Path]
# - get_config() -> tuple[AppConfig, pathlib.Path]
# - to_redacted_json(config: AppConfig) -> str
# - open_config_json_in_editor() -> pathlib.Path
# - main() -> int
#
########################

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from pydantic import BaseModel, Field, ValidationError


def _model_dump(model: BaseModel) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()  # type: ignore[attr-defined]
    return model.dict()  # type: ignore[attr-defined]


class YouTubeConfig(BaseModel):
    api_key: str = Field(default="")
    region_code: str = Field(default="US")
    language: str = Field(default="en")
    safe_search: str = Field(default="none")
    require_embeddable: bool = Field(default=True)
    cache_ttl_seconds: int = Field(default=3600)


class WebServerConfig(BaseModel):
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8080)
    web_root_dir: str = Field(default="assets")


class AttractConfig(BaseModel):
    playlist_id: str = Field(default="")
    mute: bool = Field(default=True)


class AppConfig(BaseModel):
    web_server: WebServerConfig = Field(default_factory=WebServerConfig)
    youtube: YouTubeConfig = Field(default_factory=YouTubeConfig)
    attract: AttractConfig = Field(default_factory=AttractConfig)


_CONFIG_CACHE: Optional[Tuple[AppConfig, Path]] = None


def _read_json_file_utf8(file_path: Path) -> Dict[str, Any]:
    try:
        raw_text = file_path.read_text(encoding="utf-8")
    except Exception as exception:
        raise ValueError(f"Failed to read config file as UTF-8: {file_path}: {exception}") from exception

    try:
        parsed = json.loads(raw_text)
    except Exception as exception:
        raise ValueError(f"Config file is not valid JSON: {file_path}: {exception}") from exception

    if not isinstance(parsed, dict):
        raise ValueError(f"Config JSON must be an object: {file_path}")
    return parsed


def _resolve_config_path() -> Path:
    env_path = (os.getenv("STEPPY_CONFIG_PATH") or "").strip()
    if env_path:
        return Path(env_path).expanduser().resolve()

    candidates = [
        Path.cwd() / "steppy_config.json",
        Path.cwd() / "config.json",
        Path(__file__).resolve().parent / "steppy_config.json",
        Path(__file__).resolve().parent / "config.json",
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return candidates[0]


def _apply_environment_overrides(json_dict: Dict[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Any] = json.loads(json.dumps(json_dict))

    web_block = merged.setdefault("web_server", {})
    youtube_block = merged.setdefault("youtube", {})
    attract_block = merged.setdefault("attract", {})

    web_host = (os.getenv("STEPPY_WEB_HOST") or "").strip()
    if web_host:
        web_block["host"] = web_host

    web_port = (os.getenv("STEPPY_WEB_PORT") or "").strip()
    if web_port.isdigit():
        web_block["port"] = int(web_port)

    youtube_api_key = (os.getenv("STEPPY_YOUTUBE_API_KEY") or "").strip()
    if youtube_api_key:
        youtube_block["api_key"] = youtube_api_key

    region_code = (os.getenv("STEPPY_YOUTUBE_REGION_CODE") or "").strip()
    if region_code:
        youtube_block["region_code"] = region_code

    language = (os.getenv("STEPPY_YOUTUBE_LANGUAGE") or "").strip()
    if language:
        youtube_block["language"] = language

    safe_search = (os.getenv("STEPPY_YOUTUBE_SAFE_SEARCH") or "").strip()
    if safe_search:
        youtube_block["safe_search"] = safe_search

    require_embeddable = (os.getenv("STEPPY_YOUTUBE_REQUIRE_EMBEDDABLE") or "").strip().lower()
    if require_embeddable in ("0", "1", "true", "false", "yes", "no"):
        youtube_block["require_embeddable"] = require_embeddable in ("1", "true", "yes")

    cache_ttl_text = (os.getenv("STEPPY_YOUTUBE_CACHE_TTL_SECONDS") or "").strip()
    if cache_ttl_text.isdigit():
        youtube_block["cache_ttl_seconds"] = int(cache_ttl_text)

    attract_playlist_id = (os.getenv("STEPPY_ATTRACT_PLAYLIST_ID") or "").strip()
    if attract_playlist_id:
        attract_block["playlist_id"] = attract_playlist_id

    attract_mute = (os.getenv("STEPPY_ATTRACT_MUTE") or "").strip().lower()
    if attract_mute in ("0", "1", "true", "false", "yes", "no"):
        attract_block["mute"] = attract_mute in ("1", "true", "yes")

    return merged


def load_config(config_path: Optional[Path] = None) -> Tuple[AppConfig, Path]:
    resolved_path = config_path if config_path is not None else _resolve_config_path()
    json_dict = _read_json_file_utf8(resolved_path)
    json_dict = _apply_environment_overrides(json_dict)

    try:
        config = AppConfig.model_validate(json_dict)  # type: ignore[attr-defined]
    except AttributeError:
        config = AppConfig.parse_obj(json_dict)  # type: ignore[attr-defined]
    except ValidationError as exception:
        raise ValueError(f"Config validation failed for {resolved_path}:\n{exception}") from exception

    return config, resolved_path


def get_config() -> Tuple[AppConfig, Path]:
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE
    config, config_path = load_config()
    _CONFIG_CACHE = (config, config_path)
    return config, config_path


def to_redacted_json(config: AppConfig) -> str:
    raw = _model_dump(config)
    youtube_block = raw.get("youtube")
    if isinstance(youtube_block, dict) and "api_key" in youtube_block:
        api_key_value = youtube_block.get("api_key") or ""
        if isinstance(api_key_value, str) and api_key_value:
            youtube_block["api_key"] = "REDACTED"
    return json.dumps(raw, indent=2, sort_keys=True)


def open_config_json_in_editor() -> Path:
    _config, config_path = get_config()
    try:
        if os.name == "nt":
            os.startfile(str(config_path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(config_path)])
        else:
            subprocess.Popen(["xdg-open", str(config_path)])
    except Exception:
        pass
    return config_path


def main() -> int:
    config, config_path = get_config()
    print(f"Loaded config: {config_path}")
    print(to_redacted_json(config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
