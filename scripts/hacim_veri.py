"""Per-video daily views for the volume test (docs/HACIM_DENEYI.md).

The audit's VIDEOLAR table gives every video's views as of today, so a video
from the comparison period (4-17 Oct) is always two weeks older than one from
the test (18-31 Oct) and raw totals favour the older one. The 48-hour dead rule
and the day-7 stop check also need each video's views at the same age. Only a
daily series per video gives that, and Analytics keeps it for the comparison
period too.

Read-only, printed as tables like the rest of the audit. Kept out of
channel_data.collect so the shared reporting code is untouched.
"""
from __future__ import annotations

from channel_ops.channel_data import load_records, rows

FIRST_DAY = "2026-10-04"
LAST_DAY = "2026-11-01"
DAYS = 60


def emit(root=None) -> None:
    published, _ = load_records(root)

    print("\n### HACIM_KANAL_GUNLUK")
    print("gun\tizlenme\tetkilesimli")
    try:
        block = rows(dimensions="day", metrics="views,engagedViews", days=DAYS, sort="day")
        for day, views, engaged in block["rows"]:
            print(f"{day}\t{views}\t{engaged}")
    except Exception as exc:
        print(f"HATA kanal\t{exc}")

    print("\n### HACIM_VIDEO_GUNLUK")
    print("id\tgun\tizlenme\tetkilesimli")
    for record in published:
        vid = record.get("youtube_video_id")
        if not vid or record.get("youtube_deleted"):
            continue
        if not FIRST_DAY <= (record.get("published_at") or "")[:10] <= LAST_DAY:
            continue
        try:
            block = rows(
                dimensions="day", metrics="views,engagedViews", days=DAYS,
                sort="day", filters=f"video=={vid}",
            )
        except Exception as exc:
            print(f"HATA {vid}\t{exc}")
            continue
        for day, views, engaged in block["rows"]:
            print(f"{vid}\t{day}\t{views}\t{engaged}")
