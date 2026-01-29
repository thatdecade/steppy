# thumb_cache.py
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
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
    cache_key: str
    source_url: str
    file_path: Path
    content_type: str
    fetched_at_epoch_seconds: int


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _get_windows_temp_root() -> Path:
    # On Windows, TEMP/TMP are typically set. Fallback to tempfile.gettempdir().
    temp_text = (os.environ.get("TEMP") or os.environ.get("TMP") or "").strip()
    if temp_text:
        return Path(temp_text)
    return Path(tempfile.gettempdir())


def _normalize_content_type(raw_value: str) -> str:
    value = (raw_value or "").split(";", 1)[0].strip().lower()
    return value


def _extension_for_content_type(content_type: str) -> str:
    mapping = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }
    return mapping.get(_normalize_content_type(content_type), ".jpg")


def _safe_guess_extension_from_url(source_url: str) -> str:
    try:
        parsed = urllib.parse.urlparse(source_url)
        suffix = Path(parsed.path).suffix.lower()
        if suffix in (".jpg", ".jpeg"):
            return ".jpg"
        if suffix in (".png", ".webp", ".gif"):
            return suffix
    except Exception:
        return ".jpg"
    return ".jpg"


def _is_allowed_thumbnail_url(source_url: str) -> bool:
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

    # Allow common YouTube thumbnail hosts.
    allowed_suffixes = (
        "ytimg.com",
        "ggpht.com",
        "googleusercontent.com",
    )
    if any(host == suffix or host.endswith("." + suffix) for suffix in allowed_suffixes):
        return True

    return False


class ThumbCache:
    """
    Thumbnail download and local caching.

    - Downloads images on demand
    - Stores in the Windows temp folder (or platform temp fallback)
    - Returns cached file paths for fast serving by a web server
    """

    def __init__(
        self,
        *,
        cache_dir: Optional[Path] = None,
        max_download_bytes: int = 8 * 1024 * 1024,
        request_timeout_seconds: float = 10.0,
    ) -> None:
        resolved_cache_dir = cache_dir
        if resolved_cache_dir is None:
            resolved_cache_dir = _get_windows_temp_root() / "steppy_thumb_cache"
        self._cache_dir = resolved_cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)

        self._max_download_bytes = int(max(64 * 1024, max_download_bytes))
        self._request_timeout_seconds = float(max(1.0, request_timeout_seconds))

        self._locks_guard = threading.Lock()
        self._locks_by_key: dict[str, threading.Lock] = {}

    @property
    def cache_dir(self) -> Path:
        return self._cache_dir

    def make_local_url(self, source_url: str, *, route_path: str = "/thumb") -> str:
        cleaned_url = (source_url or "").strip()
        if not cleaned_url:
            return ""
        if cleaned_url.startswith(route_path + "?"):
            return cleaned_url
        encoded = urllib.parse.quote(cleaned_url, safe="")
        return f"{route_path}?url={encoded}"

    def get_or_fetch(self, source_url: str) -> CachedThumbnail:
        cleaned_url = (source_url or "").strip()
        if not cleaned_url:
            raise ThumbCacheDownloadError("Missing thumbnail url")

        if not _is_allowed_thumbnail_url(cleaned_url):
            raise ThumbCacheUrlNotAllowedError("Thumbnail url host is not allowed")

        cache_key = _sha256_hex(cleaned_url)
        with self._get_lock_for_key(cache_key):
            existing = self._find_existing_cached_file(cache_key)
            if existing is not None:
                content_type = self._read_metadata_content_type(cache_key) or self._guess_content_type_from_path(existing)
                return CachedThumbnail(
                    cache_key=cache_key,
                    source_url=cleaned_url,
                    file_path=existing,
                    content_type=content_type,
                    fetched_at_epoch_seconds=int(existing.stat().st_mtime),
                )

            downloaded_path, content_type = self._download_to_cache(cache_key, cleaned_url)
            fetched_at = int(time.time())
            self._write_metadata(cache_key, cleaned_url, content_type, fetched_at)
            try:
                os.utime(downloaded_path, (fetched_at, fetched_at))
            except Exception:
                pass

            return CachedThumbnail(
                cache_key=cache_key,
                source_url=cleaned_url,
                file_path=downloaded_path,
                content_type=content_type,
                fetched_at_epoch_seconds=fetched_at,
            )

    def _get_lock_for_key(self, cache_key: str) -> threading.Lock:
        with self._locks_guard:
            existing_lock = self._locks_by_key.get(cache_key)
            if existing_lock is not None:
                return existing_lock
            created_lock = threading.Lock()
            self._locks_by_key[cache_key] = created_lock
            return created_lock

    def _find_existing_cached_file(self, cache_key: str) -> Optional[Path]:
        known_extensions = (".jpg", ".png", ".webp", ".gif")
        for extension in known_extensions:
            candidate = self._cache_dir / f"{cache_key}{extension}"
            if candidate.exists() and candidate.is_file():
                return candidate
        return None

    def _metadata_path(self, cache_key: str) -> Path:
        return self._cache_dir / f"{cache_key}.json"

    def _read_metadata_content_type(self, cache_key: str) -> Optional[str]:
        metadata_path = self._metadata_path(cache_key)
        if not metadata_path.exists():
            return None
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                content_type = payload.get("content_type")
                if isinstance(content_type, str) and content_type.strip():
                    return _normalize_content_type(content_type)
        except Exception:
            return None
        return None

    def _write_metadata(self, cache_key: str, source_url: str, content_type: str, fetched_at: int) -> None:
        payload = {
            "source_url": source_url,
            "content_type": _normalize_content_type(content_type),
            "fetched_at": int(fetched_at),
        }
        metadata_path = self._metadata_path(cache_key)
        temporary_path = metadata_path.with_suffix(".json.tmp")
        temporary_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary_path.replace(metadata_path)

    def _guess_content_type_from_path(self, file_path: Path) -> str:
        suffix = file_path.suffix.lower()
        if suffix == ".png":
            return "image/png"
        if suffix == ".webp":
            return "image/webp"
        if suffix == ".gif":
            return "image/gif"
        return "image/jpeg"

    def _download_to_cache(self, cache_key: str, source_url: str) -> Tuple[Path, str]:
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
                        chunk = response.read(64 * 1024)
                        if not chunk:
                            break
                        total_bytes += len(chunk)
                        if total_bytes > self._max_download_bytes:
                            raise ThumbCacheDownloadError("Thumbnail download exceeded size limit")
                        output_file.write(chunk)

                temporary_download_path.replace(final_path)
                return final_path, content_type
        except ThumbCacheError:
            try:
                if temporary_download_path.exists():
                    temporary_download_path.unlink()
            except Exception:
                pass
            raise
        except Exception as exception:
            try:
                if temporary_download_path.exists():
                    temporary_download_path.unlink()
            except Exception:
                pass
            raise ThumbCacheDownloadError(f"Thumbnail download failed: {exception}") from exception

    def clear(self) -> None:
        # Best effort cleanup.
        try:
            if self._cache_dir.exists():
                shutil.rmtree(self._cache_dir, ignore_errors=True)
        finally:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
