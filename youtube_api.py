"""
youtube_api.py

YouTube search and metadata for the Steppy web app.

Purpose
- Search
- Fetch video details (title, duration, thumbnails)
- Optional playlist support for attract mode selection

Integration
- Intended to be used by a future Flask web_server.py
- Results are cached to reduce quota usage and improve speed
- Cache is designed to be swappable later with persistence.py

Standalone usage
python -m youtube_api

No command line arguments are required or used.

Configuration
- Loads settings via config.py (JSON config file + optional env overrides)
- Requires youtube.api_key to be set in the config file
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Sequence

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from platformdirs import user_cache_dir

from config import AppConfig, get_config


# -----------------------------
# Models
# -----------------------------


@dataclass(frozen=True)
class Thumbnail:
    url: str
    width: Optional[int] = None
    height: Optional[int] = None


@dataclass(frozen=True)
class VideoDetails:
    video_id: str
    title: str
    duration_seconds: int
    channel_title: Optional[str]
    published_at: Optional[str]
    thumbnails: List[Thumbnail]


@dataclass(frozen=True)
class SearchResponse:
    query: str
    items: List[VideoDetails]
    next_page_token: Optional[str]
    total_results: Optional[int]


@dataclass(frozen=True)
class PlaylistResponse:
    playlist_id: str
    items: List[VideoDetails]
    next_page_token: Optional[str]


# -----------------------------
# Errors
# -----------------------------


class YouTubeApiError(Exception):
    pass


class YouTubeApiAuthError(YouTubeApiError):
    pass


class YouTubeApiQuotaError(YouTubeApiError):
    pass


class YouTubeApiRequestError(YouTubeApiError):
    pass


# -----------------------------
# Cache hooks
# -----------------------------


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
    """
    Lightweight JSON file cache.

    Stores all keys in one file for simplicity. This can be replaced later with
    persistence.py backed caching without changing YouTubeApi call sites.
    """

    def __init__(self, cache_path: Path) -> None:
        self._cache_path = cache_path
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._data: Dict[str, str] = {}
        self._dirty = False
        self._load_from_disk()

    def get_text(self, key: str) -> Optional[str]:
        return self._data.get(key)

    def set_text(self, key: str, value: str) -> None:
        self._data[key] = value
        self._dirty = True

    def flush(self) -> None:
        if not self._dirty:
            return
        temporary_path = self._cache_path.with_suffix(self._cache_path.suffix + ".tmp")
        temporary_path.write_text(json.dumps(self._data, ensure_ascii=False), encoding="utf-8")
        temporary_path.replace(self._cache_path)
        self._dirty = False

    def _load_from_disk(self) -> None:
        if not self._cache_path.exists():
            return
        try:
            loaded_text = self._cache_path.read_text(encoding="utf-8")
            loaded_data = json.loads(loaded_text)
            if isinstance(loaded_data, dict):
                self._data = {str(key): str(value) for key, value in loaded_data.items()}
        except Exception:
            self._data = {}


# -----------------------------
# Helpers
# -----------------------------


def _stable_json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _wrap_cached_value(value: Any) -> str:
    wrapped = {"fetched_at": int(time.time()), "value": value}
    return _stable_json_dumps(wrapped)


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


def parse_youtube_duration_seconds(iso_8601_duration: str) -> int:
    """
    Parses a YouTube ISO 8601 duration string like:
    - PT3M12S
    - PT1H2M3S
    - PT45S
    Returns total seconds as an int.
    """
    duration_text = (iso_8601_duration or "").strip().upper()
    if not duration_text:
        return 0

    if not duration_text.startswith("P"):
        return 0

    days = 0
    hours = 0
    minutes = 0
    seconds = 0

    in_time_part = False
    current_number_text = ""

    for character in duration_text[1:]:
        if character == "T":
            in_time_part = True
            current_number_text = ""
            continue

        if character.isdigit():
            current_number_text += character
            continue

        if not current_number_text:
            continue

        value_number = int(current_number_text)
        current_number_text = ""

        if not in_time_part:
            if character == "D":
                days = value_number
        else:
            if character == "H":
                hours = value_number
            elif character == "M":
                minutes = value_number
            elif character == "S":
                seconds = value_number

    total_seconds = seconds + 60 * minutes + 3600 * hours + 86400 * days
    return int(max(0, total_seconds))


def _extract_thumbnails(snippet: Dict[str, Any]) -> List[Thumbnail]:
    thumbnails_block = snippet.get("thumbnails") or {}
    if not isinstance(thumbnails_block, dict):
        return []

    preferred_order = ["maxres", "standard", "high", "medium", "default"]
    thumbnails: List[Thumbnail] = []

    for key in preferred_order:
        entry = thumbnails_block.get(key)
        if not isinstance(entry, dict):
            continue
        url = entry.get("url")
        if not isinstance(url, str) or not url:
            continue
        width_value = entry.get("width")
        height_value = entry.get("height")
        thumbnails.append(
            Thumbnail(
                url=url,
                width=int(width_value) if isinstance(width_value, int) else None,
                height=int(height_value) if isinstance(height_value, int) else None,
            )
        )

    for key, entry in thumbnails_block.items():
        if key in preferred_order:
            continue
        if not isinstance(entry, dict):
            continue
        url = entry.get("url")
        if not isinstance(url, str) or not url:
            continue
        width_value = entry.get("width")
        height_value = entry.get("height")
        thumbnails.append(
            Thumbnail(
                url=url,
                width=int(width_value) if isinstance(width_value, int) else None,
                height=int(height_value) if isinstance(height_value, int) else None,
            )
        )

    return thumbnails


def _raise_for_http_error(http_error: HttpError) -> None:
    status_code = getattr(http_error.resp, "status", None)
    error_reason = ""

    try:
        error_body = json.loads(http_error.content.decode("utf-8", errors="replace"))
    except Exception:
        error_body = None

    if isinstance(error_body, dict):
        error_block = error_body.get("error")
        if isinstance(error_block, dict):
            errors_list = error_block.get("errors")
            if isinstance(errors_list, list) and errors_list:
                first_error = errors_list[0]
                if isinstance(first_error, dict):
                    error_reason = str(first_error.get("reason") or "")

    if status_code in (401, 403) and error_reason in ("keyInvalid", "forbidden"):
        raise YouTubeApiAuthError(f"YouTube API auth error (status {status_code}, reason {error_reason}).")

    if status_code == 403 and error_reason in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"):
        raise YouTubeApiQuotaError(f"YouTube API quota error (status {status_code}, reason {error_reason}).")

    raise YouTubeApiRequestError(f"YouTube API request failed (status {status_code}, reason {error_reason}).")


# -----------------------------
# YouTube API client
# -----------------------------


class YouTubeApi:
    def __init__(
        self,
        *,
        api_key: str,
        cache: Optional[YouTubeCache],
        cache_ttl_seconds: int,
        region_code: Optional[str],
        language: Optional[str],
        safe_search: str,
        require_embeddable: bool,
    ) -> None:
        cleaned_api_key = (api_key or "").strip()
        if not cleaned_api_key:
            raise YouTubeApiAuthError("Missing YouTube API key. Set youtube.api_key in the Steppy config file.")

        self._api_key = cleaned_api_key
        self._cache = cache if cache is not None else NullCache()
        self._cache_ttl_seconds = int(cache_ttl_seconds)
        self._region_code = (region_code or "").strip() or None
        self._language = (language or "").strip() or None
        self._safe_search = (safe_search or "none").strip().lower()
        self._require_embeddable = bool(require_embeddable)

        self._service = build("youtube", "v3", developerKey=self._api_key, cache_discovery=False)

    @classmethod
    def from_app_config(cls, app_config: AppConfig, *, cache: Optional[YouTubeCache] = None) -> "YouTubeApi":
        if cache is None:
            cache_directory = Path(user_cache_dir("Steppy", "Steppy"))
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

    @classmethod
    def from_config_file(cls, *, cache: Optional[YouTubeCache] = None) -> "YouTubeApi":
        app_config, _config_path = get_config()
        return cls.from_app_config(app_config, cache=cache)

    def close(self) -> None:
        self._cache.flush()

    # Public methods intended for web_server.py

    def search_videos(
        self,
        query: str,
        *,
        page_token: Optional[str] = None,
        max_results: int = 20,
    ) -> SearchResponse:
        normalized_query = (query or "").strip()
        if not normalized_query:
            return SearchResponse(query="", items=[], next_page_token=None, total_results=0)

        bounded_max_results = int(max(1, min(50, max_results)))

        cache_key = self._make_search_cache_key(
            query=normalized_query,
            page_token=page_token,
            max_results=bounded_max_results,
        )
        cached_value = self._cache_get_value(cache_key)
        if isinstance(cached_value, dict):
            return self._search_response_from_cached_dict(cached_value, query=normalized_query)

        raw_search_result = self._perform_search_request(
            query=normalized_query,
            page_token=page_token,
            max_results=bounded_max_results,
        )

        video_ids_in_order = raw_search_result["video_ids"]
        next_page_token_out = raw_search_result["next_page_token"]
        total_results_out = raw_search_result["total_results"]

        details_by_id = self.get_video_details_map(video_ids_in_order)

        ordered_items: List[VideoDetails] = []
        for video_id in video_ids_in_order:
            details = details_by_id.get(video_id)
            if details is not None:
                ordered_items.append(details)

        response = SearchResponse(
            query=normalized_query,
            items=ordered_items,
            next_page_token=next_page_token_out,
            total_results=total_results_out,
        )

        self._cache_set_value(cache_key, self._search_response_to_cached_dict(response))
        return response

    def get_video_details_map(self, video_ids: Sequence[str]) -> Dict[str, VideoDetails]:
        cleaned_ids = [str(video_id).strip() for video_id in (video_ids or []) if str(video_id).strip()]
        if not cleaned_ids:
            return {}

        unique_ids_in_order: List[str] = []
        seen_ids: set[str] = set()
        for video_id in cleaned_ids:
            if video_id in seen_ids:
                continue
            seen_ids.add(video_id)
            unique_ids_in_order.append(video_id)

        details_by_id: Dict[str, VideoDetails] = {}
        missing_ids: List[str] = []

        for video_id in unique_ids_in_order:
            cache_key = self._make_video_details_cache_key(video_id)
            cached_value = self._cache_get_value(cache_key)
            if isinstance(cached_value, dict):
                parsed_details = self._video_details_from_cached_dict(cached_value)
                if parsed_details is not None:
                    details_by_id[video_id] = parsed_details
                    continue
            missing_ids.append(video_id)

        if missing_ids:
            fetched_details = self._perform_videos_details_request(missing_ids)
            for details in fetched_details:
                details_by_id[details.video_id] = details
                cache_key = self._make_video_details_cache_key(details.video_id)
                self._cache_set_value(cache_key, self._video_details_to_cached_dict(details))

        return details_by_id

    def get_playlist_items(
        self,
        playlist_id: str,
        *,
        page_token: Optional[str] = None,
        max_results: int = 50,
    ) -> PlaylistResponse:
        cleaned_playlist_id = (playlist_id or "").strip()
        if not cleaned_playlist_id:
            return PlaylistResponse(playlist_id="", items=[], next_page_token=None)

        bounded_max_results = int(max(1, min(50, max_results)))

        cache_key = self._make_playlist_cache_key(
            playlist_id=cleaned_playlist_id,
            page_token=page_token,
            max_results=bounded_max_results,
        )
        cached_value = self._cache_get_value(cache_key)
        if isinstance(cached_value, dict):
            return self._playlist_response_from_cached_dict(cached_value, playlist_id=cleaned_playlist_id)

        raw_playlist_result = self._perform_playlist_items_request(
            playlist_id=cleaned_playlist_id,
            page_token=page_token,
            max_results=bounded_max_results,
        )

        video_ids_in_order = raw_playlist_result["video_ids"]
        next_page_token_out = raw_playlist_result["next_page_token"]

        details_by_id = self.get_video_details_map(video_ids_in_order)
        ordered_items: List[VideoDetails] = []
        for video_id in video_ids_in_order:
            details = details_by_id.get(video_id)
            if details is not None:
                ordered_items.append(details)

        response = PlaylistResponse(
            playlist_id=cleaned_playlist_id,
            items=ordered_items,
            next_page_token=next_page_token_out,
        )

        self._cache_set_value(cache_key, self._playlist_response_to_cached_dict(response))
        return response

    # Internal request helpers

    def _perform_search_request(
        self,
        *,
        query: str,
        page_token: Optional[str],
        max_results: int,
    ) -> Dict[str, Any]:
        request_kwargs: Dict[str, Any] = {
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": int(max_results),
        }
        if page_token:
            request_kwargs["pageToken"] = page_token
        if self._region_code:
            request_kwargs["regionCode"] = self._region_code
        if self._language:
            request_kwargs["relevanceLanguage"] = self._language
        if self._safe_search in ("none", "moderate", "strict"):
            request_kwargs["safeSearch"] = self._safe_search
        if self._require_embeddable:
            request_kwargs["videoEmbeddable"] = "true"

        try:
            response = self._service.search().list(**request_kwargs).execute()
        except HttpError as http_error:
            _raise_for_http_error(http_error)
        except Exception as exception:
            raise YouTubeApiRequestError(f"YouTube API search failed: {exception}") from exception

        items = response.get("items") or []
        video_ids_in_order: List[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            id_block = item.get("id")
            if not isinstance(id_block, dict):
                continue
            video_id = id_block.get("videoId")
            if isinstance(video_id, str) and video_id.strip():
                video_ids_in_order.append(video_id.strip())

        next_page_token_out = response.get("nextPageToken")
        if not isinstance(next_page_token_out, str):
            next_page_token_out = None

        total_results_out = None
        page_info = response.get("pageInfo")
        if isinstance(page_info, dict):
            total_value = page_info.get("totalResults")
            if isinstance(total_value, int):
                total_results_out = total_value

        return {
            "video_ids": video_ids_in_order,
            "next_page_token": next_page_token_out,
            "total_results": total_results_out,
        }

    def _perform_videos_details_request(self, video_ids: Sequence[str]) -> List[VideoDetails]:
        chunk_size = 50
        results: List[VideoDetails] = []

        for chunk_start_index in range(0, len(video_ids), chunk_size):
            chunk_ids = list(video_ids[chunk_start_index : chunk_start_index + chunk_size])
            id_text = ",".join(chunk_ids)

            request_kwargs: Dict[str, Any] = {
                "part": "snippet,contentDetails",
                "id": id_text,
                "maxResults": len(chunk_ids),
            }

            try:
                response = self._service.videos().list(**request_kwargs).execute()
            except HttpError as http_error:
                _raise_for_http_error(http_error)
            except Exception as exception:
                raise YouTubeApiRequestError(f"YouTube API videos.list failed: {exception}") from exception

            items = response.get("items") or []
            for item in items:
                if not isinstance(item, dict):
                    continue
                video_id = item.get("id")
                if not isinstance(video_id, str) or not video_id:
                    continue

                snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
                content_details = item.get("contentDetails") if isinstance(item.get("contentDetails"), dict) else {}

                title_value = snippet.get("title")
                title_text = str(title_value) if isinstance(title_value, str) else ""

                channel_title_value = snippet.get("channelTitle")
                channel_title_text = str(channel_title_value) if isinstance(channel_title_value, str) else None

                published_at_value = snippet.get("publishedAt")
                published_at_text = str(published_at_value) if isinstance(published_at_value, str) else None

                duration_value = content_details.get("duration")
                duration_seconds = parse_youtube_duration_seconds(str(duration_value) if duration_value else "")

                thumbnails = _extract_thumbnails(snippet)

                results.append(
                    VideoDetails(
                        video_id=video_id,
                        title=title_text,
                        duration_seconds=duration_seconds,
                        channel_title=channel_title_text,
                        published_at=published_at_text,
                        thumbnails=thumbnails,
                    )
                )

        return results

    def _perform_playlist_items_request(
        self,
        *,
        playlist_id: str,
        page_token: Optional[str],
        max_results: int,
    ) -> Dict[str, Any]:
        request_kwargs: Dict[str, Any] = {
            "part": "snippet,contentDetails",
            "playlistId": playlist_id,
            "maxResults": int(max_results),
        }
        if page_token:
            request_kwargs["pageToken"] = page_token

        try:
            response = self._service.playlistItems().list(**request_kwargs).execute()
        except HttpError as http_error:
            _raise_for_http_error(http_error)
        except Exception as exception:
            raise YouTubeApiRequestError(f"YouTube API playlistItems.list failed: {exception}") from exception

        items = response.get("items") or []
        video_ids_in_order: List[str] = []

        for item in items:
            if not isinstance(item, dict):
                continue

            content_details = item.get("contentDetails")
            if isinstance(content_details, dict):
                video_id = content_details.get("videoId")
                if isinstance(video_id, str) and video_id.strip():
                    video_ids_in_order.append(video_id.strip())
                    continue

            snippet = item.get("snippet")
            if isinstance(snippet, dict):
                resource_id = snippet.get("resourceId")
                if isinstance(resource_id, dict):
                    video_id = resource_id.get("videoId")
                    if isinstance(video_id, str) and video_id.strip():
                        video_ids_in_order.append(video_id.strip())

        next_page_token_out = response.get("nextPageToken")
        if not isinstance(next_page_token_out, str):
            next_page_token_out = None

        return {"video_ids": video_ids_in_order, "next_page_token": next_page_token_out}

    # Cache keys and serialization

    def _make_search_cache_key(
        self,
        *,
        query: str,
        page_token: Optional[str],
        max_results: int,
    ) -> str:
        key_payload = {
            "kind": "search",
            "query": query,
            "page_token": page_token or "",
            "max_results": int(max_results),
            "region_code": self._region_code or "",
            "language": self._language or "",
            "safe_search": self._safe_search or "",
            "require_embeddable": self._require_embeddable,
        }
        return "steppy_youtube_" + _hash_key(_stable_json_dumps(key_payload))

    def _make_video_details_cache_key(self, video_id: str) -> str:
        key_payload = {"kind": "video_details", "video_id": video_id}
        return "steppy_youtube_" + _hash_key(_stable_json_dumps(key_payload))

    def _make_playlist_cache_key(
        self,
        *,
        playlist_id: str,
        page_token: Optional[str],
        max_results: int,
    ) -> str:
        key_payload = {
            "kind": "playlist",
            "playlist_id": playlist_id,
            "page_token": page_token or "",
            "max_results": int(max_results),
        }
        return "steppy_youtube_" + _hash_key(_stable_json_dumps(key_payload))

    def _cache_get_value(self, key: str) -> Optional[Any]:
        cached_text = self._cache.get_text(key)
        if cached_text is None:
            return None
        return _unwrap_cached_value(cached_text, ttl_seconds=self._cache_ttl_seconds)

    def _cache_set_value(self, key: str, value: Any) -> None:
        self._cache.set_text(key, _wrap_cached_value(value))

    def _video_details_to_cached_dict(self, details: VideoDetails) -> Dict[str, Any]:
        return asdict(details)

    def _video_details_from_cached_dict(self, payload: Dict[str, Any]) -> Optional[VideoDetails]:
        try:
            video_id = str(payload.get("video_id") or "").strip()
            if not video_id:
                return None

            title = str(payload.get("title") or "")
            duration_seconds = int(payload.get("duration_seconds") or 0)

            channel_title_value = payload.get("channel_title")
            channel_title = str(channel_title_value) if isinstance(channel_title_value, str) else None

            published_at_value = payload.get("published_at")
            published_at = str(published_at_value) if isinstance(published_at_value, str) else None

            thumbnails_payload = payload.get("thumbnails") or []
            thumbnails: List[Thumbnail] = []
            if isinstance(thumbnails_payload, list):
                for entry in thumbnails_payload:
                    if not isinstance(entry, dict):
                        continue
                    url = entry.get("url")
                    if not isinstance(url, str) or not url:
                        continue
                    width_value = entry.get("width")
                    height_value = entry.get("height")
                    thumbnails.append(
                        Thumbnail(
                            url=url,
                            width=int(width_value) if isinstance(width_value, int) else None,
                            height=int(height_value) if isinstance(height_value, int) else None,
                        )
                    )

            return VideoDetails(
                video_id=video_id,
                title=title,
                duration_seconds=max(0, duration_seconds),
                channel_title=channel_title,
                published_at=published_at,
                thumbnails=thumbnails,
            )
        except Exception:
            return None

    def _search_response_to_cached_dict(self, response: SearchResponse) -> Dict[str, Any]:
        return {
            "query": response.query,
            "items": [asdict(item) for item in response.items],
            "next_page_token": response.next_page_token,
            "total_results": response.total_results,
        }

    def _search_response_from_cached_dict(self, payload: Dict[str, Any], *, query: str) -> SearchResponse:
        items_payload = payload.get("items") or []
        items: List[VideoDetails] = []
        if isinstance(items_payload, list):
            for entry in items_payload:
                if isinstance(entry, dict):
                    details = self._video_details_from_cached_dict(entry)
                    if details is not None:
                        items.append(details)

        next_page_token_value = payload.get("next_page_token")
        next_page_token = str(next_page_token_value) if isinstance(next_page_token_value, str) else None

        total_results_value = payload.get("total_results")
        total_results = int(total_results_value) if isinstance(total_results_value, int) else None

        return SearchResponse(
            query=query,
            items=items,
            next_page_token=next_page_token,
            total_results=total_results,
        )

    def _playlist_response_to_cached_dict(self, response: PlaylistResponse) -> Dict[str, Any]:
        return {
            "playlist_id": response.playlist_id,
            "items": [asdict(item) for item in response.items],
            "next_page_token": response.next_page_token,
        }

    def _playlist_response_from_cached_dict(self, payload: Dict[str, Any], *, playlist_id: str) -> PlaylistResponse:
        items_payload = payload.get("items") or []
        items: List[VideoDetails] = []
        if isinstance(items_payload, list):
            for entry in items_payload:
                if isinstance(entry, dict):
                    details = self._video_details_from_cached_dict(entry)
                    if details is not None:
                        items.append(details)

        next_page_token_value = payload.get("next_page_token")
        next_page_token = str(next_page_token_value) if isinstance(next_page_token_value, str) else None

        return PlaylistResponse(
            playlist_id=playlist_id,
            items=items,
            next_page_token=next_page_token,
        )


# -----------------------------
# Standalone main()
# -----------------------------


def main() -> int:
    default_query = "dance music"

    try:
        youtube_api = YouTubeApi.from_config_file()
    except YouTubeApiError as exception:
        print(json.dumps({"ok": False, "error": str(exception)}, ensure_ascii=False, indent=2))
        return 2
    except Exception as exception:
        print(json.dumps({"ok": False, "error": f"Failed to initialize YouTubeApi: {exception}"}, ensure_ascii=False, indent=2))
        return 2

    try:
        response = youtube_api.search_videos(default_query, max_results=10)
        output = {"ok": True, "response": asdict(response)}
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except YouTubeApiError as exception:
        print(json.dumps({"ok": False, "error": str(exception)}, ensure_ascii=False, indent=2))
        return 1
    finally:
        youtube_api.close()


if __name__ == "__main__":
    raise SystemExit(main())
