"""The weekly and monthly analysis, written for a phone.

Prints the full report to the workflow log and sends a short version to
Telegram. The monthly run does everything the weekly one does and adds the
comparisons that need more videos behind them than a week supplies.

Usage: channel_report.py [weekly|monthly] [--no-telegram] [--no-save]
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from channel_ops import channel_analysis as ca  # noqa: E402
from channel_ops.channel_data import collect  # noqa: E402
from channel_ops.config_loader import find_project_root  # noqa: E402

BAR_WIDTH = 20


def _bar(percent: float) -> str:
    filled = max(0, min(BAR_WIDTH, round(percent / 100 * BAR_WIDTH)))
    return "█" * filled + "░" * (BAR_WIDTH - filled)


def _thousands(value) -> str:
    try:
        return f"{float(value):,.0f}".replace(",", ".")
    except (TypeError, ValueError):
        return str(value)


def _gate_lines(gate: dict) -> list[str]:
    head = (f"{gate['name']}: {_thousands(gate['value'])} / {_thousands(gate['goal'])}"
            f"  (%{gate['percent']})")
    detail = f"  {_bar(gate['percent'])}  "
    if gate["eta"]:
        detail += f"günde {_thousands(gate['rate_per_day'])} → {gate['eta']}"
    else:
        detail += gate["note"]
    return [head, detail]


def _delta(history: list[dict], key: str, current) -> str:
    """How this figure moved since the previous measurement."""
    previous = [s for s in history if s.get(key) is not None]
    if not previous or current is None:
        return ""
    last = previous[-1][key]
    try:
        change = float(current) - float(last)
    except (TypeError, ValueError):
        return ""
    if abs(change) < 0.05:
        return "  (değişmedi)"
    return f"  ({'+' if change > 0 else ''}{_thousands(round(change, 1))})"


def render(dump: dict, rows: list[dict], gates: dict, history: list[dict],
           studio: list[dict], monthly: bool) -> str:
    out: list[str] = []
    add = out.append
    mature = [row for row in rows if row["mature"]]

    add("=" * 58)
    add(f"{'AYLIK' if monthly else 'HAFTALIK'} KANAL ANALİZİ")
    add(f"{dump.get('collected_at', '')}  ·  {len(rows)} video, {len(mature)} olgun (48 saat+)")
    add("=" * 58)

    if dump.get("errors"):
        add("\n⚠️  EKSİK VERİ")
        for key, value in dump["errors"].items():
            add(f"  {key}: {value}")

    add("\n### 1. PARA KAZANMA EŞİKLERİ")
    add("")
    add("  ── ALT KADEME (hayran desteği; REKLAM GELİRİ YOK) ──")
    add(f"  Son 90 günde yükleme: {gates['uploads_90d']} / {ca.EARLY_UPLOAD_GOAL} "
        f"{'✓' if gates['uploads_90d'] >= ca.EARLY_UPLOAD_GOAL else '✗'}")
    add("")
    for key in ("early_subscribers", "early_watch_hours", "early_shorts_views"):
        for line in _gate_lines(gates[key]):
            add("  " + line)
        add("")
    add("  Gereken: 500 abone + 3 yükleme + (3.000 saat VEYA 3M Shorts)")
    add("")
    add("  ── ÜST KADEME (reklam geliri) ──")
    add("")
    for key in ("subscribers", "watch_hours", "shorts_views"):
        for line in _gate_lines(gates[key]):
            add("  " + line)
        add("")
    add("  Gereken: 1.000 abone + (4.000 saat VEYA 10M Shorts)")
    add(f"\n  Not: {gates['window_note']}")
    add("  Not: eşik rakamları YouTube'un yayınlanmış kurallarından; YouTube")
    add("       bunları zaman zaman değiştiriyor, Studio'daki rakamla karşılaştır.")

    add("\n### 2. BU HAFTA NE DEĞİŞTİ")
    snap = ca.snapshot(dump, rows, gates)
    if not history:
        add("  İlk ölçüm. Karşılaştırma bir sonraki rapordan itibaren.")
    else:
        for label, key in (("Abone", "subscribers"), ("Toplam izlenme", "views"),
                           ("Olgun video medyanı", "median_views"),
                           ("İzlenme yüzdesi medyanı", "median_retention"),
                           ("Ölü video", "dead_count"), ("Hit", "hit_count")):
            add(f"  {label}: {_thousands(snap[key])}{_delta(history, key, snap[key])}")

    add("\n### 3. NE İŞE YARIYOR")
    add("Her satır bir sıra testinden geçti. 'Fark yok' gerçek bir cevaptır.")
    for metric, title in (("views", "İzlenme"), ("retention", "İzlenme yüzdesi"),
                          ("subs_per_1k", "Bin izlenme başına abone")):
        add(f"\n  — {title} —")
        results = ca.cohorts(rows, metric)
        if not results:
            add("    (karşılaştırılacak grup yok)")
        for result in results:
            add(f"    {result['dimension']}: {result['a']} (n={result['n_a']}, "
                f"med={result['median_a']}) vs {result['b']} (n={result['n_b']}, "
                f"med={result['median_b']})")
            add(f"      → {result['verdict']}")
            if result.get("method"):
                add(f"      ! {result['method']}")

    band = ca.retention_band(rows)
    add(f"\n  — Hitlerin izlenme yüzdesi —")
    add(f"    hit (n={band['n_a']}, med={band['median_a']}) vs "
        f"diğer (n={band['n_b']}, med={band['median_b']})")
    add(f"      → {band['verdict']}")

    add("\n### 4. DÜŞÜK PERFORMANS")
    add("  izlenme  izl%   yaratık                    şablon    saat")
    for row in ca.weak_videos(rows):
        flag = "☠" if row["dead"] else " "
        add(f"  {flag}{_thousands(row['views']):>7}  {row['retention'] or '-':>5}  "
            f"{row['creature'][:24]:<26} {row['template']:<9} {row['hour']}")
    dead = [row for row in mature if row["dead"]]
    if mature:
        add(f"\n  Ölü video (48 saatte {ca.DEAD_VIEWS} altı): "
            f"{len(dead)}/{len(mature)} = %{len(dead) / len(mature) * 100:.1f}")

    add("\n### 5. HİTLERİN ORTAK NOKTALARI")
    profile = ca.hit_profile(rows)
    if profile.get("note"):
        add(f"  {profile['note']}")
    else:
        add(f"  {profile['hit_count']} hit, {profile['other_count']} diğer")
        add("  özellik                        hitte   diğerde   kat")
        for trait in profile["traits"]:
            if trait["lift"] is None or trait["hit_share"] == 0:
                continue
            add(f"  {trait['dimension']}={trait['value']:<20} "
                f"%{trait['hit_share']:>5}  %{trait['other_share']:>6}  "
                f"{trait['lift']:>5}x")
        add(f"\n  ⚠ {profile['warning']}")

    add("\n### 6. TRAFİK VE İZLEYİCİ")
    for section, title in (("traffic", "Trafik kaynağı"), ("devices", "Cihaz"),
                           ("subs_status", "Abone durumu"), ("countries", "Ülke")):
        block = (dump.get("analytics") or {}).get(section) or {}
        if not block.get("rows"):
            continue
        add(f"\n  {title}: " + " | ".join(block.get("columns") or []))
        for row in block["rows"][:8]:
            add("    " + "  ".join(_thousands(cell) if isinstance(cell, (int, float))
                                   else str(cell) for cell in row))

    add("\n### 7. GÖSTERİM VE TIKLANMA ORANI (CTR)")
    if studio:
        add("  Studio'dan elle girilen kayıtlar:")
        for entry in studio[-10:]:
            add(f"    {entry.get('date', '?')}  {entry.get('video_id', '?')}  "
                f"gösterim={_thousands(entry.get('impressions', '?'))}  "
                f"CTR=%{entry.get('ctr', '?')}")
    else:
        add("  Ölçülemiyor. Analytics API 'impressions' metriğini vermiyor")
        add("  (HTTP 400 Unknown identifier — denendi, varsayım değil). Bu iki")
        add("  metrik yalnızca Studio'da var. Kapak ve başlık kararları CTR'a")
        add("  göre otomatikleştirilemez. Studio'dan okuyup")
        add("  data/studio_manual.json'a eklersen bu bölüm dolar:")
        add('    [{"date":"2026-09-25","video_id":"...","impressions":120000,"ctr":4.1}]')

    if monthly:
        add("\n### 8. AYLIK: UZUN VİDEOLAR")
        long_form = dump.get("long_form") or []
        if not long_form:
            add("  Uzun video yok.")
        for record in long_form:
            vid = record.get("youtube_video_id", "")
            live = ca.per_video(dump).get(vid, {})
            data = (dump.get("data_api") or {}).get(vid, {})
            hours = float(live.get("estimatedMinutesWatched") or 0) / 60
            add(f"  {vid}  {record.get('published_at', '')[:10]}  "
                f"{record.get('minutes', '?')} dk, {record.get('clip_count', '?')} klip")
            add(f"    izlenme={_thousands(data.get('views', 0))}  "
                f"izl%={live.get('averageViewPercentage', '-')}  "
                f"ort={live.get('averageViewDuration', '-')} sn  "
                f"saat={hours:.1f} (4.000'in %{hours / 40:.3f}'i)")
            block = (dump.get("analytics") or {}).get(f"traffic_{vid}") or {}
            for row in block.get("rows") or []:
                add(f"      {row[0]}: {_thousands(row[1])} izlenme")

    add("\n" + "=" * 58)
    return "\n".join(out)


def telegram_summary(gates: dict, snap: dict, history: list[dict]) -> str:
    lines = ["📊 <b>Kanal analizi</b>", "", "<b>Alt kademe</b> (reklam yok):"]
    for key in ("early_subscribers", "early_watch_hours", "early_shorts_views"):
        gate = gates[key]
        eta = f" → {gate['eta']}" if gate["eta"] else f" — {gate['note']}"
        lines.append(f"{gate['name']}: <b>{_thousands(gate['value'])}</b>"
                     f"/{_thousands(gate['goal'])} (%{gate['percent']}){eta}")
    lines.append("")
    lines.append("<b>Üst kademe</b> (reklam geliri):")
    for key in ("subscribers", "watch_hours", "shorts_views"):
        gate = gates[key]
        eta = f" → {gate['eta']}" if gate["eta"] else f" — {gate['note']}"
        lines.append(f"{gate['name']}: <b>{_thousands(gate['value'])}</b>"
                     f"/{_thousands(gate['goal'])} (%{gate['percent']}){eta}")
    lines.append("")
    lines.append(f"Abone: {_thousands(snap['subscribers'])}"
                 f"{_delta(history, 'subscribers', snap['subscribers'])}")
    lines.append(f"Olgun medyan: {_thousands(snap['median_views'])}"
                 f"{_delta(history, 'median_views', snap['median_views'])}")
    lines.append(f"Ölü video: {snap['dead_count']}/{snap['mature_count']}"
                 f"{_delta(history, 'dead_count', snap['dead_count'])}")
    lines.append("")
    lines.append("Tam rapor iş kaydında.")
    return "\n".join(lines)


def main() -> None:
    argv = sys.argv[1:]
    monthly = "monthly" in argv
    root = find_project_root()

    dump = collect(root)
    rows = ca.videos(dump)
    history = ca.load_history(root)
    gates = ca.thresholds(dump, history)
    studio = ca.load_studio(root)

    print(render(dump, rows, gates, history, studio, monthly))

    snap = ca.snapshot(dump, rows, gates)
    if "--no-telegram" not in argv:
        try:
            from channel_ops.notifications import send_message
            send_message(telegram_summary(gates, snap, history))
        except Exception as exc:  # a failed notification must not lose the report
            print(f"\n[telegram gönderilemedi: {exc}]")
    # Saved last: a snapshot written before the report is rendered would be
    # compared against itself and every delta would read as zero.
    if "--no-save" not in argv:
        path = ca.save_snapshot(snap, root)
        print(f"\n[kayıt: {path}]")


if __name__ == "__main__":
    main()
