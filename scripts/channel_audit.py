"""Dump everything the channel's own APIs know, as JSON, for a full review.

The performance report that goes to Telegram is written for a phone: ten
videos, rounded numbers, no history. Judging whether a change to the pipeline
helped needs the opposite — every video, every day, and the fields that say
which version of the pipeline produced it. This prints that as one JSON blob
so it can be read out of the workflow log.

Read-only. Nothing here writes to the repo or to YouTube.
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from channel_ops.config_loader import find_project_root  # noqa: E402
from channel_ops.youtube_analytics import (  # noqa: E402
    ANALYTICS_URL,
    DATA_URL,
    AnalyticsScopeMissing,
    _analytics_query,
    _channel_selector,
    get_channel_totals,
)
from channel_ops.youtube_auth import get_access_token  # noqa: E402

# The Data API takes fifty ids per call; asking one at a time would be fifty
# round trips for the same answer.
BATCH = 50


def _data_stats(video_ids: list[str]) -> dict[str, dict]:
    token = get_access_token()
    out: dict[str, dict] = {}
    for start in range(0, len(video_ids), BATCH):
        chunk = video_ids[start:start + BATCH]
        params = urlencode({
            "part": "statistics,contentDetails,snippet,status",
            "id": ",".join(chunk),
        })
        request = Request(f"{DATA_URL}?{params}", headers={"Authorization": f"Bearer {token}"})
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
        for item in payload.get("items", []):
            stats = item.get("statistics", {})
            out[item["id"]] = {
                "views": int(stats.get("viewCount", 0)),
                "likes": int(stats.get("likeCount", 0)),
                "comments": int(stats.get("commentCount", 0)),
                "duration": item.get("contentDetails", {}).get("duration", ""),
                "privacy": item.get("status", {}).get("privacyStatus", ""),
                "title": item.get("snippet", {}).get("title", ""),
            }
    return out


def _rows(dimensions: str, metrics: str, *, days: int, sort: str = "", filters: str = "") -> dict:
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
    payload = _analytics_query(params, timeout=60)
    headers = [c.get("name", "") for c in payload.get("columnHeaders", [])]
    return {"columns": headers, "rows": payload.get("rows") or []}


def main() -> None:
    root = find_project_root()
    published = json.loads((root / "data" / "shorts_published.json").read_text("utf-8"))
    video_ids = [r["youtube_video_id"] for r in published if r.get("youtube_video_id")]

    dump: dict = {
        "collected_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "published": published,
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

    # Per-video lifetime analytics, day-by-day channel views, and the traffic
    # mix. Each is asked for separately so one failure does not blank the rest.
    queries = {
        "per_video": dict(
            dimensions="video",
            metrics=("views,estimatedMinutesWatched,averageViewDuration,"
                     "averageViewPercentage,subscribersGained,likes,shares,comments"),
            days=90,
            sort="-views",
        ),
        "daily": dict(
            dimensions="day",
            metrics="views,estimatedMinutesWatched,subscribersGained,subscribersLost",
            days=90,
            sort="day",
        ),
        "traffic": dict(
            dimensions="insightTrafficSourceType",
            metrics="views,estimatedMinutesWatched",
            days=28,
            sort="-views",
        ),
        "countries": dict(dimensions="country", metrics="views", days=28, sort="-views"),
        "devices": dict(dimensions="deviceType", metrics="views", days=28, sort="-views"),
        "subs_status": dict(
            dimensions="subscribedStatus", metrics="views,averageViewPercentage", days=28
        ),
    }
    for name, kwargs in queries.items():
        try:
            dump["analytics"][name] = _rows(**kwargs)
        except AnalyticsScopeMissing as exc:
            dump["errors"][name] = f"scope: {exc}"
        except Exception as exc:
            dump["errors"][name] = str(exc)

    _emit(dump)


def _emit(dump: dict) -> None:
    """Print the dump as short tab-separated tables.

    One long JSON line is unreadable in a workflow log and unreadable to
    anything that has to quote it back. Tables of a few hundred short lines
    say the same thing and can be scanned by eye.
    """
    print("### TOPLAMLAR")
    for key, value in (dump.get("totals") or {}).items():
        print(f"{key}\t{value}")

    print("\n### HATALAR")
    for key, value in (dump.get("errors") or {}).items():
        print(f"{key}\t{value}")
    if not dump.get("errors"):
        print("(yok)")

    stats = dump.get("data_api") or {}
    by_id = {}
    for section in ("per_video",):
        block = (dump.get("analytics") or {}).get(section) or {}
        columns = block.get("columns") or []
        for row in block.get("rows") or []:
            record = dict(zip(columns, row))
            by_id[record.get("video")] = record

    print("\n### VIDEOLAR")
    print("\t".join((
        "no", "yayin", "yaratik", "id", "gorunurluk", "izlenme", "begeni",
        "yorum", "izl%", "izl_sn", "abone", "paylasim", "kanca", "rozet", "sablon",
    )))
    for index, record in enumerate(dump.get("published") or [], 1):
        vid = record.get("youtube_video_id", "")
        data = stats.get(vid, {})
        live = by_id.get(vid, {})
        print("\t".join(str(cell) for cell in (
            index,
            (record.get("published_at") or "")[:16],
            record.get("creature", ""),
            vid,
            data.get("privacy", "-"),
            data.get("views", "-"),
            data.get("likes", "-"),
            data.get("comments", "-"),
            round(live.get("averageViewPercentage", 0) or 0, 1) or "-",
            round(live.get("averageViewDuration", 0) or 0) or "-",
            live.get("subscribersGained", "-"),
            live.get("shares", "-"),
            "var" if record.get("hook") else "yok",
            "var" if record.get("badge") else "yok",
            (record.get("template_version") or "-")[:8],
        )))

    for section in ("daily", "traffic", "countries", "devices", "subs_status"):
        block = (dump.get("analytics") or {}).get(section) or {}
        if not block.get("rows"):
            continue
        print(f"\n### {section.upper()}")
        print("\t".join(block.get("columns") or []))
        for row in block["rows"]:
            print("\t".join(str(cell) for cell in row))


if __name__ == "__main__":
    main()
