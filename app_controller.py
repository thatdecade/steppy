# -*- coding: utf-8 -*-
from __future__ import annotations

########################
# app_controller.py
########################
# Purpose:
# - High-level runtime coordinator for the desktop app.
# - In this standalone mode, it acts as an integration test runner for the Web control plane chunk.
#
# Design notes:
# - When run as a script, this module starts ControlApiBridge (which starts web_server.py) and then
#   exercises the HTTP endpoints using real HTTP requests.
# - Prints PASS/FAIL for each test and exits non-zero on failure.
#
########################
# Test coverage for this standalone mode:
# - Server state machine transitions and status snapshots are stable and thread-safe.
# - /api/status payload shape matches ControlStatus normalization expectations.
# - ControlApiBridge emits typed ControlStatus and only emits state/error changes when they change.
# - Thumb allowlist blocks unexpected URLs and local URL rewriting is correct.
# - YouTube API caching behavior works (prefer stubbed tests, opt-in live tests).
# - Control URL and QR generation are deterministic for a given host/port (optional).
#
########################

import argparse
import json
import os
import random
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import config as app_config_module
from control_api import ControlApiBridge, ControlStatus
from thumb_cache import ThumbCache, ThumbCacheUrlNotAllowedError
from youtube_api import JsonFileCache, NullCache, YouTubeApi, YouTubeApiError
from web_server import SessionState


@dataclass
class TestCounts:
    passed: int = 0
    failed: int = 0
    skipped: int = 0


class TestRunner:
    def __init__(self, *, verbose: bool) -> None:
        self._verbose = bool(verbose)
        self._counts = TestCounts()

    def counts(self) -> TestCounts:
        return self._counts

    def pass_test(self, name: str, detail: str = "") -> None:
        self._counts.passed += 1
        message = f"PASS {name}"
        if detail:
            message = message + f": {detail}"
        print(message)

    def fail_test(self, name: str, detail: str) -> None:
        self._counts.failed += 1
        print(f"FAIL {name}: {detail}")

    def skip_test(self, name: str, detail: str) -> None:
        self._counts.skipped += 1
        print(f"SKIP {name}: {detail}")

    def info(self, message: str) -> None:
        if self._verbose:
            print(f"INFO {message}")


def _http_get_json(url: str, *, timeout_seconds: float = 3.0) -> tuple[int, Any]:
    request_object = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request_object, timeout=timeout_seconds) as response:
            status_code = int(getattr(response, "status", 200))
            raw_bytes = response.read()
    except Exception as exception:
        raise RuntimeError(f"GET failed: {url}: {exception}") from exception

    try:
        decoded = raw_bytes.decode("utf-8", errors="replace")
        parsed = json.loads(decoded)
    except Exception as exception:
        raise RuntimeError(f"GET invalid JSON: {url}: {exception}") from exception

    return status_code, parsed


def _http_post_json(url: str, payload: Optional[dict[str, Any]] = None, *, timeout_seconds: float = 3.0) -> tuple[int, Any]:
    body_bytes = b""
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        body_bytes = json.dumps(payload).encode("utf-8")

    request_object = urllib.request.Request(url, data=body_bytes, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request_object, timeout=timeout_seconds) as response:
            status_code = int(getattr(response, "status", 200))
            raw_bytes = response.read()
    except Exception as exception:
        raise RuntimeError(f"POST failed: {url}: {exception}") from exception

    try:
        decoded = raw_bytes.decode("utf-8", errors="replace")
        parsed = json.loads(decoded) if decoded.strip() else {}
    except Exception as exception:
        raise RuntimeError(f"POST invalid JSON: {url}: {exception}") from exception

    return status_code, parsed


def _http_get_raw(url: str, *, timeout_seconds: float = 3.0) -> tuple[int, bytes]:
    request_object = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request_object, timeout=timeout_seconds) as response:
            status_code = int(getattr(response, "status", 200))
            raw_bytes = response.read()
    except urllib.error.HTTPError as exception:
        return int(exception.code), exception.read() or b""
    except Exception as exception:
        raise RuntimeError(f"GET failed: {url}: {exception}") from exception
    return status_code, raw_bytes


