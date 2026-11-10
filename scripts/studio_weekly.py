#!/usr/bin/env python3
"""Weekly scorecard from a YouTube Studio content export.

Usage:  python3 scripts/studio_weekly.py <export.zip | "Tablo verileri.csv"> [weeks]

The one number the 10 November roadmap tracks is the share of a week's
videos whose "stayed to watch" rate (Studio: "İzlemeye devam edenler (%)") is
70% or more. That rate is not in the Analytics API, so the export has to be
downloaded by hand: Studio → Analytics → Advanced mode → Content, lifetime,
Export → CSV. This script reads it and prints the last few publish weeks.

On 10 November the share was 25% (35 of 141 videos since 7 September);
the target is 50% by 1 December.
"""
from __future__ import annotations

import csv
import io
import statistics
import sys
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

STAY = "İzlemeye devam edenler (%)"
VIEWS = "Görüntüleme"
PUBLISHED = "Videonun yayınlanma tarihi"
TITLE = "Video başlığı"
TABLE = "Tablo verileri.csv"
THRESHOLD = 70.0


def read_rows(path: Path) -> list[dict[str, str]]:
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            name = next(n for n in archive.namelist() if n.endswith(TABLE))
            text = archive.read(name).decode("utf-8-sig")
    else:
        text = path.read_text(encoding="utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def videos(rows: list[dict[str, str]]) -> list[dict]:
    out = []
    for row in rows:
        try:
            published = datetime.strptime(row[PUBLISHED], "%b %d, %Y").date()
            stay = float(row[STAY])
        except (KeyError, ValueError):
            continue  # the "Toplam" row, or a video without the rate yet
        out.append({
            "id": row.get("İçerik", ""),
            "title": row.get(TITLE, ""),
            "published": published,
            "stay": stay,
            "views": int(float(row.get(VIEWS) or 0)),
        })
    return out


def _n(value: float) -> str:
    """Turkish thousands separator: 12.779"""
    return f"{value:,.0f}".replace(",", ".")


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def scorecard(items: list[dict], weeks: int = 4) -> str:
    if not items:
        return "Dışa aktarımda izlemeye devam oranı olan video yok."
    latest = week_start(max(v["published"] for v in items))
    lines = ["Haftalık karne (izlemeye devam ≥ %70 payı)", ""]
    for back in range(weeks - 1, -1, -1):
        start = latest - timedelta(weeks=back)
        week = [v for v in items if week_start(v["published"]) == start]
        if not week:
            continue
        good = sum(v["stay"] >= THRESHOLD for v in week)
        lines.append(
            f"{start:%d.%m} haftası: {len(week)} video, ≥%70: {good} "
            f"(%{100 * good / len(week):.0f}), medyan oran %{statistics.median(v['stay'] for v in week):.1f}, "
            f"medyan izlenme {_n(statistics.median(v['views'] for v in week))}"
        )
    # The newest week is usually a day or two old; rank the last full one.
    last = [v for v in items if week_start(v["published"]) == latest]
    if len(last) < 7:
        latest -= timedelta(weeks=1)
        last = [v for v in items if week_start(v["published"]) == latest]
    ranked = sorted(last, key=lambda v: v["stay"], reverse=True)
    row = lambda v: f"  %{v['stay']:.0f}  {_n(v['views']):>9}  {v['title'][:60]}"
    lines += ["", f"{latest:%d.%m} haftasının en iyileri:"]
    lines += [row(v) for v in ranked[:3]]
    lines += [f"{latest:%d.%m} haftasının en zayıfları:"]
    lines += [row(v) for v in ranked[-3:]]
    lines += ["", "Hedef: 1 Aralık'a kadar %50. Son haftanın videoları henüz genç; izlenme rakamları artacak, oran pek değişmez."]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    weeks = int(argv[2]) if len(argv) > 2 else 4
    print(scorecard(videos(read_rows(Path(argv[1]))), weeks))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
