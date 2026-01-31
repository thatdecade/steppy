# -*- coding: utf-8 -*-
from __future__ import annotations

########################
# thumb_cache.py
########################
# Purpose:
# - Stable thumbnail download and caching.
# - Downloads allowed thumbnail URLs and stores them in a local cache directory.
# - Exposes local URLs suitable for web_server.py to serve.
#
# Design notes:
# - This module is a critical dependency for gameplay_harness.py and web_server.py integrations.
# - Strict allowlist: reject unexpected domains rather than fetching arbitrary URLs.
# - Concurrency: safe for multi-thread usage via internal locking.
#
########################
# Interfaces:
# Public exceptions:
# - class ThumbCacheError(Exception)
# - class ThumbCacheUrlNotAllowedError(ThumbCacheError)
# - class ThumbCacheDownloadError(ThumbCacheError)
#
# Public dataclasses:
# - CachedThumbnail(file_path: pathlib.Path, url: str, fetched_unix_seconds: float, content_type: str)
#
# Public classes:
# - class ThumbCache
#   - cache_dir() -> pathlib.Path
#   - make_local_url(thumbnail_url: str, *, route_path: str = "/thumb") -> str
#   - get_or_fetch(thumbnail_url: str) -> CachedThumbnail
#   - clear() -> None
#
########################

import hashlib
import json
import os
import shutil
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple


class ThumbCacheError(Exception):
    pass


class ThumbCacheUrlNotAllowedError(ThumbCacheError):
    pass


class ThumbCacheDownloadError(ThumbCacheError):
    pass


@dataclass(frozen=True)
class CachedThumbnail:
    file_path: Path
    url: str
    fetched_unix_seconds: float
    content_type: str


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_content_type(content_type_value: str) -> str:
    cleaned = (content_type_value or "").strip().lower()
    if ";" in cleaned:
        cleaned = cleaned.split(";", 1)[0].strip()
    return cleaned


def _extension_for_content_type(content_type: str) -> str:
    if content_type == "image/png":
        return ".png"
    if content_type == "image/webp":
        return ".webp"
    if content_type == "image/gif":
        return ".gif"
    return ".jpg"


def _safe_guess_extension_from_url(url_text: str) -> Optional[str]:
    try:
        parsed = urllib.parse.urlparse(url_text)
    except Exception:
        return None
    path = (parsed.path or "").lower()
    for extension in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        if path.endswith(extension):
            return extension
    return None


def _is_allowed_thumbnail_url(source_url: str, *, allowed_host_suffixes: Tuple[str, ...]) -> bool:
    try:
        parsed = urllib.parse.urlparse(source_url)
    except Exception:
        return False

    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        return False

    host = (parsed.hostname or "").lower()
    if not host:
        return False

    return any(host == suffix or host.endswith("." + suffix) for suffix in allowed_host_suffixes)