def _wait_for_status(base_url: str, *, timeout_seconds: float = 6.0) -> dict[str, Any]:
    deadline = time.time() + float(timeout_seconds)
    last_error: Optional[str] = None
    while time.time() < deadline:
        try:
            status_code, payload = _http_get_json(base_url + "/api/status", timeout_seconds=2.0)
            if status_code == 200 and isinstance(payload, dict):
                return payload
            last_error = f"Unexpected status {status_code}"
        except Exception as exception:
            last_error = str(exception)
        time.sleep(0.1)
    raise RuntimeError(f"Server did not become ready: {last_error or 'unknown error'}")


def _must_dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Expected dict")
    return value


def _safe_str(value: Any) -> str:
    if isinstance(value, str):
        return value
    return ""


def _make_minimal_config_json(web_root_dir: Path, host: str, port: int) -> dict[str, Any]:
    return {
        "web_server": {"host": host, "port": port, "web_root_dir": str(web_root_dir)},
        "youtube": {
            "api_key": os.getenv("STEPPY_YOUTUBE_API_KEY", ""),
            "region_code": os.getenv("STEPPY_YOUTUBE_REGION_CODE", "US"),
            "language": os.getenv("STEPPY_YOUTUBE_LANGUAGE", "en"),
            "safe_search": os.getenv("STEPPY_YOUTUBE_SAFE_SEARCH", "none"),
            "require_embeddable": True,
            "cache_ttl_seconds": 3600,
        },
        "attract": {"playlist_id": "", "mute": True},
    }


class _ElapsedProvider:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._value = 0.0

    def __call__(self) -> float:
        with self._lock:
            self._value += 1.0
            return self._value


class _DifficultyStore:
    def __init__(self, initial: str) -> None:
        self._lock = threading.Lock()
        self._value = initial

    def get(self) -> str:
        with self._lock:
            return self._value

    def set(self, difficulty: str) -> None:
        cleaned = str(difficulty or "").strip()
        if not cleaned:
            return
        with self._lock:
            self._value = cleaned


def _get_session_state_from_bridge(bridge: ControlApiBridge) -> SessionState:
    flask_app = bridge.flask_application()
    session_state = flask_app.extensions.get("steppy_session_state")
    if not isinstance(session_state, SessionState):
        raise RuntimeError("Could not access SessionState from Flask app extensions")
    return session_state

  

def _assert_status_shape(payload: dict[str, Any]) -> None:
    required_keys = [
        "ok",
        "state",
        "video_id",
        "video_title",
        "channel_title",
        "thumbnail_url",
        "duration_seconds",
        "elapsed_seconds",
        "difficulty",
    ]
    for key in required_keys:
        if key not in payload:
            raise AssertionError(f"Missing key: {key}")

    if not isinstance(payload.get("ok"), bool):
        raise AssertionError("ok must be bool")
    if not isinstance(payload.get("state"), str) or not payload.get("state"):
        raise AssertionError("state must be non-empty string")
    if not isinstance(payload.get("elapsed_seconds"), (int, float)):
        raise AssertionError("elapsed_seconds must be number")
    if not isinstance(payload.get("difficulty"), str) or not payload.get("difficulty"):
        raise AssertionError("difficulty must be non-empty string")


