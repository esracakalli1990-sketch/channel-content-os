from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen


API_URL = "https://www.googleapis.com/youtube/v3/search"


def search_public_videos(query: str, region: str, max_results: int) -> dict:
    api_key = os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        raise RuntimeError("YOUTUBE_API_KEY is not set. Add it to your environment; never commit it to Git.")
    params = {"part": "snippet", "q": query, "type": "video", "regionCode": region.upper(), "maxResults": max_results, "order": "relevance", "key": api_key}
    try:
        with urlopen(f"{API_URL}?{urlencode(params)}", timeout=30) as response:
            payload = json.load(response)
    except HTTPError as error:
        raise RuntimeError(f"YouTube API returned HTTP {error.code}.") from error
    except URLError as error:
        raise RuntimeError(f"Could not reach the YouTube API: {error.reason}") from error
    return {
        "collected_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "query": query,
        "region": region.upper(),
        "source": "YouTube Data API v3 search.list",
        "items": [{
            "video_id": item["id"].get("videoId"),
            "channel_id": item["snippet"].get("channelId"),
            "channel_title": item["snippet"].get("channelTitle"),
            "title": item["snippet"].get("title"),
            "published_at": item["snippet"].get("publishedAt"),
            "description": item["snippet"].get("description"),
            "thumbnail": item["snippet"].get("thumbnails", {}).get("high", {}).get("url"),
        } for item in payload.get("items", [])],
    }


def save_snapshot(video_directory: Path, snapshot: dict) -> Path:
    from .project import MARKET_DIR

    market_directory = video_directory / MARKET_DIR
    market_directory.mkdir(exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = market_directory / f"youtube_search_{timestamp}.json"
    destination.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    return destination