class ThumbCache:
    """Thumbnail download and local caching."""

    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        *,
        max_download_bytes: int = 8 * 1024 * 1024,
        request_timeout_seconds: float = 10.0,
        allowed_host_suffixes: Tuple[str, ...] = ("ytimg.com", "ggpht.com", "googleusercontent.com"),
    ) -> None:
        if cache_dir is None:
            base_temp_dir = Path(os.getenv("TEMP") or os.getenv("TMP") or "/tmp")
            cache_dir = base_temp_dir / "steppy_thumb_cache"

        self._cache_dir = Path(cache_dir).resolve()
        self._cache_dir.mkdir(parents=True, exist_ok=True)

        self._max_download_bytes = int(max(64 * 1024, max_download_bytes))
        self._request_timeout_seconds = float(max(1.0, request_timeout_seconds))
        self._allowed_host_suffixes = tuple(str(value).lower() for value in allowed_host_suffixes)

        self._locks_guard = threading.Lock()
        self._locks_by_key: dict[str, threading.Lock] = {}

    def cache_dir(self) -> Path:
        return self._cache_dir

    def make_local_url(self, thumbnail_url: str, *, route_path: str = "/thumb") -> str:
        cleaned_url = (thumbnail_url or "").strip()
        if not cleaned_url:
            raise ThumbCacheDownloadError("Missing thumbnail url")

        if not _is_allowed_thumbnail_url(cleaned_url, allowed_host_suffixes=self._allowed_host_suffixes):
            raise ThumbCacheUrlNotAllowedError("Thumbnail url host is not allowed")

        query_string = urllib.parse.urlencode({"url": cleaned_url})
        return f"{route_path}?{query_string}"

    def get_or_fetch(self, thumbnail_url: str) -> CachedThumbnail:
        cleaned_url = (thumbnail_url or "").strip()
        if not cleaned_url:
            raise ThumbCacheDownloadError("Missing thumbnail url")

        if not _is_allowed_thumbnail_url(cleaned_url, allowed_host_suffixes=self._allowed_host_suffixes):
            raise ThumbCacheUrlNotAllowedError("Thumbnail url host is not allowed")

        cache_key = _sha256_hex(cleaned_url)

        with self._get_lock_for_key(cache_key):
            existing_path = self._find_existing_cached_file(cache_key)
            if existing_path is not None:
                content_type = self._read_metadata_content_type(cache_key) or self._guess_content_type_from_path(existing_path)
                fetched_unix_seconds = self._read_metadata_fetched_unix_seconds(cache_key) or existing_path.stat().st_mtime
                return CachedThumbnail(
                    file_path=existing_path,
                    url=cleaned_url,
                    fetched_unix_seconds=float(fetched_unix_seconds),
                    content_type=content_type,
                )

            downloaded_path, content_type = self._download_to_cache(cache_key, cleaned_url)
            fetched_unix_seconds = time.time()
            self._write_metadata(cache_key, content_type=content_type, fetched_unix_seconds=fetched_unix_seconds)
            return CachedThumbnail(
                file_path=downloaded_path,
                url=cleaned_url,
                fetched_unix_seconds=float(fetched_unix_seconds),
                content_type=content_type,
            )

    def clear(self) -> None:
        try:
            if self._cache_dir.exists():
                shutil.rmtree(self._cache_dir, ignore_errors=True)
        finally:
            self._cache_dir.mkdir(parents=True, exist_ok=True)

    def _metadata_path(self, cache_key: str) -> Path:
        return self._cache_dir / f"{cache_key}.json"

    def _write_metadata(self, cache_key: str, *, content_type: str, fetched_unix_seconds: float) -> None:
        metadata_path = self._metadata_path(cache_key)
        payload = {"content_type": str(content_type), "fetched_unix_seconds": float(fetched_unix_seconds)}
        metadata_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def _read_metadata_content_type(self, cache_key: str) -> Optional[str]:
        metadata_path = self._metadata_path(cache_key)
        if not metadata_path.exists():
            return None
        try:
            parsed = json.loads(metadata_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        value = parsed.get("content_type")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    def _read_metadata_fetched_unix_seconds(self, cache_key: str) -> Optional[float]:
        metadata_path = self._metadata_path(cache_key)
        if not metadata_path.exists():
            return None
        try:
            parsed = json.loads(metadata_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        value = parsed.get("fetched_unix_seconds")
        if isinstance(value, (int, float)):
            return float(value)
        return None

    def _guess_content_type_from_path(self, file_path: Path) -> str:
        suffix = file_path.suffix.lower()
        if suffix == ".png":
            return "image/png"
        if suffix == ".webp":
            return "image/webp"
        if suffix == ".gif":
            return "image/gif"
        return "image/jpeg"

    def _get_lock_for_key(self, cache_key: str) -> threading.Lock:
        with self._locks_guard:
            lock = self._locks_by_key.get(cache_key)
            if lock is None:
                lock = threading.Lock()
                self._locks_by_key[cache_key] = lock
            return lock

    def _find_existing_cached_file(self, cache_key: str) -> Optional[Path]:
        for extension in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
            candidate = self._cache_dir / f"{cache_key}{extension}"
            if candidate.exists():
                return candidate
        return None

    def _download_to_cache(self, cache_key: str, source_url: str) -> tuple[Path, str]:
        request_headers = {
            "User-Agent": "SteppyThumbCache/1.0",
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        }
        request_object = urllib.request.Request(source_url, headers=request_headers)

        temporary_download_path = self._cache_dir / f"{cache_key}.download.tmp"
        try:
            if temporary_download_path.exists():
                temporary_download_path.unlink()
        except Exception:
            pass

        try:
            with urllib.request.urlopen(request_object, timeout=self._request_timeout_seconds) as response:
                content_type = _normalize_content_type(str(response.headers.get("Content-Type") or ""))
                if not content_type:
                    content_type = self._guess_content_type_from_path(Path(source_url))

                extension = _extension_for_content_type(content_type)
                if extension == ".jpg":
                    extension = _safe_guess_extension_from_url(source_url) or ".jpg"

                final_path = self._cache_dir / f"{cache_key}{extension}"

                total_bytes = 0
                with open(temporary_download_path, "wb") as output_file:
                    while True:
                        chunk_bytes = response.read(64 * 1024)
                        if not chunk_bytes:
                            break
                        total_bytes += len(chunk_bytes)
                        if total_bytes > self._max_download_bytes:
                            raise ThumbCacheDownloadError("Thumbnail exceeded max download size")
                        output_file.write(chunk_bytes)

            os.replace(str(temporary_download_path), str(final_path))
            return final_path, content_type

        except ThumbCacheDownloadError:
            raise
        except Exception as exception:
            try:
                if temporary_download_path.exists():
                    temporary_download_path.unlink()
            except Exception:
                pass
            raise ThumbCacheDownloadError(f"Thumbnail download failed: {exception}") from exception
