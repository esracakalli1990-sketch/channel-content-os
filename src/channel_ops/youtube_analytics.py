"""YouTube Analytics data collection.

Pulls performance metrics from the YouTube Analytics API
and generates structured reports for each video.

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

logger = logging.getLogger(__name__)

ANALYTICS_URL = "https://youtubeanalytics.googleapis.com/v2/reports"
DATA_URL = "https://www.googleapis.com/youtube/v3/videos"


def _load_token() -> str:
    token_path = os.getenv("YOUTUBE_TOKEN_PATH", "")
    if not token_path or not Path(token_path).exists():
        raise RuntimeError("YouTube OAuth token not found.")
    data = json.loads(Path(token_path).read_text(encoding="utf-8"))
    return data.get("access_token", "")


def get_video_stats(youtube_video_id: str) -> dict:
    """Fetch basic statistics for a video (views, likes, comments)."""
    token = _load_token()
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


def get_analytics_report(
    youtube_video_id: str,
    *,
    days: int = 28,
    channel_id: str | None = None,
) -> dict:
    """Fetch analytics for a specific video over the last N days.

    Requires YouTube Analytics API scope.
    """
    token = _load_token()
    ch_id = channel_id or os.getenv("YOUTUBE_CHANNEL_ID", "")
    if not ch_id:
        raise RuntimeError("YOUTUBE_CHANNEL_ID required for analytics.")

    end_date = datetime.now(UTC).date()
    start_date = end_date - timedelta(days=days)

    params = urlencode({
        "ids": f"channel=={ch_id}",
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "metrics": "views,estimatedMinutesWatched,averageViewDuration,subscribersGained,likes,shares",
        "dimensions": "day",
        "filters": f"video=={youtube_video_id}",
        "sort": "day",
    })

    request = Request(
        f"{ANALYTICS_URL}?{params}",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urlopen(request, timeout=15) as response:
            return json.load(response)
    except HTTPError as exc:
        raise RuntimeError(f"YouTube Analytics API error: HTTP {exc.code}") from exc


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
