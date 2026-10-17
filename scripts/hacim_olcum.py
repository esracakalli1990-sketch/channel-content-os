"""Pre-registered measurement of the volume test (docs/HACIM_DENEYI.md).

Usage: python scripts/hacim_olcum.py AUDIT_LOG [--ufuk 3] [--kayit data/shorts_published.json]

AUDIT_LOG is the text of a `shorts-tools` audit run made from this branch (it
prints HACIM_KANAL_GUNLUK and HACIM_VIDEO_GUNLUK). Workflow timestamps at the
start of each line are stripped.

Every per-video number is read at the SAME AGE: the views a video had from its
publish day (Analytics day, Pacific time) through publish day + k. A video
whose day + k Analytics has not reported yet is left out and counted, never
filled in. Fixed in advance:

  low         views through day + 2 below 2,000 (comparison's bottom 10%);
              stop if more than 30% of the test videos are low
  dead        views through day + 2 below 250; stop at 4 or more
              (day + 2 is 56-68h depending on slot)
  ufuk (k)    3 for the final report on 6 Nov  (the 31 Oct videos reach it)
  hit         50,000+ views through day + k (primary), 10,000+ (secondary),
              and live Data API views beside them
  Thresholds as written in docs/HACIM_DENEYI.md on 17 Oct (6c84d60), before
  the test began. They are not changed afterwards.
  tests       Mann-Whitney (repo implementation), Fisher exact for counts,
              Holm over the four primary tests; p<0.05 after Holm and at least
              8 mature videos per group, otherwise "yeterli veri yok"

Read-only. Prints a report; writes nothing.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from math import comb
from pathlib import Path
from statistics import mean, median
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from channel_ops.channel_analysis import mann_whitney_p  # noqa: E402

PACIFIC = ZoneInfo("America/Los_Angeles")
COMPARISON = (date(2026, 10, 4), date(2026, 10, 17))
TEST = (date(2026, 10, 18), date(2026, 10, 31))
DEAD_K, DEAD_VIEWS, LOW_VIEWS = 2, 250, 2_000
HIT_VIEWS, HIT_SECONDARY = 50_000, 10_000
MIN_GROUP = 8
STOP_DROP = 0.30
STOP_LOW_SHARE = 0.30
STOP_DEAD = 4


def sections(text: str) -> dict[str, list[list[str]]]:
    out: dict[str, list[list[str]]] = {}
    current = None
    for line in text.splitlines():
        line = re.sub(r"^\S+Z ", "", line).rstrip("\n")
        if line.startswith("### "):
            current = line[4:].strip()
            out[current] = []
        elif current and line.strip():
            out[current].append(line.split("\t"))
    return out


def fisher_p(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher exact p for [[a, b], [c, d]]."""
    row1, col1, n = a + b, a + c, a + b + c + d

    def prob(x: int) -> float:
        return comb(col1, x) * comb(n - col1, row1 - x) / comb(n, row1)

    observed = prob(a)
    low, high = max(0, row1 + col1 - n), min(row1, col1)
    return min(1.0, sum(prob(x) for x in range(low, high + 1) if prob(x) <= observed * (1 + 1e-9)))


def holm(pvalues: list[float]) -> list[float]:
    order = sorted(range(len(pvalues)), key=lambda i: pvalues[i])
    adjusted, previous = [0.0] * len(pvalues), 0.0
    for rank, i in enumerate(order):
        previous = min(1.0, max(previous, (len(pvalues) - rank) * pvalues[i]))
        adjusted[i] = previous
    return adjusted


def in_period(day: date, period: tuple[date, date]) -> bool:
    return period[0] <= day <= period[1]


