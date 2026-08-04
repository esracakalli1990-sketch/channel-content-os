"""YouTube Analytics data collection.

Pulls performance metrics from the YouTube Analytics API
and generates structured reports for each video.

Two different APIs are used here and they are not interchangeable:

* The **Data API** (``get_video_stats``) gives public counters — views, likes,
  comments. Any token that can upload can read these.
* The **Analytics API** (``get_video_retention``, ``get_traffic_sources``)
  gives what actually explains a Short's performance: how much of it people
  watched, and where the views came from. It needs the separate
  ``yt-analytics.readonly`` scope, so it fails until the app is re-authorised
  with that permission included.

Requires YouTube Data API v3 + Analytics API enabled.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .youtube_auth import get_access_token

logger = logging.getLogger(__name__)

ANALYTICS_URL = "https://youtubeanalytics.googleapis.com/v2/reports"
DATA_URL = "https://www.googleapis.com/youtube/v3/videos"


def get_video_stats(youtube_video_id: str) -> dict:
    """Fetch basic statistics for a video (views, likes, comments)."""
    token = get_access_token()
    params = urlencode({
        "part": "statistics,contentDetails",
        "id": youtube_video_id,
    })
    request = Request(
        f"{DATA_URL}?{params}",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urlopen(request, timeout=15) as response:
            payload = json.load(response)
    except HTTPError as exc:
        raise RuntimeError(f"YouTube Data API error: HTTP {exc.code}") from exc

    items = payload.get("items", [])
    if not items:
        return {}

    stats = items[0].get("statistics", {})
    details = items[0].get("contentDetails", {})
    return {
        "video_id": youtube_video_id,
        "collected_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "views": int(stats.get("viewCount", 0)),
        "likes": int(stats.get("likeCount", 0)),
        "comments": int(stats.get("commentCount", 0)),
        "favorites": int(stats.get("favoriteCount", 0)),
        "duration": details.get("duration", ""),
    }


class AnalyticsScopeMissing(RuntimeError):
    """Raised when the token cannot read Analytics, only the Data API."""


def _channel_selector(channel_id: str | None = None) -> str:
    """Which channel to report on.

    ``channel==MINE`` means "the channel this token belongs to", which is
    always the right answer here and saves having to store a channel id.
    """
    explicit = channel_id or os.getenv("YOUTUBE_CHANNEL_ID", "").strip()
    return f"channel=={explicit}" if explicit else "channel==MINE"


def _analytics_query(params: dict, *, timeout: int = 20) -> dict:
    """Run one Analytics API query, naming the scope problem when it is one."""
    token = get_access_token()
    request = Request(
        f"{ANALYTICS_URL}?{urlencode(params)}",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read().decode("utf-8", "replace")).get("error", {}).get("message", "")
        except (json.JSONDecodeError, ValueError):
            pass
        if exc.code in (401, 403):
            raise AnalyticsScopeMissing(
                "YouTube Analytics izni yok. YOUTUBE_REFRESH_TOKEN'ı "
                "yt-analytics.readonly kapsamıyla birlikte yeniden üret "
                f"({detail or f'HTTP {exc.code}'})"
            ) from exc
        raise RuntimeError(f"YouTube Analytics API error: HTTP {exc.code} {detail}") from exc


def _first_row(payload: dict) -> dict[str, float]:
    """Flatten a single-row Analytics response into {metric: value}.

    An empty ``rows`` is normal rather than an error: a video published today
    may have no processed analytics yet.
    """
    rows = payload.get("rows") or []
    if not rows:
        return {}
    headers = [str(column.get("name", "")) for column in payload.get("columnHeaders", [])]
    return {name: value for name, value in zip(headers, rows[0]) if isinstance(value, (int, float))}


# Retention is the metric Shorts distribution actually turns on. The rest are
# here because they cost nothing extra in the same request.
RETENTION_METRICS = (
    "views,averageViewDuration,averageViewPercentage,"
    "estimatedMinutesWatched,subscribersGained,shares,likes"
)


def get_video_retention(
    youtube_video_id: str,
    *,
    days: int = 90,
    channel_id: str | None = None,
) -> dict[str, float]:
    """Return lifetime-to-date retention figures for one video.

    ``averageViewPercentage`` is the number worth reading: how much of the clip
    the average viewer watched. Above ~90% on a Short usually means loops.
    """
    end_date = datetime.now(UTC).date()
    payload = _analytics_query({
        "ids": _channel_selector(channel_id),
        "startDate": (end_date - timedelta(days=days)).isoformat(),
        "endDate": end_date.isoformat(),
        "metrics": RETENTION_METRICS,
        "filters": f"video=={youtube_video_id}",
    })
    return _first_row(payload)


def get_traffic_sources(
    youtube_video_id: str,
    *,
    days: int = 90,
    channel_id: str | None = None,
) -> dict[str, int]:
    """Return views per traffic source for one video, largest first.

    ``SHORTS`` is the Shorts feed — the only source that scales. A video whose
    views are mostly ``NO_LINK_OTHER`` or ``SUBSCRIBER`` was never picked up.
    """
    end_date = datetime.now(UTC).date()
    payload = _analytics_query({
        "ids": _channel_selector(channel_id),
        "startDate": (end_date - timedelta(days=days)).isoformat(),
        "endDate": end_date.isoformat(),
        "metrics": "views",
        "dimensions": "insightTrafficSourceType",
        "filters": f"video=={youtube_video_id}",
        "sort": "-views",
    })
    sources: dict[str, int] = {}
    for row in payload.get("rows") or []:
        if len(row) >= 2:
            sources[str(row[0])] = int(row[1])
    return sources


def get_analytics_report(
    youtube_video_id: str,
    *,
    days: int = 28,
    channel_id: str | None = None,
) -> dict:
    """Fetch day-by-day analytics for a specific video over the last N days.

    Requires the YouTube Analytics scope.
    """
    end_date = datetime.now(UTC).date()
    return _analytics_query({
        "ids": _channel_selector(channel_id),
        "startDate": (end_date - timedelta(days=days)).isoformat(),
        "endDate": end_date.isoformat(),
        "metrics": RETENTION_METRICS,
        "dimensions": "day",
        "filters": f"video=={youtube_video_id}",
        "sort": "day",
    })


def save_analytics_snapshot(
    video_directory: Path,
    stats: dict,
    label: str = "snapshot",
) -> Path:
    """Save analytics data to the video's analytics directory."""
    from .project import ANALYTICS_DIR

    analytics_dir = video_directory / ANALYTICS_DIR
    analytics_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest = analytics_dir / f"analytics_{label}_{timestamp}.json"
    dest.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Analytics saved: %s", dest.name)
    return dest


def generate_performance_summary(stats: dict) -> str:
    """Generate a human-readable performance summary from video stats."""
    views = stats.get("views", 0)
    likes = stats.get("likes", 0)
    comments = stats.get("comments", 0)
    return (
        f"📊 Performance:\n"
        f"  👁 Views: {views:,}\n"
        f"  👍 Likes: {likes:,}\n"
        f"  💬 Comments: {comments:,}\n"
        f"  📈 Engagement: {(likes + comments) / max(views, 1) * 100:.1f}%"
    )
