from __future__ import annotations

import argparse
import mimetypes
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from flask import Flask, Response, abort, jsonify, make_response, request, send_file

from youtube_api import YouTubeApi, YouTubeApiError


@dataclass(frozen=True)
class WebServerConfig:
    host: str
    port: int
    web_root_dir: Path
    debug: bool


def _is_within_directory(web_root_dir: Path, candidate_path: Path) -> bool:
    try:
        web_root_resolved = web_root_dir.resolve(strict=True)
        candidate_resolved = candidate_path.resolve(strict=True)
    except FileNotFoundError:
        return False
    return web_root_resolved == candidate_resolved or web_root_resolved in candidate_resolved.parents


def _guess_mime_type(file_path: Path) -> Optional[str]:
    mime_type, _encoding = mimetypes.guess_type(str(file_path))
    return mime_type


def _serialize_dataclass(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        output: dict[str, Any] = {}
        for field_name in getattr(value, "__dataclass_fields__", {}).keys():
            output[field_name] = _serialize_dataclass(getattr(value, field_name))
        return output

    if isinstance(value, list):
        return [_serialize_dataclass(item) for item in value]

    if isinstance(value, dict):
        return {str(key): _serialize_dataclass(value_item) for key, value_item in value.items()}

    return value


class SessionState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = "IDLE"
        self._video_id: Optional[str] = None
        self._video_title: Optional[str] = None
        self._channel_title: Optional[str] = None
        self._thumbnail_url: Optional[str] = None
        self._duration_seconds: Optional[int] = None
        self._difficulty = "easy"
        self._start_monotonic: Optional[float] = None
        self._pause_started_monotonic: Optional[float] = None
        self._paused_accumulated_seconds = 0.0

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            elapsed_seconds = self._compute_elapsed_seconds_locked()
            return {
                "ok": True,
                "state": self._state,
                "video_id": self._video_id,
                "video_title": self._video_title,
                "channel_title": self._channel_title,
                "thumbnail_url": self._thumbnail_url,
                "duration_seconds": self._duration_seconds,
                "elapsed_seconds": elapsed_seconds,
                "difficulty": self._difficulty,
            }

    def play(
        self,
        *,
        video_id: str,
        difficulty: str,
        video_title: Optional[str] = None,
        channel_title: Optional[str] = None,
        thumbnail_url: Optional[str] = None,
        duration_seconds: Optional[int] = None,
    ) -> None:
        cleaned_video_id = (video_id or "").strip()
        if not cleaned_video_id:
            raise ValueError("video_id is required")

        cleaned_difficulty = (difficulty or "").strip().lower()
        if cleaned_difficulty not in ("easy", "medium", "hard"):
            cleaned_difficulty = "easy"

        duration_value: Optional[int] = None
        if isinstance(duration_seconds, int):
            duration_value = max(0, int(duration_seconds))

        with self._lock:
            self._state = "PLAYING"
            self._video_id = cleaned_video_id
            self._video_title = (video_title or "").strip() or None
            self._channel_title = (channel_title or "").strip() or None
            self._thumbnail_url = (thumbnail_url or "").strip() or None
            self._duration_seconds = duration_value
            self._difficulty = cleaned_difficulty
            self._start_monotonic = time.monotonic()
            self._pause_started_monotonic = None
            self._paused_accumulated_seconds = 0.0

    def pause(self) -> None:
        with self._lock:
            if self._state != "PLAYING":
                return
            self._state = "PAUSED"
            self._pause_started_monotonic = time.monotonic()

    def resume(self) -> None:
        with self._lock:
            if self._state != "PAUSED":
                return
            if self._pause_started_monotonic is not None:
                self._paused_accumulated_seconds += max(0.0, time.monotonic() - self._pause_started_monotonic)
            self._pause_started_monotonic = None
            self._state = "PLAYING"

    def restart(self) -> None:
        with self._lock:
            if self._video_id is None:
                return
            self._state = "PLAYING"
            self._start_monotonic = time.monotonic()
            self._pause_started_monotonic = None
            self._paused_accumulated_seconds = 0.0

    def stop(self) -> None:
        with self._lock:
            self._state = "IDLE"
            self._video_id = None
            self._video_title = None
            self._channel_title = None
            self._thumbnail_url = None
            self._duration_seconds = None
            self._start_monotonic = None
            self._pause_started_monotonic = None
            self._paused_accumulated_seconds = 0.0

    def set_difficulty(self, difficulty: str) -> None:
        cleaned_difficulty = (difficulty or "").strip().lower()
        if cleaned_difficulty not in ("easy", "medium", "hard"):
            raise ValueError("difficulty must be easy, medium, or hard")
        with self._lock:
            self._difficulty = cleaned_difficulty

    def _compute_elapsed_seconds_locked(self) -> float:
        if self._start_monotonic is None:
            return 0.0
        now = time.monotonic()
        paused_total = self._paused_accumulated_seconds
        if self._pause_started_monotonic is not None:
            paused_total += max(0.0, now - self._pause_started_monotonic)
        elapsed = max(0.0, (now - self._start_monotonic) - paused_total)
        return float(elapsed)


def create_flask_app(config: WebServerConfig) -> Flask:
    flask_app = Flask(__name__)
    flask_app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

    session_state = SessionState()

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
                youtube_api_instance = YouTubeApi.from_config_file()
                return youtube_api_instance, None
            except YouTubeApiError as exception:
                youtube_api_init_error = str(exception)
                return None, youtube_api_init_error
            except Exception as exception:
                youtube_api_init_error = f"Failed to initialize YouTubeApi: {exception}"
                return None, youtube_api_init_error

    @flask_app.after_request
    def add_no_cache_headers(response: Response) -> Response:
        response.headers["Cache-Control"] = "no-store"
        return response

    def serve_file_from_web_root(relative_path: str) -> Response:
        normalized_relative_path = relative_path.lstrip("/")
        candidate_path = config.web_root_dir / normalized_relative_path

        if not _is_within_directory(config.web_root_dir, candidate_path):
            abort(404)

        if not candidate_path.exists() or not candidate_path.is_file():
            abort(404)

        mime_type = _guess_mime_type(candidate_path)
        return make_response(send_file(candidate_path, mimetype=mime_type))

    # -------------------------
    # PWA disable routes
    # -------------------------

    @flask_app.get("/service-worker.js")
    def route_disable_service_worker() -> Response:
        """
        Serves a self-unregistering service worker so the UI behaves like a normal web app.

        This disables offline caching and reduces development confusion when assets change.
        """
        script_lines = [
            "self.addEventListener(\"install\", function () {",
            "  self.skipWaiting();",
            "});",
            "self.addEventListener(\"activate\", function (event) {",
            "  event.waitUntil(",
            "    self.registration.unregister().then(function () {",
            "      return self.clients.matchAll({ type: \"window\" });",
            "    }).then(function (clients) {",
            "      clients.forEach(function (client) {",
            "        try { client.navigate(client.url); } catch (e) { }",
            "      });",
            "    })",
            "  );",
            "});",
        ]
        response = make_response("\n".join(script_lines), 200)
        response.headers["Content-Type"] = "application/javascript; charset=utf-8"
        response.headers["Cache-Control"] = "no-store"
        return response

    @flask_app.get("/manifest.json")
    def route_disable_manifest() -> Response:
        abort(404)

    # -------------------------
    # Pages
    # -------------------------

    @flask_app.get("/")
    def route_root() -> Response:
        return serve_file_from_web_root("index.html")

    @flask_app.get("/index.html")
    def route_index_html() -> Response:
        return serve_file_from_web_root("index.html")

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

    # -------------------------
    # API
    # -------------------------

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
            return jsonify({"ok": True, "response": _serialize_dataclass(response)})
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

        try:
            session_state.play(
                video_id=video_id,
                difficulty=difficulty,
                video_title=str(video_title_value) if isinstance(video_title_value, str) else None,
                channel_title=str(channel_title_value) if isinstance(channel_title_value, str) else None,
                thumbnail_url=str(thumbnail_url_value) if isinstance(thumbnail_url_value, str) else None,
                duration_seconds=duration_seconds,
            )
            return jsonify({"ok": True})
        except Exception as exception:
            return jsonify({"ok": False, "error": str(exception)}), 400

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
        difficulty = str(payload.get("difficulty") or "").strip()
        try:
            session_state.set_difficulty(difficulty)
            return jsonify({"ok": True})
        except Exception as exception:
            return jsonify({"ok": False, "error": str(exception)}), 400

    # -------------------------
    # Static files fallback
    # -------------------------

    @flask_app.get("/<path:requested_path>")
    def route_static_files(requested_path: str) -> Response:
        return serve_file_from_web_root(requested_path)

    return flask_app


def _parse_args() -> WebServerConfig:
    argument_parser = argparse.ArgumentParser(description="Steppy local web server (Phase 2)")
    argument_parser.add_argument("--host", default="0.0.0.0", help="Bind host, 0.0.0.0 for LAN access")
    argument_parser.add_argument("--port", type=int, default=8080, help="Bind port")
    argument_parser.add_argument(
        "--web-root",
        default=str(Path(__file__).resolve().parent / "assets"),
        help="Directory containing index.html and static assets",
    )
    argument_parser.add_argument("--debug", action="store_true", help="Enable Flask debug mode")
    parsed_args = argument_parser.parse_args()

    web_root_dir = Path(parsed_args.web_root)
    if not web_root_dir.exists() or not web_root_dir.is_dir():
        raise SystemExit(f"web root directory not found: {web_root_dir}")

    return WebServerConfig(
        host=str(parsed_args.host),
        port=int(parsed_args.port),
        web_root_dir=web_root_dir,
        debug=bool(parsed_args.debug),
    )


def main() -> None:
    config = _parse_args()
    flask_app = create_flask_app(config)

    flask_app.run(
        host=config.host,
        port=config.port,
        debug=config.debug,
        use_reloader=False,
        threaded=True,
    )


if __name__ == "__main__":
    main()