def _run_tests(
    *,
    runner: TestRunner,
    bridge: ControlApiBridge,
    base_url: str,
    live_youtube: bool,
    run_qr: bool,
) -> None:
    # Attach signal recording for ControlApiBridge behavior tests.
    received_statuses: list[ControlStatus] = []
    received_state_changes: list[str] = []
    received_error_changes: list[str] = []

    bridge.status_updated.connect(lambda status: received_statuses.append(status))
    bridge.state_changed.connect(lambda state: received_state_changes.append(str(state)))
    bridge.error_changed.connect(lambda error: received_error_changes.append(str(error)))

    # Ensure server is ready
    try:
        payload = _wait_for_status(base_url, timeout_seconds=8.0)
        _assert_status_shape(payload)
        runner.pass_test("boot.server_ready", "GET /api/status returned valid payload")
    except Exception as exception:
        runner.fail_test("boot.server_ready", str(exception))
        return

    # Access SessionState and bind runtime truth providers
    try:
        session_state = _get_session_state_from_bridge(bridge)
        elapsed_provider = _ElapsedProvider()
        difficulty_store = _DifficultyStore(initial="easy")

        session_state.bind_elapsed_seconds_provider(elapsed_provider)
        session_state.bind_difficulty_accessors(getter=difficulty_store.get, setter=difficulty_store.set)
        runner.pass_test("bindings.session_state", "bound elapsed and difficulty accessors")
    except Exception as exception:
        runner.fail_test("bindings.session_state", str(exception))
        return

    # Test 1.1: status shape and ControlStatus normalization
    try:
        _, status_payload = _http_get_json(base_url + "/api/status")
        status_dict = _must_dict(status_payload)
        _assert_status_shape(status_dict)

        normalized = ControlStatus.from_dict(status_dict)
        if not normalized.state:
            raise AssertionError("ControlStatus.state empty")
        runner.pass_test("status.shape_and_normalization", f"state={normalized.state}")
    except Exception as exception:
        runner.fail_test("status.shape_and_normalization", str(exception))

    # Test 5.1: elapsed_seconds comes from provider (should increment per call)
    try:
        _, p1 = _http_get_json(base_url + "/api/status")
        _, p2 = _http_get_json(base_url + "/api/status")
        v1 = float(_must_dict(p1)["elapsed_seconds"])
        v2 = float(_must_dict(p2)["elapsed_seconds"])
        if v2 <= v1:
            raise AssertionError(f"elapsed did not increase: {v1} then {v2}")
        runner.pass_test("elapsed.provider_truth", f"{v1} then {v2}")
    except Exception as exception:
        runner.fail_test("elapsed.provider_truth", str(exception))

    # Test 6.1 and 6.2: difficulty getter/setter and status reflection
    try:
        desired = "hard"
        _, resp = _http_post_json(base_url + "/api/difficulty", {"difficulty": desired})
        if not _must_dict(resp).get("ok"):
            raise AssertionError("POST /api/difficulty returned ok=false")

        _, status_payload = _http_get_json(base_url + "/api/status")
        current = _safe_str(_must_dict(status_payload).get("difficulty"))
        if current.strip() != desired:
            raise AssertionError(f"difficulty mismatch: expected {desired}, got {current}")
        runner.pass_test("difficulty.set_and_status_reflects", f"difficulty={current}")
    except Exception as exception:
        runner.fail_test("difficulty.set_and_status_reflects", str(exception))

    # Test 1.3: play transition updates snapshot and rewrites thumbnail
    play_video_id = "dQw4w9WgXcQ"
    play_payload = {
        "video_id": play_video_id,
        "difficulty": "easy",
        "video_title": "Test Title",
        "channel_title": "Test Channel",
        "thumbnail_url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
        "duration_seconds": 212,
    }
    try:
        _, play_resp = _http_post_json(base_url + "/api/play", play_payload)
        if not _must_dict(play_resp).get("ok"):
            raise AssertionError("POST /api/play returned ok=false")

        _, status_payload = _http_get_json(base_url + "/api/status")
        status_dict = _must_dict(status_payload)

        if _safe_str(status_dict.get("state")).upper() != "PLAYING":
            raise AssertionError(f"Expected state PLAYING, got {status_dict.get('state')}")
        if _safe_str(status_dict.get("video_id")) != play_video_id:
            raise AssertionError("video_id did not match")
        thumb_value = _safe_str(status_dict.get("thumbnail_url"))
        if not thumb_value.startswith("/thumb?url="):
            raise AssertionError(f"thumbnail_url not rewritten to /thumb: {thumb_value}")

        runner.pass_test("state.play_and_thumb_rewrite", "state=PLAYING and thumbnail_url is local /thumb")
    except Exception as exception:
        runner.fail_test("state.play_and_thumb_rewrite", str(exception))

    # Test 1.4 pause, 1.6 resume, 1.8 stop
    try:
        _http_post_json(base_url + "/api/pause", {})
        _, s1 = _http_get_json(base_url + "/api/status")
        if _safe_str(_must_dict(s1).get("state")).upper() != "PAUSED":
            raise AssertionError("Expected PAUSED after pause")

        _http_post_json(base_url + "/api/resume", {})
        _, s2 = _http_get_json(base_url + "/api/status")
        if _safe_str(_must_dict(s2).get("state")).upper() != "PLAYING":
            raise AssertionError("Expected PLAYING after resume")

        _http_post_json(base_url + "/api/stop", {})
        _, s3 = _http_get_json(base_url + "/api/status")
        s3d = _must_dict(s3)
        if _safe_str(s3d.get("state")).upper() != "IDLE":
            raise AssertionError("Expected IDLE after stop")
        if s3d.get("video_id") is not None:
            raise AssertionError("video_id should be None after stop")

        runner.pass_test("state.pause_resume_stop", "pause, resume, stop transitions ok")
    except Exception as exception:
        runner.fail_test("state.pause_resume_stop", str(exception))

    # Test 9.1: Learning state representation via runtime override
    try:
        session_state.set_runtime_state_override("LEARNING")
        _, s = _http_get_json(base_url + "/api/status")
        state_value = _safe_str(_must_dict(s).get("state")).upper()
        if state_value != "LEARNING":
            raise AssertionError(f"Expected LEARNING, got {state_value}")
        session_state.set_runtime_state_override(None)
        runner.pass_test("state.learning_override", "state=LEARNING when overridden")
    except Exception as exception:
        runner.fail_test("state.learning_override", str(exception))

    # Test 4: ControlApiBridge signal emission behavior
    try:
        received_statuses.clear()
        received_state_changes.clear()
        received_error_changes.clear()

        time.sleep(0.3)

        # Drive state changes through HTTP; polling should observe them.
        _http_post_json(base_url + "/api/play", play_payload)
        time.sleep(0.2)
        _http_post_json(base_url + "/api/pause", {})
        time.sleep(0.2)
        _http_post_json(base_url + "/api/resume", {})
        time.sleep(0.2)
        _http_post_json(base_url + "/api/stop", {})
        time.sleep(0.4)

        if len(received_statuses) < 2:
            raise AssertionError(f"Expected repeated status updates, got {len(received_statuses)}")

        # State changes should be close to the number of transitions.
        # Exact count can vary by initial state or timing, but it must not spam.
        # We require at least 2 and no more than 10 for this sequence.
        if len(received_state_changes) < 2 or len(received_state_changes) > 10:
            raise AssertionError(f"Unexpected state_changed count: {len(received_state_changes)}")

        runner.pass_test(
            "bridge.signals.state_and_status",
            f"status_updates={len(received_statuses)} state_changes={len(received_state_changes)}",
        )
    except Exception as exception:
        runner.fail_test("bridge.signals.state_and_status", str(exception))

    # Test 7: thumb allowlist and /thumb behavior
    try:
        thumb_cache = ThumbCache()
        allowed_url = "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"
        local_url = thumb_cache.make_local_url(allowed_url, route_path="/thumb")
        if not local_url.startswith("/thumb?url="):
            raise AssertionError("make_local_url did not return /thumb query URL")

        disallowed_url = "https://example.com/not_allowed.jpg"
        disallowed_raised = False
        try:
            thumb_cache.make_local_url(disallowed_url, route_path="/thumb")
        except ThumbCacheUrlNotAllowedError:
            disallowed_raised = True
        if not disallowed_raised:
            raise AssertionError("Expected allowlist rejection for example.com")

        # Verify server blocks disallowed URL
        disallowed_query = urllib.parse.urlencode({"url": disallowed_url})
        status_code, _raw = _http_get_raw(base_url + "/thumb?" + disallowed_query)
        if status_code != 404:
            raise AssertionError(f"Expected 404 for disallowed /thumb, got {status_code}")

        runner.pass_test("thumb.allowlist_and_server_block", "allowlist and /thumb rejection ok")
    except Exception as exception:
        runner.fail_test("thumb.allowlist_and_server_block", str(exception))

    # Test 2.1: basic concurrency (thread safety)
    try:
        errors: list[str] = []
        stop_flag = threading.Event()

        def reader_thread() -> None:
            while not stop_flag.is_set():
                try:
                    _http_get_json(base_url + "/api/status", timeout_seconds=2.0)
                except Exception as exception:
                    errors.append(str(exception))

        threads = [threading.Thread(target=reader_thread, daemon=True) for _i in range(6)]
        for t in threads:
            t.start()

        for _i in range(20):
            action = random.choice(["play", "pause", "resume", "stop"])
            try:
                if action == "play":
                    _http_post_json(base_url + "/api/play", play_payload, timeout_seconds=2.0)
                elif action == "pause":
                    _http_post_json(base_url + "/api/pause", {}, timeout_seconds=2.0)
                elif action == "resume":
                    _http_post_json(base_url + "/api/resume", {}, timeout_seconds=2.0)
                else:
                    _http_post_json(base_url + "/api/stop", {}, timeout_seconds=2.0)
            except Exception as exception:
                errors.append(str(exception))
            time.sleep(0.03)

        stop_flag.set()
        time.sleep(0.2)

        if errors:
            raise AssertionError(f"Concurrency errors: {errors[0]}")

        runner.pass_test("thread_safety.concurrent_status_reads", "no errors under concurrent reads and transitions")
    except Exception as exception:
        runner.fail_test("thread_safety.concurrent_status_reads", str(exception))

    # Test 8: YouTube API caching behavior (stubbed)
    try:
        call_count = {"count": 0}

        def fake_http_get_json(url: str) -> Any:
            call_count["count"] += 1

            if "youtube/v3/search" in url:
                return {
                    "items": [{"id": {"videoId": "abc123"}, "snippet": {"title": "x", "channelTitle": "c", "thumbnails": {}}}],
                    "pageInfo": {"totalResults": 1},
                }
            if "youtube/v3/videos" in url:
                return {
                    "items": [
                        {
                            "id": "abc123",
                            "snippet": {
                                "title": "Video Title",
                                "channelTitle": "Channel",
                                "thumbnails": {"default": {"url": "https://i.ytimg.com/vi/abc123/default.jpg", "width": 120, "height": 90}},
                            },
                            "contentDetails": {"duration": "PT3M32S"},
                        }
                    ]
                }
            raise YouTubeApiError(f"Unexpected URL in stub: {url}")

        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "yt_cache.json"
            cache = JsonFileCache(cache_path)

            api = YouTubeApi(
                api_key="stub-key",
                cache=cache,
                cache_ttl_seconds=3600,
                region_code="US",
                language="en",
                safe_search="none",
                require_embeddable=True,
                http_get_json=fake_http_get_json,
            )

            r1 = api.search_videos("hello", max_results=5)
            r2 = api.search_videos("hello", max_results=5)

            if not r1.items or not r2.items:
                raise AssertionError("Expected items from stubbed search")
            if call_count["count"] >= 4:
                raise AssertionError(f"Expected caching to reduce calls, call_count={call_count['count']}")

        runner.pass_test("youtube.stubbed_cache", f"call_count={call_count['count']}")
    except Exception as exception:
        runner.fail_test("youtube.stubbed_cache", str(exception))

    # Live /api/search test (opt-in)
    if live_youtube:
        api_key = (os.getenv("STEPPY_YOUTUBE_API_KEY") or "").strip()
        if not api_key:
            runner.skip_test("youtube.live_search", "STEPPY_YOUTUBE_API_KEY not set")
        else:
            try:
                query = urllib.parse.urlencode({"q": "steppy test"})
                _, payload = _http_get_json(base_url + "/api/search?" + query, timeout_seconds=6.0)
                payload_dict = _must_dict(payload)
                if not payload_dict.get("ok"):
                    raise AssertionError(f"/api/search ok=false: {payload_dict.get('error')}")
                response_block = payload_dict.get("response")
                if not isinstance(response_block, dict):
                    raise AssertionError("Missing response block")
                items = response_block.get("items")
                if not isinstance(items, list):
                    raise AssertionError("Missing items list")
                runner.pass_test("youtube.live_search", f"items={len(items)}")
            except Exception as exception:
                runner.fail_test("youtube.live_search", str(exception))
    else:
        runner.skip_test("youtube.live_search", "live tests not enabled")

    # QR tests (optional integration)
    if run_qr:
        try:
            import qr_code as qr_code_module  # type: ignore

            # Determinism for build_control_url
            app_config, _ = app_config_module.get_config()
            u1 = qr_code_module.build_control_url(app_config)
            u2 = qr_code_module.build_control_url(app_config)
            if u1 != u2:
                raise AssertionError("build_control_url not deterministic")
            runner.pass_test("qr.build_control_url_deterministic", u1)
        except Exception as exception:
            runner.fail_test("qr.build_control_url_deterministic", str(exception))
    else:
        runner.skip_test("qr.build_control_url_deterministic", "qr tests not enabled")


