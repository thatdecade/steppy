# -*- coding: utf-8 -*-
from __future__ import annotations

########################
# web_server.py
########################
# Purpose:
# - Stable local Flask web server for remote control.
# - Serves static web assets and provides /api endpoints for play, pause, seek, load and status.
# - Integrates thumbnail caching and YouTube metadata search via thumb_cache.py and youtube_api.py.
#
# Design notes:
# - This module is considered stable and should remain unchanged if at all possible.
# - Other modules must adapt to this API, not the other way around.
# - SessionState is the server-side state machine; keep it thread-safe and explicit.
#
########################
# Interfaces:
# Public dataclasses:
# - WebServerConfig(host: str, port: int, web_root_dir: pathlib.Path, debug: bool)
#
# Public classes:
# - class SessionState
#   - snapshot() -> dict[str, Any]
#   - play(video_id: str, *, title: Optional[str] = None, channel_title: Optional[str] = None, thumbnail_url: Optional[str] = None,
#          duration_seconds: Optional[int] = None, difficulty: str = "easy") -> None
#   - pause() -> None
#   - resume() -> None
#   - restart() -> None
#   - stop() -> None
#   - set_difficulty(difficulty: str) -> None
#
# Public functions:
# - create_flask_app(config: WebServerConfig) -> flask.Flask
# - main() -> int
#
# Inputs:
# - HTTP requests from remote clients:
#   - /api/status (GET)
#   - /api/play, /api/pause, /api/resume, /api/restart, /api/stop (POST)
#   - /api/difficulty (POST)
#   - /api/search (GET) and related YouTube endpoints (if enabled)
# - thumb_cache.ThumbCache for URL rewriting and caching.
# - youtube_api.YouTubeApi for search and metadata.
#
# Outputs:
# - JSON responses (status and search results) and static file responses.
#
########################
# Tests:
#   - python web_server.py --host 0.0.0.0 --port 8080
########################

import argparse
import dataclasses
import mimetypes
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from flask import Flask, Response, jsonify, make_response, redirect, request, send_file

from thumb_cache import ThumbCache, ThumbCacheError, ThumbCacheUrlNotAllowedError
from youtube_api import YouTubeApi, YouTubeApiError
import config as app_config_module


@dataclass(frozen=True)
class WebServerConfig:
    host: str
    port: int
    web_root_dir: Path
    debug: bool = False


