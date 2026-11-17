"""Who else publishes mechanical-transformation Shorts, and how do they do?

Read-only. Finds channels through Shorts searches, then for the busiest ones
reads their recent uploads (views, likes, comments, length, titles) and the
top comments on their best videos. Prints tables plus a JSON dump; writes
nothing to the repo or to YouTube.

Quota: ~100 per search (6), ~1 per other call -- about 700-900 units.
"""
from __future__ import annotations

import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from channel_ops.youtube_auth import get_access_token  # noqa: E402

API = "https://www.googleapis.com/youtube/v3"

QUERIES = [
    "mechanical transformation",
    "mechanical toy transformation",
    "mechanical animal transformation",
    "toy unfolds into mechanical",
    "satisfying mechanical transformation ai",
    "tiny mechanical toy transforms",
]
CHANNELS_TO_PROFILE = 15
UPLOADS_PER_CHANNEL = 50


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


def _videos(ids: list[str]) -> list[dict]:
    out = []
    for start in range(0, len(ids), 50):
        chunk = ids[start:start + 50]
        data = _get("videos", part="statistics,contentDetails,snippet", id=",".join(chunk))
        now = datetime.now(UTC)
        for v in data.get("items", []):
            st = v.get("statistics", {})
            published = datetime.fromisoformat(v["snippet"]["publishedAt"].replace("Z", "+00:00"))
            out.append({
                "id": v["id"],
                "channel_id": v["snippet"]["channelId"],
                "channel": v["snippet"]["channelTitle"],
                "title": v["snippet"]["title"],
                "published": published.date().isoformat(),
                "age_days": max((now - published).days, 1),
                "seconds": _seconds(v["contentDetails"].get("duration", "")),
                "views": int(st.get("viewCount", 0) or 0),
                "likes": int(st.get("likeCount", 0) or 0),
                "comments": int(st.get("commentCount", 0) or 0),
            })
    return out


def _comments(video_id: str, n: int = 8) -> list[str]:
    try:
        data = _get("commentThreads", part="snippet", videoId=video_id, maxResults=n,
                    order="relevance", textFormat="plainText")
    except HTTPError:
        return []  # comments disabled
    return [
        " ".join(item["snippet"]["topLevelComment"]["snippet"]["textDisplay"].split())[:160]
        for item in data.get("items", [])
    ]


def main() -> None:
    hits: list[dict] = []
    for query in QUERIES:
        found = _get("search", part="snippet", q=query, type="video", videoDuration="short",
                     maxResults=50, order="viewCount", publishedAfter="2026-05-01T00:00:00Z")
        ids = [i["id"]["videoId"] for i in found.get("items", [])]
        hits.extend(_videos(ids))

    by_channel: dict[str, list[dict]] = defaultdict(list)
    seen = set()
    for v in hits:
        if v["id"] not in seen:
            seen.add(v["id"])
            by_channel[v["channel_id"]].append(v)
    ranked = sorted(by_channel, key=lambda c: -sum(v["views"] for v in by_channel[c]))
    print(f"Aramalarda {len(seen)} video, {len(by_channel)} kanal bulundu.")

    # Our own channel, for comparison.
    mine = _get("channels", part="id", mine="true").get("items", [{}])[0].get("id")
    profile_ids = ranked[:CHANNELS_TO_PROFILE]
    if mine and mine not in profile_ids:
        profile_ids.append(mine)

    report = []
    for start in range(0, len(profile_ids), 50):
        chans = _get("channels", part="statistics,snippet,contentDetails",
                     id=",".join(profile_ids[start:start + 50]))
        for c in chans.get("items", []):
            uploads = c["contentDetails"]["relatedPlaylists"]["uploads"]
            items = _get("playlistItems", part="contentDetails", playlistId=uploads,
                         maxResults=UPLOADS_PER_CHANNEL).get("items", [])
            recent = _videos([i["contentDetails"]["videoId"] for i in items])
            st = c["statistics"]
            report.append({
                "id": c["id"],
                "name": c["snippet"]["title"],
                "country": c["snippet"].get("country", ""),
                "created": c["snippet"]["publishedAt"][:10],
                "subs": int(st.get("subscriberCount", 0) or 0),
                "views": int(st.get("viewCount", 0) or 0),
                "videos": int(st.get("videoCount", 0) or 0),
                "is_us": c["id"] == mine,
                "recent": recent,
            })

    for ch in sorted(report, key=lambda r: -r["subs"]):
        rec = ch["recent"]
        if not rec:
            continue
        views = [v["views"] for v in rec]
        shorts = [v for v in rec if v["seconds"] <= 180]
        span = max(v["age_days"] for v in rec) - min(v["age_days"] for v in rec) + 1
        like_rate = sum(v["likes"] for v in rec) / max(sum(views), 1) * 100
        comment_rate = sum(v["comments"] for v in rec) / max(sum(views), 1) * 1000
        tag = "  <== BİZ" if ch["is_us"] else ""
        print(
            f"\n### {ch['name']}{tag}  ({ch['country'] or '?'}, açılış {ch['created']})\n"
            f"  abone {ch['subs']:,}  toplam izlenme {ch['views']:,}  video {ch['videos']}\n"
            f"  son {len(rec)} video: medyan {statistics.median(views):,.0f}  ortalama {statistics.mean(views):,.0f}"
            f"  en yüksek {max(views):,}  50k+ olan {sum(v >= 50_000 for v in views)}\n"
            f"  Shorts payı {len(shorts)}/{len(rec)}  sıklık ~{len(rec) / span * 7:.1f}/hafta"
            f"  beğeni %{like_rate:.2f}  yorum/1000 izlenme {comment_rate:.2f}"
        )
        for v in sorted(rec, key=lambda v: -v["views"])[:5]:
            print(f"    {v['views']:>11,}  {v['published']}  {v['seconds']}s  {v['title'][:70]}")
        for v in sorted(rec, key=lambda v: v["views"])[:2]:
            print(f"    (en düşük) {v['views']:>7,}  {v['published']}  {v['title'][:60]}")
        if not ch["is_us"]:
            best = sorted(rec, key=lambda v: -v["views"])[:2]
            for v in best:
                for text in _comments(v["id"], 6):
                    print(f"      💬 {text}")

    words = Counter()
    for ch in report:
        if ch["is_us"]:
            continue
        for v in sorted(ch["recent"], key=lambda v: -v["views"])[:10]:
            words.update(w.lower() for w in re.findall(r"[A-Za-z]{4,}", v["title"]))
    print("\nRakiplerin en iyi videolarının başlıklarında sık kelimeler:")
    print("  " + ", ".join(f"{w} ({n})" for w, n in words.most_common(40)))

    print("\nJSON_DUMP_BEGIN")
    print(json.dumps({"report": report}, ensure_ascii=False))
    print("JSON_DUMP_END")


if __name__ == "__main__":
    main()