def _write_temp_config_if_needed(web_root_dir: Path, host: str, port: int, runner: TestRunner) -> Optional[Path]:
    # If STEPPY_CONFIG_PATH is set, respect it.
    if (os.getenv("STEPPY_CONFIG_PATH") or "").strip():
        runner.info("STEPPY_CONFIG_PATH already set; not writing temp config")
        return None

    try:
        config_payload = _make_minimal_config_json(web_root_dir, host, port)
        temp_dir = tempfile.mkdtemp(prefix="steppy_config_")
        config_path = Path(temp_dir) / "steppy_config.json"
        config_path.write_text(json.dumps(config_payload, indent=2, sort_keys=True), encoding="utf-8")
        os.environ["STEPPY_CONFIG_PATH"] = str(config_path)
        # Clear cached config if it was already loaded in this process.
        try:
            app_config_module._CONFIG_CACHE = None  # type: ignore[attr-defined]
        except Exception:
            pass
        runner.info(f"Wrote temp config: {config_path}")
        return config_path
    except Exception as exception:
        runner.info(f"Failed to write temp config: {exception}")
        return None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Steppy app_controller standalone test runner")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host for the web server")
    parser.add_argument("--port", type=int, default=8080, help="Bind port for the web server")
    parser.add_argument("--web-root", default=str(Path.cwd()), help="Directory containing controller.html, search.html, steppy.js")
    parser.add_argument("--poll-interval-ms", type=int, default=200, help="ControlApiBridge poll interval")
    parser.add_argument("--live-youtube", action="store_true", help="Enable live /api/search test (requires API key)")
    parser.add_argument("--run-qr", action="store_true", help="Enable QR determinism check if qr_code module is available")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    runner = TestRunner(verbose=bool(args.verbose))

    host = str(args.host)
    port = int(args.port)
    web_root_dir = Path(args.web_root).resolve()
    
    _write_temp_config_if_needed(web_root_dir, host, port, runner)

    bridge = ControlApiBridge(
        bind_host=host,
        bind_port=port,
        web_root_dir=web_root_dir,
        debug=False,
        poll_interval_ms=int(args.poll_interval_ms),
        parent=None,
    )

    # Start server and polling
    bridge.start()

    base_url = f"http://{host}:{port}"
    _run_tests(
        runner=runner,
        bridge=bridge,
        base_url=base_url,
        live_youtube=bool(args.live_youtube),
        run_qr=bool(args.run_qr),
    )

    counts = runner.counts()
    print(f"TOTAL: {counts.passed} passed, {counts.failed} failed, {counts.skipped} skipped")
    return 0 if counts.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
