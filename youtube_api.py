# -*- coding: utf-8 -*-
from __future__ import annotations

########################
# youtube_api.py
########################
# Purpose:
# - YouTube search and metadata access for the web control experience.
# - Provides cached API calls to reduce quota usage and improve responsiveness.
#
# Design notes:
# - This module is a critical dependency for gameplay_harness.py and web_server.py integrations.
# - Caching is an interface control. Callers should not implement their own redundant cache.
# - Always raise explicit YouTubeApiError for API failures.
#
########################
# Interfaces:
# Public dataclasses:
# - Thumbnail(url: str, width: Optional[int] = None, height: Optional[int] = None)
# - VideoDetails(video_id: str, title: str, channel_title: str, duration_seconds: Optional[int], thumbnails: list[Thumbnail])
# - SearchResponse(items: list[VideoDetails], next_page_token: Optional[str], total_results: Optional[int])
#
# Public protocols and classes:
# - class YouTubeCache(Protocol): get_text(key: str) -> Optional[str], set_text(key: str, value: str) -> None, flush() -> None
# - class NullCache(YouTubeCache)
# - class JsonFileCache(YouTubeCache)
# - class YouTubeApi
#   - from_app_config(app_config: AppConfig, cache: Optional[YouTubeCache] = None) -> YouTubeApi
#   - search_videos(query: str, *, page_token: Optional[str] = None, max_results: int = 10) -> SearchResponse
#   - get_video_details_map(video_ids: Sequence[str]) -> dict[str, VideoDetails]
#   - get_playlist_items(playlist_id: str, *, page_token: Optional[str] = None, max_results: int = 25) -> SearchResponse
#
# Public exceptions:
# - class YouTubeApiError(Exception)
#
########################

import json
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Protocol, Sequence

from config import AppConfig


class YouTubeApiError(Exception):
    pass


@dataclass(frozen=True)
class Thumbnail:
    url: str
    width: Optional[int] = None
    height: Optional[int] = None


@dataclass(frozen=True)
class VideoDetails:
    video_id: str
    title: str
    channel_title: str
    duration_seconds: Optional[int]
    thumbnails: list[Thumbnail]


@dataclass(frozen=True)
class SearchResponse:
    items: list[VideoDetails]
    next_page_token: Optional[str]
    total_results: Optional[int]


class YouTubeCache(Protocol):
    def get_text(self, key: str) -> Optional[str]:
        raise NotImplementedError

    def set_text(self, key: str, value: str) -> None:
        raise NotImplementedError

    def flush(self) -> None:
        raise NotImplementedError


class NullCache:
    def get_text(self, key: str) -> Optional[str]:
        return None

    def set_text(self, key: str, value: str) -> None:
        return None

    def flush(self) -> None:
        return None


class JsonFileCache:
    def __init__(self, file_path: Path) -> None:
        self._file_path = Path(file_path).resolve()
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        self._data: Dict[str, str] = {}
        self._loaded = False
        self._lock = None
        try:
            import threading

            self._lock = threading.Lock()
        except Exception:
            self._lock = None

    def get_text(self, key: str) -> Optional[str]:
        self._ensure_loaded()
        return self._data.get(key)

    def set_text(self, key: str, value: str) -> None:
        self._ensure_loaded()
        self._data[str(key)] = str(value)

    def flush(self) -> None:
        self._ensure_loaded()
        self._write()

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        if self._lock is not None:
            with self._lock:
                if not self._loaded:
                    self._load()
        else:
            self._load()

    def _load(self) -> None:
        self._loaded = True
        if not self._file_path.exists():
            self._data = {}
            return
        try:
            parsed = json.loads(self._file_path.read_text(encoding="utf-8"))
        except Exception:
            self._data = {}
            return
        if isinstance(parsed, dict):
            self._data = {str(k): str(v) for k, v in parsed.items()}
        else:
            self._data = {}

    def _write(self) -> None:
        payload_text = json.dumps(self._data, indent=2, sort_keys=True)
        self._file_path.write_text(payload_text, encoding="utf-8")


