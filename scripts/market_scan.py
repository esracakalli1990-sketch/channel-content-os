"""How much do tutorials on how these videos are made get watched?

Read-only. Searches YouTube for tutorial queries, then reads each result's
view count, length, age and its channel's subscriber count, and prints a
table per query plus a JSON dump. Nothing is written to the repo or YouTube.

Quota: 100 units per search, about 1 per stats call -- roughly 700 units
for the default queries, against 10,000 a day (an upload costs 1,600).
"""
from __future__ import annotations

import json
import re
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from channel_ops.youtube_auth import get_access_token  # noqa: E402

API = "https://www.googleapis.com/youtube/v3"

QUERIES = [
    "how to make ai transformation video",
    "veo 3 prompt tutorial",
    "google flow ai video tutorial",
    "how to make satisfying ai videos",
    "how to make viral ai shorts tutorial",
    "ai mechanical transformation video how to make",
]


def _get(path: str, **params) -> dict:
    request = Request(
        f"{API}/{path}?{urlencode(params)}",
        headers={"Authorization": f"Bearer {get_access_token()}"},
    )
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def _seconds(iso: str) -> int:
    match = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not match:
        return 0
    d, h, m, s = (int(x or 0) for x in match.groups())
    return d * 86400 + h * 3600 + m * 60 + s


def scan(query: str) -> list[dict]:
    found = _get(
        "search", part="snippet", q=query, type="video", maxResults=25,
        order="relevance", relevanceLanguage="en", regionCode="US",
    )
    ids = [item["id"]["videoId"] for item in found.get("items", [])]
    if not ids:
        return []
    videos = _get("videos", part="statistics,contentDetails,snippet", id=",".join(ids))
    channel_ids = sorted({v["snippet"]["channelId"] for v in videos.get("items", [])})
    channels = _get("channels", part="statistics", id=",".join(channel_ids))
    subs = {
        c["id"]: int(c["statistics"].get("subscriberCount", 0) or 0)
        for c in channels.get("items", [])
    }
    now = datetime.now(UTC)
    rows = []
    for v in videos.get("items", []):
        published = datetime.fromisoformat(v["snippet"]["publishedAt"].replace("Z", "+00:00"))
        age = max((now - published).days, 1)
        views = int(v["statistics"].get("viewCount", 0) or 0)
        rows.append({
            "id": v["id"],
            "title": v["snippet"]["title"],
            "channel": v["snippet"]["channelTitle"],
            "channel_subs": subs.get(v["snippet"]["channelId"], 0),
            "published": published.date().isoformat(),
            "age_days": age,
            "seconds": _seconds(v["contentDetails"].get("duration", "")),
            "views": views,
            "views_per_day": round(views / age, 1),
        })
    return rows


def _summary(rows: list[dict]) -> str:
    if not rows:
        return "  (sonuç yok)"
    long_rows = [r for r in rows if r["seconds"] >= 240]
    small = [r for r in long_rows if r["channel_subs"] < 20_000]
    lines = []
    for label, part in (("tümü", rows), ("uzun (4dk+)", long_rows), ("uzun + kanal <20 bin abone", small)):
        if part:
            lines.append(
                f"  {label:28} n={len(part):2}  medyan izlenme {statistics.median(r['views'] for r in part):>10,.0f}"
                f"  medyan günlük {statistics.median(r['views_per_day'] for r in part):>8,.1f}"
            )
    return "\n".join(lines)


def main() -> None:
    everything = {}
    for query in QUERIES:
        rows = scan(query)
        everything[query] = rows
        print(f"\n=== {query} ===")
        print(_summary(rows))
        for r in sorted(rows, key=lambda r: -r["views"]):
            print(
                f"  {r['views']:>10,}  {r['views_per_day']:>9,.1f}/gün  {r['seconds'] // 60:>3}dk"
                f"  {r['age_days']:>4}g  abone {r['channel_subs']:>9,}  {r['title'][:60]}"
            )
    print("\nJSON_DUMP_BEGIN")
    print(json.dumps(everything, ensure_ascii=False))
    print("JSON_DUMP_END")


if __name__ == "__main__":
    main()
