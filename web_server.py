from __future__ import annotations

import argparse
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from flask import Flask, Response, abort, make_response, send_file


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


def create_flask_app(config: WebServerConfig) -> Flask:
    flask_app = Flask(__name__)
    flask_app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

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
        response = make_response(send_file(candidate_path, mimetype=mime_type))
        return response

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

    @flask_app.get("/<path:requested_path>")
    def route_static_files(requested_path: str) -> Response:
        return serve_file_from_web_root(requested_path)

    return flask_app


def _parse_args() -> WebServerConfig:
    argument_parser = argparse.ArgumentParser(description="Steppy local web server (Phase 1)")
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