def _user_cache_dir(app_name: str) -> Path:
    xdg_cache_home = os.getenv("XDG_CACHE_HOME")
    if xdg_cache_home:
        return Path(xdg_cache_home).expanduser().resolve() / app_name
    home_dir = Path.home()
    if os.name == "nt":
        local_app_data = os.getenv("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data).expanduser().resolve() / app_name
        return home_dir / "AppData" / "Local" / app_name
    return home_dir / ".cache" / app_name


def _parse_iso8601_duration_seconds(iso_8601_duration: str) -> Optional[int]:
    duration_text = (iso_8601_duration or "").strip().upper()
    if not duration_text or not duration_text.startswith("P"):
        return None

    days = 0
    hours = 0
    minutes = 0
    seconds = 0

    number_buffer = ""
    in_time_section = False

    for character in duration_text[1:]:
        if character == "T":
            in_time_section = True
            number_buffer = ""
            continue
        if character.isdigit():
            number_buffer += character
            continue
        if not number_buffer:
            continue
        value_int = int(number_buffer)
        number_buffer = ""

        if character == "D" and not in_time_section:
            days = value_int
        elif character == "H" and in_time_section:
            hours = value_int
        elif character == "M" and in_time_section:
            minutes = value_int
        elif character == "S" and in_time_section:
            seconds = value_int

    total_seconds = days * 86400 + hours * 3600 + minutes * 60 + seconds
    if total_seconds <= 0:
        return None
    return total_seconds


def _wrap_cached_value(value: Any) -> str:
    payload = {"fetched_at": int(time.time()), "value": value}
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def _unwrap_cached_value(value_text: str, ttl_seconds: int) -> Optional[Any]:
    try:
        parsed_value = json.loads(value_text)
    except Exception:
        return None
    if not isinstance(parsed_value, dict):
        return None

    fetched_at = parsed_value.get("fetched_at")
    if not isinstance(fetched_at, int):
        return None

    age_seconds = int(time.time()) - fetched_at
    if ttl_seconds >= 0 and age_seconds > ttl_seconds:
        return None

    return parsed_value.get("value")


class YouTubeApi:
    def __init__(
        self,
        *,
        api_key: str,
        cache: YouTubeCache,
        cache_ttl_seconds: int,
        region_code: str,
        language: str,
        safe_search: str,
        require_embeddable: bool,
        http_get_json: Optional[Callable[[str], Any]] = None,
    ) -> None:
        self._api_key = str(api_key or "").strip()
        if not self._api_key:
            raise YouTubeApiError("Missing YouTube API key")

        self._cache = cache
        self._cache_ttl_seconds = int(cache_ttl_seconds)
        self._region_code = str(region_code or "").strip() or "US"
        self._language = str(language or "").strip() or "en"
        self._safe_search = str(safe_search or "").strip() or "none"
        self._require_embeddable = bool(require_embeddable)

        self._http_get_json = http_get_json or self._http_get_json_default

    @classmethod
    def from_app_config(cls, app_config: AppConfig, cache: Optional[YouTubeCache] = None) -> "YouTubeApi":
        if cache is None:
            cache_directory = _user_cache_dir("Steppy")
            cache_path = cache_directory / "youtube_api_cache.json"
            cache = JsonFileCache(cache_path)

        youtube_config = app_config.youtube
        return cls(
            api_key=youtube_config.api_key,
            cache=cache,
            cache_ttl_seconds=youtube_config.cache_ttl_seconds,
            region_code=youtube_config.region_code,
            language=youtube_config.language,
            safe_search=youtube_config.safe_search,
            require_embeddable=youtube_config.require_embeddable,
        )

    def search_videos(self, query: str, *, page_token: Optional[str] = None, max_results: int = 10) -> SearchResponse:
        normalized_query = str(query or "").strip()
        if not normalized_query:
            return SearchResponse(items=[], next_page_token=None, total_results=0)

        bounded_max_results = int(max(1, min(50, max_results)))

        cache_key = self._cache_key("search", {"q": normalized_query, "page_token": page_token, "max_results": bounded_max_results})
        cached_value = self._cache_get_value(cache_key)
        if isinstance(cached_value, dict):
            return self._search_response_from_cached_dict(cached_value)

        raw_search = self._perform_search_request(query=normalized_query, page_token=page_token, max_results=bounded_max_results)

        video_ids_in_order = raw_search["video_ids"]
        next_page_token_out = raw_search.get("next_page_token")
        total_results_out = raw_search.get("total_results")

        details_by_id = self.get_video_details_map(video_ids_in_order)
        items_out: list[VideoDetails] = []
        for video_id in video_ids_in_order:
            details = details_by_id.get(video_id)
            if details is not None:
                items_out.append(details)

        response = SearchResponse(items=items_out, next_page_token=next_page_token_out, total_results=total_results_out)
        self._cache_set_value(
            cache_key,
            {
                "items": [self._video_details_to_cached_dict(item) for item in items_out],
                "next_page_token": next_page_token_out,
                "total_results": total_results_out,
            },
        )
        return response

    def get_video_details_map(self, video_ids: Sequence[str]) -> Dict[str, VideoDetails]:
        cleaned_video_ids = [str(value).strip() for value in video_ids if str(value).strip()]
        if not cleaned_video_ids:
            return {}

        unique_ids: list[str] = []
        seen: set[str] = set()
        for value in cleaned_video_ids:
            if value not in seen:
                unique_ids.append(value)
                seen.add(value)
        unique_ids = unique_ids[:50]

        cache_key = self._cache_key("videos", {"ids": unique_ids})
        cached_value = self._cache_get_value(cache_key)
        if isinstance(cached_value, dict):
            return self._details_map_from_cached_dict(cached_value)

        api_response = self._perform_videos_request(unique_ids)

        items = api_response.get("items")
        if not isinstance(items, list):
            raise YouTubeApiError("Unexpected YouTube videos response shape")

        result: Dict[str, VideoDetails] = {}
        for item in items:
            details = self._parse_video_details_item(item)
            if details is not None:
                result[details.video_id] = details

        self._cache_set_value(cache_key, {"items": [self._video_details_to_cached_dict(v) for v in result.values()]})
        return result

    def get_playlist_items(self, playlist_id: str, *, page_token: Optional[str] = None, max_results: int = 25) -> SearchResponse:
        cleaned_playlist_id = str(playlist_id or "").strip()
        if not cleaned_playlist_id:
            return SearchResponse(items=[], next_page_token=None, total_results=0)

        bounded_max_results = int(max(1, min(50, max_results)))

        cache_key = self._cache_key(
            "playlist",
            {"playlist_id": cleaned_playlist_id, "page_token": page_token, "max_results": bounded_max_results},
        )
        cached_value = self._cache_get_value(cache_key)
        if isinstance(cached_value, dict):
            return self._search_response_from_cached_dict(cached_value)

        raw_playlist = self._perform_playlist_items_request(
            playlist_id=cleaned_playlist_id,
            page_token=page_token,
            max_results=bounded_max_results,
        )

        video_ids_in_order = raw_playlist["video_ids"]
        next_page_token_out = raw_playlist.get("next_page_token")
        total_results_out = raw_playlist.get("total_results")

        details_by_id = self.get_video_details_map(video_ids_in_order)
        items_out: list[VideoDetails] = []
        for video_id in video_ids_in_order:
            details = details_by_id.get(video_id)
            if details is not None:
                items_out.append(details)

        response = SearchResponse(items=items_out, next_page_token=next_page_token_out, total_results=total_results_out)
        self._cache_set_value(
            cache_key,
            {
                "items": [self._video_details_to_cached_dict(item) for item in items_out],
                "next_page_token": next_page_token_out,
                "total_results": total_results_out,
            },
        )
        return response

    # Cache helpers

    def _cache_key(self, prefix: str, payload: Dict[str, Any]) -> str:
        return "yt:" + prefix + ":" + json.dumps(payload, separators=(",", ":"), sort_keys=True)

    def _cache_get_value(self, cache_key: str) -> Optional[Any]:
        cached_text = self._cache.get_text(cache_key)
        if not cached_text:
            return None
        return _unwrap_cached_value(cached_text, self._cache_ttl_seconds)

    def _cache_set_value(self, cache_key: str, value: Any) -> None:
        try:
            self._cache.set_text(cache_key, _wrap_cached_value(value))
            self._cache.flush()
        except Exception:
            return None

    # HTTP and parsing

    def _http_get_json_default(self, url: str) -> Any:
        request_headers = {"User-Agent": "SteppyYouTubeApi/1.0"}
        request_object = urllib.request.Request(url, headers=request_headers)
        try:
            with urllib.request.urlopen(request_object, timeout=15.0) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                raw_text = response.read().decode(charset, errors="replace")
        except Exception as exception:
            raise YouTubeApiError(f"YouTube API request failed: {exception}") from exception

        try:
            return json.loads(raw_text)
        except Exception as exception:
            raise YouTubeApiError(f"YouTube API returned invalid JSON: {exception}") from exception

    def _perform_search_request(self, *, query: str, page_token: Optional[str], max_results: int) -> Dict[str, Any]:
        query_params: Dict[str, str] = {
            "key": self._api_key,
            "part": "snippet",
            "type": "video",
            "q": query,
            "maxResults": str(max_results),
            "regionCode": self._region_code,
            "relevanceLanguage": self._language,
            "safeSearch": self._safe_search,
        }
        if self._require_embeddable:
            query_params["videoEmbeddable"] = "true"
        if page_token:
            query_params["pageToken"] = page_token

        url = "https://www.googleapis.com/youtube/v3/search?" + urllib.parse.urlencode(query_params)
        payload = self._http_get_json(url)
        self._raise_if_api_error(payload)

        video_ids: list[str] = []
        items = payload.get("items")
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                id_block = item.get("id")
                if isinstance(id_block, dict):
                    video_id = id_block.get("videoId")
                    if isinstance(video_id, str) and video_id.strip():
                        video_ids.append(video_id.strip())

        next_page_token_out = payload.get("nextPageToken")
        if not isinstance(next_page_token_out, str) or not next_page_token_out.strip():
            next_page_token_out = None

        total_results_out: Optional[int] = None
        page_info = payload.get("pageInfo")
        if isinstance(page_info, dict):
            total_results_value = page_info.get("totalResults")
            if isinstance(total_results_value, int):
                total_results_out = total_results_value

        return {"video_ids": video_ids, "next_page_token": next_page_token_out, "total_results": total_results_out}

    def _perform_videos_request(self, video_ids: Sequence[str]) -> Dict[str, Any]:
        query_params: Dict[str, str] = {
            "key": self._api_key,
            "part": "snippet,contentDetails",
            "id": ",".join(video_ids),
            "maxResults": "50",
        }
        url = "https://www.googleapis.com/youtube/v3/videos?" + urllib.parse.urlencode(query_params)
        payload = self._http_get_json(url)
        self._raise_if_api_error(payload)
        return payload

    def _perform_playlist_items_request(self, *, playlist_id: str, page_token: Optional[str], max_results: int) -> Dict[str, Any]:
        query_params: Dict[str, str] = {
            "key": self._api_key,
            "part": "snippet,contentDetails",
            "playlistId": playlist_id,
            "maxResults": str(max_results),
        }
        if page_token:
            query_params["pageToken"] = page_token

        url = "https://www.googleapis.com/youtube/v3/playlistItems?" + urllib.parse.urlencode(query_params)
        payload = self._http_get_json(url)
        self._raise_if_api_error(payload)

        video_ids: list[str] = []
        items = payload.get("items")
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                content_details = item.get("contentDetails")
                if isinstance(content_details, dict):
                    video_id = content_details.get("videoId")
                    if isinstance(video_id, str) and video_id.strip():
                        video_ids.append(video_id.strip())

        next_page_token_out = payload.get("nextPageToken")
        if not isinstance(next_page_token_out, str) or not next_page_token_out.strip():
            next_page_token_out = None

        total_results_out: Optional[int] = None
        page_info = payload.get("pageInfo")
        if isinstance(page_info, dict):
            total_results_value = page_info.get("totalResults")
            if isinstance(total_results_value, int):
                total_results_out = total_results_value

        return {"video_ids": video_ids, "next_page_token": next_page_token_out, "total_results": total_results_out}

    def _raise_if_api_error(self, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        error_block = payload.get("error")
        if not isinstance(error_block, dict):
            return
        message = error_block.get("message")
        errors = error_block.get("errors")
        if isinstance(errors, list) and errors:
            first_error = errors[0]
            if isinstance(first_error, dict):
                detail_reason = first_error.get("reason")
                if isinstance(detail_reason, str) and detail_reason.strip():
                    if isinstance(message, str) and message.strip():
                        raise YouTubeApiError(f"{message.strip()} ({detail_reason.strip()})")
                    raise YouTubeApiError(detail_reason.strip())
        if isinstance(message, str) and message.strip():
            raise YouTubeApiError(message.strip())
        raise YouTubeApiError("YouTube API error")

    def _parse_video_details_item(self, item: Any) -> Optional[VideoDetails]:
        if not isinstance(item, dict):
            return None
        video_id = item.get("id")
        if not isinstance(video_id, str) or not video_id.strip():
            return None
        snippet = item.get("snippet")
        if not isinstance(snippet, dict):
            return None

        title = str(snippet.get("title") or "").strip()
        channel_title = str(snippet.get("channelTitle") or "").strip()

        content_details = item.get("contentDetails")
        duration_seconds: Optional[int] = None
        if isinstance(content_details, dict):
            duration_value = content_details.get("duration")
            if isinstance(duration_value, str):
                duration_seconds = _parse_iso8601_duration_seconds(duration_value)

        thumbnails: list[Thumbnail] = []
        thumbnails_block = snippet.get("thumbnails")
        if isinstance(thumbnails_block, dict):
            for key_name in ("default", "medium", "high", "standard", "maxres"):
                thumb = thumbnails_block.get(key_name)
                if isinstance(thumb, dict):
                    url = thumb.get("url")
                    if isinstance(url, str) and url.strip():
                        width = thumb.get("width")
                        height = thumb.get("height")
                        thumbnails.append(
                            Thumbnail(
                                url=url.strip(),
                                width=int(width) if isinstance(width, int) else None,
                                height=int(height) if isinstance(height, int) else None,
                            )
                        )

        return VideoDetails(
            video_id=video_id.strip(),
            title=title,
            channel_title=channel_title,
            duration_seconds=duration_seconds,
            thumbnails=thumbnails,
        )

    # Cached dict conversion

    def _video_details_to_cached_dict(self, details: VideoDetails) -> Dict[str, Any]:
        return {
            "video_id": details.video_id,
            "title": details.title,
            "channel_title": details.channel_title,
            "duration_seconds": details.duration_seconds,
            "thumbnails": [{"url": t.url, "width": t.width, "height": t.height} for t in details.thumbnails],
        }

    def _details_map_from_cached_dict(self, cached: Dict[str, Any]) -> Dict[str, VideoDetails]:
        items = cached.get("items")
        if not isinstance(items, list):
            return {}
        result: Dict[str, VideoDetails] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            video_id = item.get("video_id")
            title = item.get("title")
            channel_title = item.get("channel_title")
            duration_seconds = item.get("duration_seconds")
            thumbnails_value = item.get("thumbnails")
            if not isinstance(video_id, str):
                continue
            thumbnails: list[Thumbnail] = []
            if isinstance(thumbnails_value, list):
                for thumb in thumbnails_value:
                    if isinstance(thumb, dict):
                        url = thumb.get("url")
                        if isinstance(url, str) and url.strip():
                            width = thumb.get("width")
                            height = thumb.get("height")
                            thumbnails.append(
                                Thumbnail(
                                    url=url.strip(),
                                    width=int(width) if isinstance(width, int) else None,
                                    height=int(height) if isinstance(height, int) else None,
                                )
                            )
            result[video_id] = VideoDetails(
                video_id=video_id,
                title=str(title or ""),
                channel_title=str(channel_title or ""),
                duration_seconds=int(duration_seconds) if isinstance(duration_seconds, int) else None,
                thumbnails=thumbnails,
            )
        return result

    def _search_response_from_cached_dict(self, cached: Dict[str, Any]) -> SearchResponse:
        items_value = cached.get("items")
        items_out: list[VideoDetails] = []
        if isinstance(items_value, list):
            map_value = self._details_map_from_cached_dict({"items": items_value})
            for item in items_value:
                if isinstance(item, dict):
                    video_id = item.get("video_id")
                    if isinstance(video_id, str) and video_id in map_value:
                        items_out.append(map_value[video_id])

        next_page_token = cached.get("next_page_token")
        if not isinstance(next_page_token, str) or not next_page_token.strip():
            next_page_token = None

        total_results = cached.get("total_results")
        if not isinstance(total_results, int):
            total_results = None

        return SearchResponse(items=items_out, next_page_token=next_page_token, total_results=total_results)