def fmt(x: float | None) -> str:
    return "-" if x is None else f"{x:,.0f}".replace(",", ".")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("log")
    parser.add_argument("--ufuk", type=int, default=3)
    parser.add_argument("--kayit", default="data/shorts_published.json")
    args = parser.parse_args()
    k = args.ufuk

    tables = sections(Path(args.log).read_text("utf-8"))
    channel = {
        date.fromisoformat(r[0]): (int(r[1]), int(r[2]))
        for r in tables.get("HACIM_KANAL_GUNLUK", [])[1:] if len(r) == 3 and r[0][:2] == "20"
    }
    series: dict[str, dict[date, tuple[int, int]]] = defaultdict(dict)
    errors = []
    for r in tables.get("HACIM_VIDEO_GUNLUK", [])[1:]:
        if r[0].startswith("HATA"):
            errors.append(r)
        elif len(r) == 4:
            series[r[0]][date.fromisoformat(r[1])] = (int(r[2]), int(r[3]))
    live = {}
    head = (tables.get("VIDEOLAR") or [[]])[0]
    for r in (tables.get("VIDEOLAR") or [])[1:]:
        row = dict(zip(head, r))
        try:
            live[row["id"]] = int(row["izlenme"])
        except (KeyError, ValueError):
            pass

    if not channel:
        sys.exit("HACIM_KANAL_GUNLUK yok: audit bu daldan çalıştırılmamış.")
    last_day = max(d for d, (v, _) in channel.items() if v > 0)

    videos = []
    for rec in json.loads(Path(args.kayit).read_text("utf-8")):
        vid = rec.get("youtube_video_id")
        if not vid or rec.get("youtube_deleted"):
            continue
        pub = datetime.fromisoformat(rec["published_at"].replace("Z", "+00:00"))
        utc_day = pub.date()
        period = ("kiyas" if in_period(utc_day, COMPARISON)
                  else "deney" if in_period(utc_day, TEST) else None)
        if not period:
            continue
        pt_day = pub.astimezone(PACIFIC).date()
        s = series.get(vid, {})

        def through(h: int, idx: int, s=s, pt_day=pt_day):
            if pt_day + timedelta(days=h) > last_day:
                return None
            return sum(s.get(pt_day + timedelta(days=i), (0, 0))[idx] for i in range(h + 1))

        videos.append(dict(
            id=vid, cre=rec.get("creature", ""), pub=pub, utc_day=utc_day, period=period,
            slot=pub.hour if pub.hour in (11, 17, 23) else (17 if 14 <= pub.hour < 20 else 23 if pub.hour >= 20 else 11),
            dead_views=through(DEAD_K, 0), views=through(k, 0), engaged=through(k, 1),
            live=live.get(vid), has_series=bool(s),
        ))

    print(f"# Hacim denemesi ölçümü  (Analytics son gün {last_day}, ufuk gün+{k}, "
          f"düşük gün+{DEAD_K} < {fmt(LOW_VIEWS)}, ölü gün+{DEAD_K} < {DEAD_VIEWS})")
    for p in ("kiyas", "deney"):
        vs = [v for v in videos if v["period"] == p]
        print(f"{p}: {len(vs)} video, gün+{k} olgun {sum(v['views'] is not None for v in vs)}, "
              f"gün+{DEAD_K} olgun {sum(v['dead_views'] is not None for v in vs)}, "
              f"Analytics serisi olmayan {sum(not v['has_series'] for v in vs)}")
    if errors:
        print(f"sorgu hatası: {len(errors)} video  {errors[:3]}")

    tests: list[tuple[str, float, bool]] = []  # (name, p, enough data)

    # 1. Channel daily engaged views, with the new videos split out.
    print("\n## 1. Kanal günlük etkileşimli izlenme")
    ids = {v["id"] for v in videos}
    for p, span in (("kiyas", COMPARISON), ("deney", TEST)):
        days = [d for d in channel if in_period(d, span) and d <= last_day]
        if not days:
            print(f"{p}: veri yok")
            continue
        eng = [channel[d][1] for d in days]
        bg = [channel[d][0] - sum(series[i].get(d, (0, 0))[0] for i in ids) for d in days]
        new = [sum(series[i].get(d, (0, 0))[1] for i in ids) for d in days]
        print(f"{p}: {len(days)} gün | ort. etkileşimli/gün {fmt(mean(eng))} | medyan {fmt(median(eng))} | "
              f"dönem videolarının etkileşimli/gün {fmt(mean(new))} | arka plan izlenme/gün "
              f"(4 Eki-1 Kas videoları hariç) {fmt(mean(bg))}")
    print("Not: günlük toplam kanal ivmesini de taşır; arka plan izlenmesi aynı oranda değiştiyse fark ivmedir, "
          "hacim değil. Günler birbirine bağımlı olduğu için burada p yazılmaz.")

    # 2. New-video yield per publish day at a matched age (primary test 1).
    print(f"\n## 2. Günün videolarının gün+{k} etkileşimli izlenmesi (yayın günü başına toplam)")
    per_day: dict[str, dict[date, float]] = {"kiyas": {}, "deney": {}}
    for p in per_day:
        span = COMPARISON if p == "kiyas" else TEST
        d = span[0]
        while d <= span[1]:
            vs = [v for v in videos if v["utc_day"] == d]
            if vs and all(v["engaged"] is not None for v in vs):
                per_day[p][d] = sum(v["engaged"] for v in vs)
            d += timedelta(days=1)
        vals = list(per_day[p].values())
        print(f"{p}: {len(vals)} olgun gün | medyan {fmt(median(vals) if vals else None)} | "
              f"ortalama {fmt(mean(vals) if vals else None)}")
    a, b = list(per_day["deney"].values()), list(per_day["kiyas"].values())
    if a and b:
        p1 = mann_whitney_p(a, b)
        print(f"oran (ortalama) deney/kıyas = {mean(a) / mean(b):.2f}  MW p={p1:.3f}")
        tests.append(("günlük yeni video verimi", p1, min(len(a), len(b)) >= MIN_GROUP))

    # 3. Dilution: 17:00 and 23:00 videos (primary test 2, and the stop rule).
    print(f"\n## 3. 17:00 + 23:00 videoları, gün+{k} (sulandırma kontrolü)")
    groups = {p: [v for v in videos if v["period"] == p and v["slot"] in (17, 23) and v["engaged"] is not None]
              for p in ("kiyas", "deney")}
    for p, vs in groups.items():
        if vs:
            print(f"{p}: n={len(vs)} medyan izlenme {fmt(median(v['views'] for v in vs))} | "
                  f"medyan etkileşimli {fmt(median(v['engaged'] for v in vs))}")
    if groups["kiyas"] and groups["deney"]:
        ratio = median(v["engaged"] for v in groups["deney"]) / max(1, median(v["engaged"] for v in groups["kiyas"]))
        p2 = mann_whitney_p([v["engaged"] for v in groups["deney"]], [v["engaged"] for v in groups["kiyas"]])
        print(f"medyan oranı deney/kıyas = {ratio:.2f}  MW p={p2:.3f}")
        print(f"DURDURMA (%30+ düşüş): {'TETİKLENDİ' if ratio < 1 - STOP_DROP else 'hayır'}")
        tests.append(("17/23 sulandırma", p2, min(len(groups['kiyas']), len(groups['deney'])) >= MIN_GROUP))

    # 4. Low and dead videos (primary test 3, and two stop rules).
    print(f"\n## 4. Düşük video (gün+{DEAD_K} < {fmt(LOW_VIEWS)}) ve ölü video (< {DEAD_VIEWS})")
    counts, dead_test = {}, 0
    for p in ("kiyas", "deney"):
        vs = [v for v in videos if v["period"] == p and v["dead_views"] is not None]
        low = [v for v in vs if v["dead_views"] < LOW_VIEWS]
        dead = [v for v in vs if v["dead_views"] < DEAD_VIEWS]
        counts[p] = (len(low), len(vs) - len(low))
        if p == "deney":
            dead_test = len(dead)
        print(f"{p}: düşük {len(low)}/{len(vs)}" + (f" ({len(low) / len(vs) * 100:.1f}%)" if vs else "")
              + f" | ölü {len(dead)}"
              + "".join(f"\n   {v['utc_day']} {v['slot']:02d} {v['cre']} {v['dead_views']}" for v in low))
    low_n, total_n = counts["deney"][0], sum(counts["deney"])
    print(f"DURDURMA (deney videolarının %{STOP_LOW_SHARE * 100:.0f}'undan fazlası düşük; 42 videoda 13+): "
          + ("TETİKLENDİ" if total_n and low_n / total_n > STOP_LOW_SHARE else "hayır")
          + f"  [{low_n}/{total_n} olgun deney videosu]")
    print(f"DURDURMA (deneyde {STOP_DEAD}+ ölü, mutlak çöküş): {'TETİKLENDİ' if dead_test >= STOP_DEAD else 'hayır'}")
    if sum(counts["kiyas"]) and sum(counts["deney"]):
        p3 = fisher_p(*counts["deney"], *counts["kiyas"])
        print(f"düşük oranı Fisher p={p3:.3f}")
        tests.append(("düşük video oranı", p3, min(sum(counts['kiyas']), sum(counts['deney'])) >= MIN_GROUP))

    # 5. 11:00 alone, descriptive only.
    print(f"\n## 5. 11:00 videoları (yalnızca betimleyici, test yok)")
    morning = [v for v in videos if v["period"] == "deney" and v["slot"] == 11 and v["engaged"] is not None]
    if morning:
        e = sorted(v["engaged"] for v in morning)
        print(f"n={len(morning)} | medyan izlenme {fmt(median(v['views'] for v in morning))} | "
              f"medyan etkileşimli {fmt(median(e))} | en düşük {fmt(e[0])} en yüksek {fmt(e[-1])} | "
              f"hit {fmt(HIT_VIEWS)}+ {sum(v['views'] >= HIT_VIEWS for v in morning)} | "
              f"{fmt(HIT_SECONDARY)}+ {sum(v['views'] >= HIT_SECONDARY for v in morning)}")
    else:
        print("olgun 11:00 videosu yok")

    # 6. Hits (primary test 4).
    print(f"\n## 6. Hit (birincil {fmt(HIT_VIEWS)}+, ikincil {fmt(HIT_SECONDARY)}+ izlenme)")
    hit = {}
    for p in ("kiyas", "deney"):
        vs = [v for v in videos if v["period"] == p and v["views"] is not None]
        h = sum(v["views"] >= HIT_VIEWS for v in vs)
        hit[p] = (h, len(vs) - h)
        lv = [v for v in videos if v["period"] == p and v["live"] is not None]
        print(f"{p}: gün+{k} {fmt(HIT_VIEWS)}+ {h}/{len(vs)} | {fmt(HIT_SECONDARY)}+ "
              f"{sum(v['views'] >= HIT_SECONDARY for v in vs)}/{len(vs)} | canlı izlenmeyle (yaşa göre "
              f"eşleşmemiş) {fmt(HIT_VIEWS)}+ {sum(v['live'] >= HIT_VIEWS for v in lv)}/{len(lv)}")
    if sum(hit["kiyas"]) and sum(hit["deney"]):
        p4 = fisher_p(*hit["deney"], *hit["kiyas"])
        print(f"hit oranı Fisher p={p4:.3f}  (deney günde 3 video: oran aynı kalsa bile hit SAYISI artar)")
        tests.append(("hit oranı", p4, min(sum(hit['kiyas']), sum(hit['deney'])) >= MIN_GROUP))

    # 7. Within +-7 day percentile, on age-matched views. Descriptive.
    print(f"\n## 7. ±7 gün yüzdelik (gün+{k} izlenmesiyle, betimleyici)")
    mature = [v for v in videos if v["views"] is not None]
    for v in mature:
        peers = [w["views"] for w in mature if abs((w["utc_day"] - v["utc_day"]).days) <= 7 and w is not v]
        v["pct"] = sum(x < v["views"] for x in peers) / len(peers) * 100 if peers else None
    for label, sel in (("kıyas 17/23", lambda v: v["period"] == "kiyas"),
                       ("deney 17/23", lambda v: v["period"] == "deney" and v["slot"] != 11),
                       ("deney 11:00", lambda v: v["period"] == "deney" and v["slot"] == 11)):
        pcts = [v["pct"] for v in mature if sel(v) and v["pct"] is not None]
        if pcts:
            print(f"{label}: n={len(pcts)} medyan yüzdelik {median(pcts):.0f}")
    print("Pencere iki rejimin sınırında karışır; sonuç sayılmaz.")

    # Verdict.
    print("\n## Testler (Holm, 4 birincil test)")
    if tests:
        for (name, p, enough), adj in zip(tests, holm([t[1] for t in tests])):
            verdict = ("yeterli veri yok" if not enough
                       else "anlamlı" if adj < 0.05 else "fark gösterilemedi")
            print(f"{name:26} p={p:.3f} holm={adj:.3f}  {verdict}")

    print("\n## Video tablosu")
    print("donem\tgun\tsaat\tyaratik\tid\tgun+2_izl\tgun+k_izl\tgun+k_etk\tcanli")
    for v in sorted(videos, key=lambda v: v["pub"]):
        print("\t".join(str(x) for x in (v["period"], v["utc_day"], f"{v['slot']:02d}", v["cre"][:24], v["id"],
                                         fmt(v["dead_views"]), fmt(v["views"]), fmt(v["engaged"]), fmt(v["live"]))))


if __name__ == "__main__":
    main()
