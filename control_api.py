"""
control_api.py

Python client for the Steppy local control HTTP API.

Purpose
- Provide a small wrapper around the Flask endpoints exposed by web_server.py.
- Allow steppy.py to poll /api/status and translate state into MainWindow actions.

Notes
- This is a client only module. The server endpoints live in web_server.py.
- This module avoids third party HTTP dependencies.

Public API
- ControlApiClient
- ControlStatus
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional


class ControlApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class ControlStatus:
    ok: bool
    state: str
    video_id: Optional[str]
    video_title: Optional[str]
    channel_title: Optional[str]
    thumbnail_url: Optional[str]
    duration_seconds: Optional[int]
    elapsed_seconds: Optional[float]
    difficulty: Optional[str]
    error: Optional[str] = None

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ControlStatus":
        ok_value = bool(payload.get("ok", False))
        error_text = str(payload.get("error") or "").strip() or None

        state_value = str(payload.get("state") or "").strip() or "UNKNOWN"
        video_id_value = payload.get("video_id")
        video_title_value = payload.get("video_title")
        channel_title_value = payload.get("channel_title")
        thumbnail_url_value = payload.get("thumbnail_url")
        duration_seconds_value = payload.get("duration_seconds")
        elapsed_seconds_value = payload.get("elapsed_seconds")
        difficulty_value = payload.get("difficulty")

        duration_seconds_parsed: Optional[int] = None
        if isinstance(duration_seconds_value, int):
            duration_seconds_parsed = max(0, int(duration_seconds_value))

        elapsed_seconds_parsed: Optional[float] = None
        if isinstance(elapsed_seconds_value, (int, float)):
            elapsed_seconds_parsed = float(max(0.0, float(elapsed_seconds_value)))

        def normalize_optional_text(value: Any) -> Optional[str]:
            if not isinstance(value, str):
                return None
            trimmed = value.strip()
            return trimmed or None

        return cls(
            ok=ok_value,
            state=state_value,
            video_id=normalize_optional_text(video_id_value),
            video_title=normalize_optional_text(video_title_value),
            channel_title=normalize_optional_text(channel_title_value),
            thumbnail_url=normalize_optional_text(thumbnail_url_value),
            duration_seconds=duration_seconds_parsed,
            elapsed_seconds=elapsed_seconds_parsed,
            difficulty=normalize_optional_text(difficulty_value),
            error=error_text,
        )


def choose_loopback_host(bind_host: str) -> str:
    cleaned = (bind_host or "").strip().lower()
    if cleaned in ("0.0.0.0", "", "localhost", "127.0.0.1"):
        return "127.0.0.1"
    if cleaned in ("::", "::0", "[::]"):
        return "127.0.0.1"
    return bind_host


class ControlApiClient:
    def __init__(self, *, host: str, port: int, timeout_seconds: float = 0.25) -> None:
        self._host = str(host)
        self._port = int(port)
        self._timeout_seconds = float(max(0.05, timeout_seconds))

        base_host = choose_loopback_host(self._host)
        self._base_url = f"http://{base_host}:{self._port}"

    @property
    def base_url(self) -> str:
        return self._base_url

    def get_status(self) -> ControlStatus:
        payload = self._get_json("/api/status")
        if not isinstance(payload, dict):
            return ControlStatus(
                ok=False,
                state="ERROR",
                video_id=None,
                video_title=None,
                channel_title=None,
                thumbnail_url=None,
                duration_seconds=None,
                elapsed_seconds=None,
                difficulty=None,
                error="Invalid status payload",
            )
        return ControlStatus.from_dict(payload)

    def play(
        self,
        *,
        video_id: str,
        difficulty: str = "easy",
        video_title: Optional[str] = None,
        channel_title: Optional[str] = None,
        thumbnail_url: Optional[str] = None,
        duration_seconds: Optional[int] = None,
    ) -> None:
        self._post_json(
            "/api/play",
            {
                "video_id": str(video_id),
                "difficulty": str(difficulty),
                "video_title": video_title,
                "channel_title": channel_title,
                "thumbnail_url": thumbnail_url,
                "duration_seconds": duration_seconds,
            },
        )

    def pause(self) -> None:
        self._post_json("/api/pause", {})

    def resume(self) -> None:
        self._post_json("/api/resume", {})

    def restart(self) -> None:
        self._post_json("/api/restart", {})

    def stop(self) -> None:
        self._post_json("/api/stop", {})

    def set_difficulty(self, difficulty: str) -> None:
        self._post_json("/api/difficulty", {"difficulty": str(difficulty)})

    def _get_json(self, path: str) -> Any:
        request_url = self._base_url + path
        request_object = urllib.request.Request(request_url, method="GET")
        try:
            with urllib.request.urlopen(request_object, timeout=self._timeout_seconds) as response:
                raw_bytes = response.read()
        except (urllib.error.URLError, socket.timeout) as exception:
            raise ControlApiError(f"GET failed for {request_url}: {exception}") from exception

        try:
            return json.loads(raw_bytes.decode("utf-8"))
        except Exception as exception:
            raise ControlApiError(f"Failed to decode JSON from {request_url}: {exception}") from exception

    def _post_json(self, path: str, body: Dict[str, Any]) -> Any:
        request_url = self._base_url + path
        raw_body = json.dumps(body, ensure_ascii=True).encode("utf-8")
        request_object = urllib.request.Request(
            request_url,
            data=raw_body,
            method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )

        try:
            with urllib.request.urlopen(request_object, timeout=self._timeout_seconds) as response:
                raw_bytes = response.read()
        except (urllib.error.URLError, socket.timeout) as exception:
            raise ControlApiError(f"POST failed for {request_url}: {exception}") from exception

        try:
            payload = json.loads(raw_bytes.decode("utf-8"))
        except Exception:
            payload = None

        if isinstance(payload, dict) and payload.get("ok") is False:
            error_text = str(payload.get("error") or "").strip() or "Unknown error"
            raise ControlApiError(f"Request failed for {request_url}: {error_text}")

        return payload
