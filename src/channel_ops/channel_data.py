"""Everything the channel's own APIs know, collected once into one dict.

Two consumers need the same numbers: the audit, which prints them, and the
analysis, which judges them. Collecting twice would mean two sets of API calls
that can disagree with each other about what day it is, so it happens here and
both read the result.

Read-only. Nothing here writes to the repo or to YouTube.

A note on what is NOT in here, because it keeps being asked for: impressions
and impressionClickThroughRate. They are Studio-only. The Analytics API answers
``HTTP 400 Unknown identifier (impressions)`` -- this was tried, not assumed.
Click-through rate therefore cannot be measured by any automated part of this
system, and anything that claims to is making it up.
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .config_loader import find_project_root
from .youtube_analytics import (
    ANALYTICS_URL,  # noqa: F401  (re-exported for callers that build their own queries)
    DATA_URL,
    AnalyticsScopeMissing,
    _analytics_query,
    _channel_selector,
    get_channel_totals,
)
from .youtube_auth import get_access_token

# The Data API takes fifty ids per call; asking one at a time would be fifty
# round trips for the same answer.
BATCH = 50

# A Short is at most sixty seconds. The distinction matters more than it looks:
# Shorts watch time does not count toward the Partner Programme's 4,000 hours,
# so the two kinds of video have to be totalled separately or the threshold
# reads as much closer than it is.
SHORT_MAX_SECONDS = 60


def _data_stats(video_ids: list[str]) -> dict[str, dict]:
    """Public counters, straight from the Data API, with no reporting lag."""
    token = get_access_token()
    out: dict[str, dict] = {}
    for start in range(0, len(video_ids), BATCH):
        chunk = video_ids[start:start + BATCH]
        params = urlencode({
            # processingDetails and the full status block are what separate
            # "nobody watched it" from "YouTube never finished with it": a
            # rejected or still-processing video is public and unwatchable.
            # fileDetails carries the pixel dimensions of what was actually
            # uploaded. A clip that stops being 9:16 stops being a Short and
            # leaves the Shorts feed entirely, which looks exactly like being
            # throttled -- so it has to be ruled in or out, not assumed.
            "part": ("statistics,contentDetails,snippet,status,"
                     "processingDetails,fileDetails"),
            "id": ",".join(chunk),
        })
        request = Request(f"{DATA_URL}?{params}", headers={"Authorization": f"Bearer {token}"})
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
        for item in payload.get("items", []):
            stats = item.get("statistics", {})
            status = item.get("status", {})
            details = item.get("processingDetails", {})
            content = item.get("contentDetails", {})
            duration = content.get("duration", "")
            out[item["id"]] = {
                "views": int(stats.get("viewCount", 0)),
                "likes": int(stats.get("likeCount", 0)),
                "comments": int(stats.get("commentCount", 0)),
                "duration": duration,
                "seconds": parse_duration(duration),
                "privacy": status.get("privacyStatus", ""),
                "upload": status.get("uploadStatus", ""),
                "rejection": status.get("rejectionReason", ""),
                "failure": status.get("failureReason", ""),
                "processing": details.get("processingStatus", ""),
                "kids": status.get("madeForKids", ""),
                "size": _resolution(item.get("fileDetails", {})),
                "embeddable": status.get("embeddable", ""),
                "title": item.get("snippet", {}).get("title", ""),
            }
    return out


def parse_duration(iso: str) -> int:
    """Seconds in an ISO-8601 duration like PT9S or PT1H2M3S.

    Written out rather than pulled from a library because the only shapes that
    occur here are minutes and seconds, and a wrong answer silently reclassifies
    a long video as a Short -- which is the one distinction the watch-hour
    threshold turns on.
    """
    if not iso.startswith("PT"):
        return 0
    total, number = 0, ""
    for char in iso[2:]:
        if char.isdigit():
            number += char
            continue
        if not number:
            continue
        value = int(number)
        total += value * {"H": 3600, "M": 60, "S": 1}.get(char, 0)
        number = ""
    return total


def _resolution(file_details: dict) -> str:
    """WxH of the first video stream, or "-" when YouTube did not report one."""
    for stream in file_details.get("videoStreams") or []:
        width, height = stream.get("widthPixels"), stream.get("heightPixels")
        if width and height:
            return f"{width}x{height}"
    return "-"


def rows(
    dimensions: str,
    metrics: str,
    *,
    days: int,
    sort: str = "",
    filters: str = "",
    max_results: int = 0,
) -> dict:
    """One Analytics query returned with its column names attached."""
    end = date.today()
    params = {
        "ids": _channel_selector(),
        "startDate": (end - timedelta(days=days)).isoformat(),
        "endDate": end.isoformat(),
        "metrics": metrics,
        "dimensions": dimensions,
    }
    if sort:
        params["sort"] = sort
    if filters:
        params["filters"] = filters
    if max_results:
        # The video dimension is a "top videos" report; without maxResults the
        # API answers with no rows at all rather than an error.
        params["maxResults"] = max_results
    payload = _analytics_query(params, timeout=60)
    headers = [c.get("name", "") for c in payload.get("columnHeaders", [])]
    return {"columns": headers, "rows": payload.get("rows") or []}


def load_records(root: Path | None = None) -> tuple[list, list]:
    """The Shorts and the long-form publish records."""
    base = (root or find_project_root()) / "data"

    def read(name: str) -> list:
        path = base / name
        return json.loads(path.read_text("utf-8")) if path.exists() else []

    return read("shorts_published.json"), read("long_published.json")


def collect(root: Path | None = None) -> dict:
    """Every figure the APIs will give, in one dict, with failures recorded.

    Each query is attempted separately and a failure is written to ``errors``
    rather than raised, so one unavailable metric costs that metric and not the
    whole report.
    """
    published, long_form = load_records(root)
    video_ids = [
        record["youtube_video_id"]
        for record in published + long_form
        if record.get("youtube_video_id")
    ]

    dump: dict = {
        "collected_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "published": published,
        "long_form": long_form,
        "totals": {},
        "data_api": {},
        "analytics": {},
        "errors": {},
    }

    try:
        dump["totals"] = get_channel_totals()
    except Exception as exc:  # a missing figure must not cost the rest
        dump["errors"]["totals"] = str(exc)

    try:
        dump["data_api"] = _data_stats(video_ids)
    except Exception as exc:
        dump["errors"]["data_api"] = str(exc)

    # Each window is the longest the API allows for that report. per_video is
    # capped at 90 days, which is also exactly the window the 10-million-Shorts
    # threshold is counted over -- convenient, and not a coincidence worth
    # relying on if YouTube changes either number.
    queries = {
        "per_video": dict(
            dimensions="video",
            metrics=("views,estimatedMinutesWatched,averageViewDuration,"
                     "averageViewPercentage,subscribersGained,likes,shares,comments"),
            days=90,
            sort="-views",
            max_results=200,
        ),
        "daily": dict(
            dimensions="day",
            metrics="views,estimatedMinutesWatched,subscribersGained,subscribersLost",
            days=45,
            sort="day",
        ),
        "traffic": dict(
            dimensions="insightTrafficSourceType",
            metrics="views,estimatedMinutesWatched",
            days=28,
            sort="-views",
        ),
        "countries": dict(
            dimensions="country", metrics="views", days=28, sort="-views", max_results=15
        ),
        "devices": dict(dimensions="deviceType", metrics="views", days=28, sort="-views"),
        "subs_status": dict(
            dimensions="subscribedStatus", metrics="views,averageViewPercentage", days=28
        ),
    }
    for name, kwargs in queries.items():
        try:
            dump["analytics"][name] = rows(**kwargs)
        except AnalyticsScopeMissing as exc:
            dump["errors"][name] = f"scope: {exc}"
        except Exception as exc:
            dump["errors"][name] = str(exc)

    # Where each long-form video's views actually came from. The channel-wide
    # traffic mix is dominated by Shorts and says nothing about whether YouTube
    # is serving a compilation on its own or whether every view arrived through
    # a link we placed ourselves. Only a per-video filter separates the two.
    for record in long_form:
        vid = record.get("youtube_video_id")
        if not vid:
            continue
        try:
            dump["analytics"][f"traffic_{vid}"] = rows(
                dimensions="insightTrafficSourceType",
                metrics="views,estimatedMinutesWatched",
                days=28,
                sort="-views",
                filters=f"video=={vid}",
            )
        except Exception as exc:
            dump["errors"][f"traffic_{vid}"] = str(exc)

    return dump


def per_video(dump: dict) -> dict[str, dict]:
    """The per-video Analytics rows keyed by video id."""
    block = (dump.get("analytics") or {}).get("per_video") or {}
    columns = block.get("columns") or []
    out = {}
    for row in block.get("rows") or []:
        record = dict(zip(columns, row))
        out[record.get("video")] = record
    return out
