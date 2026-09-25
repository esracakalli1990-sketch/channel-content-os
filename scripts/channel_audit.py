"""Dump everything the channel's own APIs know, as tables, for a full review.

The collection itself lives in channel_ops.channel_data so the analysis reads
exactly the same numbers; this file is only the printing.

Read-only. Nothing here writes to the repo or to YouTube.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from channel_ops.channel_data import collect, per_video  # noqa: E402
from channel_ops.config_loader import find_project_root  # noqa: E402


def main() -> None:
    _emit(collect(find_project_root()))


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
    by_id = per_video(dump)

    print("\n### VIDEOLAR")
    print("\t".join((
        "no", "yayin", "yaratik", "id", "gorunurluk", "yukleme", "islem",
        "ret", "sure", "cocuk", "boyut", "izlenme", "begeni", "yorum", "izl%",
        "izl_sn", "abone", "paylasim", "kanca", "rozet", "sablon", "fikir",
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
            data.get("upload", "-"),
            data.get("processing", "-"),
            data.get("rejection") or data.get("failure") or "-",
            (data.get("duration", "") or "-").replace("PT", ""),
            data.get("kids", "-"),
            data.get("size", "-"),
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
            (record.get("idea_version") or "-")[:8],
        )))

    long_form = dump.get("long_form") or []
    if long_form:
        # Watch HOURS, not views, are what the 4,000-hour threshold is counted
        # in, and estimatedMinutesWatched is the figure YouTube itself counts --
        # so it is printed rather than re-derived from views x duration, which
        # only approximates it.
        print("\n### UZUN VIDEOLAR")
        print("\t".join((
            "no", "yayin", "id", "dk", "klip", "gorunurluk", "izlenme",
            "begeni", "yorum", "izl%", "izl_sn", "izleme_saati", "esik%",
            "abone", "paylasim",
        )))
        for index, record in enumerate(long_form, 1):
            vid = record.get("youtube_video_id", "")
            data = stats.get(vid, {})
            live = by_id.get(vid, {})
            minutes_watched = live.get("estimatedMinutesWatched", 0) or 0
            hours = minutes_watched / 60
            print("\t".join(str(cell) for cell in (
                index,
                (record.get("published_at") or "")[:16],
                vid,
                record.get("minutes", "-"),
                record.get("clip_count", "-"),
                data.get("privacy", "-"),
                data.get("views", "-"),
                data.get("likes", "-"),
                data.get("comments", "-"),
                round(live.get("averageViewPercentage", 0) or 0, 1) or "-",
                round(live.get("averageViewDuration", 0) or 0) or "-",
                round(hours, 1),
                round(hours / 4000 * 100, 3),
                live.get("subscribersGained", "-"),
                live.get("shares", "-"),
            )))

    sections = ["ctr", "daily", "traffic", "devices", "subs_status", "countries"]
    sections += sorted(
        name for name in (dump.get("analytics") or {}) if name.startswith("traffic_")
    )
    for section in sections:
        block = (dump.get("analytics") or {}).get(section) or {}
        if not block.get("rows"):
            continue
        print(f"\n### {section.upper()}")
        print("\t".join(block.get("columns") or []))
        for row in block["rows"]:
            print("\t".join(str(cell) for cell in row))

    # Repeated at the end: a log is read from the bottom, and the copy at the
    # top scrolls out of reach behind two hundred lines of table.
    print("\n### TOPLAMLAR (tekrar)")
    for key, value in (dump.get("totals") or {}).items():
        print(f"{key}\t{value}")
    for key, value in (dump.get("errors") or {}).items():
        print(f"HATA {key}\t{value}")


if __name__ == "__main__":
    main()