def _serialize_dataclass(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        result: dict[str, Any] = {}
        for field in dataclasses.fields(value):
            result[field.name] = _serialize_dataclass(getattr(value, field.name))
        return result
    if isinstance(value, (list, tuple)):
        return [_serialize_dataclass(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _serialize_dataclass(subvalue) for key, subvalue in value.items()}
    return value


def _is_within_directory(base_dir: Path, candidate_path: Path) -> bool:
    try:
        base_resolved = base_dir.resolve()
        candidate_resolved = candidate_path.resolve()
    except Exception:
        return False
    return base_resolved == candidate_resolved or str(candidate_resolved).startswith(str(base_resolved) + os.sep)


def _guess_mime_type(file_path: Path) -> str:
    guessed, _encoding = mimetypes.guess_type(str(file_path))
    if guessed:
        return guessed
    return "application/octet-stream"


class SessionState:
    """Server-side control state.

    Design intent:
    - This state tracks what was requested by the web controller.
    - Runtime truth (elapsed seconds, current difficulty, and optional mode overrides) is provided by bindings.

    This module intentionally does not compute elapsed time.
    The source of truth should be the desktop player.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()

        self._requested_state: str = "IDLE"
        self._runtime_state_override: Optional[str] = None

        self._video_id: Optional[str] = None
        self._video_title: Optional[str] = None
        self._channel_title: Optional[str] = None
        self._thumbnail_url: Optional[str] = None
        self._duration_seconds: Optional[int] = None

        self._requested_difficulty: str = "easy"

        self._error_text: Optional[str] = None

        # Bindings
        self._elapsed_seconds_provider: Optional[Callable[[], Optional[float]]] = None
        self._difficulty_getter: Optional[Callable[[], Optional[str]]] = None
        self._difficulty_setter: Optional[Callable[[str], None]] = None

    # Bindings

    def bind_elapsed_seconds_provider(self, provider: Optional[Callable[[], Optional[float]]]) -> None:
        with self._lock:
            self._elapsed_seconds_provider = provider

    def bind_difficulty_accessors(
        self,
        *,
        getter: Optional[Callable[[], Optional[str]]],
        setter: Optional[Callable[[str], None]],
    ) -> None:
        with self._lock:
            self._difficulty_getter = getter
            self._difficulty_setter = setter

    def set_runtime_state_override(self, state: Optional[str]) -> None:
        normalized_state = (state or "").strip().upper() or None
        with self._lock:
            self._runtime_state_override = normalized_state

    def set_error_text(self, error_text: Optional[str]) -> None:
        with self._lock:
            self._error_text = (error_text or "").strip() or None

    # Snapshot and transitions

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            requested_state = self._requested_state
            runtime_override = self._runtime_state_override
            video_id = self._video_id
            video_title = self._video_title
            channel_title = self._channel_title
            thumbnail_url = self._thumbnail_url
            duration_seconds = self._duration_seconds
            requested_difficulty = self._requested_difficulty
            elapsed_provider = self._elapsed_seconds_provider
            difficulty_getter = self._difficulty_getter
            error_text = self._error_text

        state_value = runtime_override or requested_state

        elapsed_seconds: float = 0.0
        if elapsed_provider is not None:
            try:
                value = elapsed_provider()
                if isinstance(value, (int, float)):
                    elapsed_seconds = float(value)
            except Exception:
                pass

        difficulty_value = requested_difficulty
        if difficulty_getter is not None:
            try:
                current_difficulty = difficulty_getter()
                if isinstance(current_difficulty, str) and current_difficulty.strip():
                    difficulty_value = current_difficulty.strip()
            except Exception:
                pass

        payload: dict[str, Any] = {
            "ok": True,
            "state": state_value,
            "video_id": video_id,
            "video_title": video_title,
            "channel_title": channel_title,
            "thumbnail_url": thumbnail_url,
            "duration_seconds": duration_seconds,
            "elapsed_seconds": elapsed_seconds,
            "difficulty": difficulty_value,
        }
        if error_text:
            payload["error"] = error_text
        return payload

    def play(
        self,
        video_id: str,
        *,
        title: Optional[str] = None,
        channel_title: Optional[str] = None,
        thumbnail_url: Optional[str] = None,
        duration_seconds: Optional[int] = None,
        difficulty: str = "easy",
    ) -> None:
        cleaned_video_id = str(video_id or "").strip()
        if not cleaned_video_id:
            return

        cleaned_difficulty = str(difficulty or "").strip() or "easy"

        with self._lock:
            self._requested_state = "PLAYING"
            self._video_id = cleaned_video_id
            self._video_title = str(title) if title is not None else None
            self._channel_title = str(channel_title) if channel_title is not None else None
            self._thumbnail_url = str(thumbnail_url) if thumbnail_url is not None else None
            self._duration_seconds = int(duration_seconds) if isinstance(duration_seconds, int) else None
            self._requested_difficulty = cleaned_difficulty
            self._error_text = None
            difficulty_setter = self._difficulty_setter

        if difficulty_setter is not None:
            try:
                difficulty_setter(cleaned_difficulty)
            except Exception:
                pass

    def pause(self) -> None:
        with self._lock:
            if self._requested_state != "PLAYING":
                return
            self._requested_state = "PAUSED"

    def resume(self) -> None:
        with self._lock:
            if self._requested_state != "PAUSED":
                return
            self._requested_state = "PLAYING"

    def restart(self) -> None:
        with self._lock:
            if self._requested_state not in ("PLAYING", "PAUSED"):
                return
            self._requested_state = "PLAYING"

    def stop(self) -> None:
        with self._lock:
            if self._requested_state == "IDLE":
                return
            self._requested_state = "IDLE"
            self._video_id = None
            self._video_title = None
            self._channel_title = None
            self._thumbnail_url = None
            self._duration_seconds = None
            self._error_text = None

    def set_difficulty(self, difficulty: str) -> None:
        cleaned_difficulty = str(difficulty or "").strip()
        if not cleaned_difficulty:
            return

        with self._lock:
            self._requested_difficulty = cleaned_difficulty
            difficulty_setter = self._difficulty_setter

        if difficulty_setter is not None:
            difficulty_setter(cleaned_difficulty)


def create_flask_app(config: WebServerConfig) -> Flask:
    flask_app = Flask(__name__, static_folder=None)

    session_state = SessionState()
    thumb_cache_instance = ThumbCache()

    flask_app.extensions["steppy_session_state"] = session_state
    flask_app.extensions["steppy_thumb_cache"] = thumb_cache_instance

    youtube_api_lock = threading.Lock()
    youtube_api_instance: Optional[YouTubeApi] = None
    youtube_api_init_error: Optional[str] = None

    def get_youtube_api() -> tuple[Optional[YouTubeApi], Optional[str]]:
        nonlocal youtube_api_instance, youtube_api_init_error

        if youtube_api_instance is not None:
            return youtube_api_instance, None
        if youtube_api_init_error is not None:
            return None, youtube_api_init_error

        with youtube_api_lock:
            if youtube_api_instance is not None:
                return youtube_api_instance, None
            if youtube_api_init_error is not None:
                return None, youtube_api_init_error
            try:
                app_config, _config_path = app_config_module.get_config()
                youtube_api_instance = YouTubeApi.from_app_config(app_config)
                return youtube_api_instance, None
            except YouTubeApiError as exception:
                youtube_api_init_error = str(exception)
                return None, youtube_api_init_error
            except Exception as exception:
                youtube_api_init_error = f"Failed to initialize YouTubeApi: {exception}"
                return None, youtube_api_init_error

    @flask_app.after_request
    def add_no_cache_headers(response: Response) -> Response:
        if request.path.startswith("/thumb"):
            return response
        response.headers["Cache-Control"] = "no-store"
        return response

    def not_found_response() -> Response:
        return jsonify({"ok": False, "error": "Not found"}), 404

    def serve_file_from_web_root(requested_path: str) -> Response:
        candidate_path = (config.web_root_dir / requested_path).resolve()
        if not _is_within_directory(config.web_root_dir, candidate_path):
            return not_found_response()
        if not candidate_path.exists() or not candidate_path.is_file():
            return not_found_response()
        mime_type = _guess_mime_type(candidate_path)
        return make_response(send_file(candidate_path, mimetype=mime_type))

    def maybe_wrap_thumbnail_url(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        cleaned = value.strip()
        if not cleaned:
            return value
        if cleaned.startswith("/thumb?url="):
            return cleaned
        try:
            return thumb_cache_instance.make_local_url(cleaned, route_path="/thumb")
        except ThumbCacheUrlNotAllowedError:
            return value

    def rewrite_thumbnail_urls_in_search_payload(payload: dict[str, Any]) -> dict[str, Any]:
        response_block = payload.get("response")
        if not isinstance(response_block, dict):
            return payload

        items = response_block.get("items")
        if not isinstance(items, list):
            return payload

        for item in items:
            if not isinstance(item, dict):
                continue
            if "thumbnail_url" in item:
                item["thumbnail_url"] = maybe_wrap_thumbnail_url(item.get("thumbnail_url"))
            thumbnails_value = item.get("thumbnails")
            if isinstance(thumbnails_value, list):
                for thumb in thumbnails_value:
                    if isinstance(thumb, dict) and "url" in thumb:
                        thumb["url"] = maybe_wrap_thumbnail_url(thumb.get("url"))
        return payload

    # Assets and static files

    @flask_app.get("/thumb")
    def route_thumb() -> Response:
        source_url = (request.args.get("url") or "").strip()
        if not source_url:
            return not_found_response()

        try:
            cached_thumbnail = thumb_cache_instance.get_or_fetch(source_url)
        except ThumbCacheUrlNotAllowedError:
            return not_found_response()
        except ThumbCacheError:
            return not_found_response()

        response = make_response(
            send_file(
                cached_thumbnail.file_path,
                mimetype=cached_thumbnail.content_type,
                conditional=True,
                etag=True,
                last_modified=cached_thumbnail.fetched_unix_seconds,
            )
        )
        response.headers["Cache-Control"] = "public, max-age=86400"
        return response

    @flask_app.get("/")
    def route_root() -> Response:
        return redirect("/controller.html")

    @flask_app.get("/index.html")
    def route_index_html() -> Response:
        return redirect("/controller.html")

    @flask_app.get("/controller")
    def route_controller() -> Response:
        return serve_file_from_web_root("controller.html")

    @flask_app.get("/controller.html")
    def route_controller_html() -> Response:
        return serve_file_from_web_root("controller.html")

    @flask_app.get("/search")
    def route_search() -> Response:
        return serve_file_from_web_root("search.html")

    @flask_app.get("/search.html")
    def route_search_html() -> Response:
        return serve_file_from_web_root("search.html")

    # API

    @flask_app.get("/api/status")
    def api_status() -> Response:
        return jsonify(session_state.snapshot())

    @flask_app.get("/api/search")
    def api_search() -> Response:
        query_text = (request.args.get("q") or "").strip()
        page_token = (request.args.get("page_token") or "").strip() or None

        youtube_api, init_error = get_youtube_api()
        if youtube_api is None:
            return jsonify({"ok": False, "error": init_error or "YouTube API not available"}), 503

        try:
            response = youtube_api.search_videos(query_text, page_token=page_token, max_results=20)
            payload = {"ok": True, "response": _serialize_dataclass(response)}
            payload = rewrite_thumbnail_urls_in_search_payload(payload)
            return jsonify(payload)
        except YouTubeApiError as exception:
            return jsonify({"ok": False, "error": str(exception)}), 502
        except Exception as exception:
            return jsonify({"ok": False, "error": f"Search failed: {exception}"}), 500

    @flask_app.post("/api/play")
    def api_play() -> Response:
        payload = request.get_json(silent=True) or {}

        video_id = str(payload.get("video_id") or "").strip()
        difficulty = str(payload.get("difficulty") or "").strip()
        video_title_value = payload.get("video_title")
        channel_title_value = payload.get("channel_title")
        thumbnail_url_value = payload.get("thumbnail_url")
        duration_seconds_value = payload.get("duration_seconds")

        duration_seconds: Optional[int] = None
        if isinstance(duration_seconds_value, int):
            duration_seconds = duration_seconds_value

        if isinstance(thumbnail_url_value, str):
            thumbnail_url_value = maybe_wrap_thumbnail_url(thumbnail_url_value)

        session_state.play(
            video_id,
            title=str(video_title_value) if video_title_value is not None else None,
            channel_title=str(channel_title_value) if channel_title_value is not None else None,
            thumbnail_url=str(thumbnail_url_value) if thumbnail_url_value is not None else None,
            duration_seconds=duration_seconds,
            difficulty=difficulty or "easy",
        )
        return jsonify({"ok": True})

    @flask_app.post("/api/pause")
    def api_pause() -> Response:
        session_state.pause()
        return jsonify({"ok": True})

    @flask_app.post("/api/resume")
    def api_resume() -> Response:
        session_state.resume()
        return jsonify({"ok": True})

    @flask_app.post("/api/restart")
    def api_restart() -> Response:
        session_state.restart()
        return jsonify({"ok": True})

    @flask_app.post("/api/stop")
    def api_stop() -> Response:
        session_state.stop()
        return jsonify({"ok": True})

    @flask_app.post("/api/difficulty")
    def api_difficulty() -> Response:
        payload = request.get_json(silent=True) or {}
        difficulty_value = str(payload.get("difficulty") or "").strip()
        try:
            session_state.set_difficulty(difficulty_value)
            return jsonify({"ok": True})
        except Exception as exception:
            return jsonify({"ok": False, "error": str(exception)}), 400

    # Static files fallback

    @flask_app.get("/<path:requested_path>")
    def route_static_files(requested_path: str) -> Response:
        return serve_file_from_web_root(requested_path)

    return flask_app


def _parse_args() -> WebServerConfig:
    argument_parser = argparse.ArgumentParser(description="Steppy local web server")
    argument_parser.add_argument("--host", default="0.0.0.0", help="Bind host, 0.0.0.0 for LAN access")
    argument_parser.add_argument("--port", type=int, default=8080, help="Bind port")
    argument_parser.add_argument(
        "--web-root",
        default=str(Path(__file__).resolve().parent / "assets"),
        help="Directory containing controller.html and static assets",
    )
    argument_parser.add_argument("--debug", action="store_true", help="Enable Flask debug mode")
    parsed = argument_parser.parse_args()

    return WebServerConfig(
        host=str(parsed.host),
        port=int(parsed.port),
        web_root_dir=Path(parsed.web_root).resolve(),
        debug=bool(parsed.debug),
    )


def main() -> int:
    config = _parse_args()
    flask_app = create_flask_app(config)

    # For future development you can enable threaded=True in app.run to allow concurrent requests.
    flask_app.run(host=config.host, port=config.port, debug=config.debug, use_reloader=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
