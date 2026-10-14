"""What the Partner Programme reviewer will find, before they look.

Run once before applying. It does not judge the application -- a human at
YouTube does that, against policies no API exposes. What it does is check the
things that ARE machine-readable and that are known to sink applications:
a video stuck in processing, a rejected upload, a copyright claim, a channel
whose features are restricted, an undeclared AI video. Each of those is
fixable in an afternoon and expensive to discover in a rejection email thirty
days later.

Everything it cannot see is listed at the end rather than left silent, so the
report is never mistaken for a clean bill of health.

Usage: ypp_preflight.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from channel_ops import video_metadata  # noqa: E402
from channel_ops.channel_data import _data_stats, load_records  # noqa: E402
from channel_ops.config_loader import find_project_root  # noqa: E402
from channel_ops.youtube_auth import get_access_token  # noqa: E402

CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"


def channel_status() -> dict:
    """The channel's own status block.

    ``longUploadsStatus`` is the one field here that answers a question asked
    three times in chat and never settled: whether the advanced features lost
    in July are back. "allowed" or "eligible" means the restriction lifted.
    """
    token = get_access_token()
    params = urlencode({"part": "status,contentDetails,snippet,statistics",
                        "mine": "true"})
    request = Request(f"{CHANNELS_URL}?{params}",
                      headers={"Authorization": f"Bearer {token}"})
    with urlopen(request, timeout=20) as response:
        payload = json.load(response)
    items = payload.get("items") or []
    return items[0] if items else {}


def main() -> int:
    root = find_project_root()
    published, long_form = load_records(root)
    video_ids = [r["youtube_video_id"] for r in published + long_form
                 if r.get("youtube_video_id")]

    print("=" * 58)
    print("YPP BAŞVURU ÖN KONTROLÜ")
    print("=" * 58)

    problems: list[str] = []
    notes: list[str] = []

    # ---- channel -----------------------------------------------------
    try:
        channel = channel_status()
    except Exception as exc:  # noqa: BLE001
        channel = {}
        problems.append(f"Kanal durumu okunamadı: {exc}")

    if channel:
        status = channel.get("status", {})
        snippet = channel.get("snippet", {})
        stats = channel.get("statistics", {})
        print(f"\nKanal      : {snippet.get('title', '?')}")
        print(f"Ülke       : {snippet.get('country', '(belirtilmemiş)')}")
        print(f"Abone      : {int(stats.get('subscriberCount', 0)):,}".replace(",", "."))
        print(f"Video      : {stats.get('videoCount', '?')}")
        print(f"Gizlilik   : {status.get('privacyStatus', '?')}")
        print(f"Çocuklara  : madeForKids={status.get('madeForKids')}")

        uploads = status.get("longUploadsStatus", "")
        print(f"İleri özellikler (longUploadsStatus): {uploads or '(yok)'}")
        if uploads in ("disallowed", "longUploadsUnspecified", ""):
            problems.append(
                f"İleri seviye özellikler kısıtlı görünüyor (longUploadsStatus="
                f"{uploads or 'boş'}). 31 Temmuz'daki 'özellik kaybı' uyarısı "
                "hâlâ geçerli olabilir. Studio → Ayarlar → Kanal → Özellik "
                "uygunluğu'ndan telefon/kimlik doğrulaması yap."
            )
        if not snippet.get("country"):
            notes.append("Kanalın ülkesi ayarlı değil. Başvuruyu engellemez "
                         "ama ödeme ve vergi adımlarında sorulur.")

    # ---- videos ------------------------------------------------------
    try:
        stats_by_id = _data_stats(video_ids)
    except Exception as exc:  # noqa: BLE001
        stats_by_id = {}
        problems.append(f"Video durumları okunamadı: {exc}")

    if stats_by_id:
        privacy = Counter(v["privacy"] for v in stats_by_id.values())
        upload = Counter(v["upload"] for v in stats_by_id.values())
        processing = Counter(v["processing"] for v in stats_by_id.values())

        print(f"\nVideo durumu ({len(stats_by_id)} video API'den döndü)")
        print(f"  gizlilik  : {dict(privacy)}")
        print(f"  yükleme   : {dict(upload)}")
        print(f"  işleme    : {dict(processing)}")

        rejected = {vid: v for vid, v in stats_by_id.items() if v["rejection"]}
        failed = {vid: v for vid, v in stats_by_id.items() if v["failure"]}
        unprocessed = {vid: v for vid, v in stats_by_id.items()
                       if v["upload"] and v["upload"] != "processed"}
        not_public = {vid: v for vid, v in stats_by_id.items()
                      if v["privacy"] and v["privacy"] != "public"}

        for label, group, why in (
            ("REDDEDİLMİŞ", rejected,
             "YouTube bu videoları reddetmiş. İncelemede ilk görülecek şey bu."),
            ("YÜKLENEMEMİŞ", failed, "Yükleme hatası kalmış."),
            ("İŞLENMEMİŞ", unprocessed,
             "Yayında görünüp izlenemeyen video, incelemede kötü görünür."),
        ):
            if group:
                problems.append(f"{len(group)} {label} video. {why}")
                for vid, v in list(group.items())[:5]:
                    reason = v["rejection"] or v["failure"] or v["upload"]
                    print(f"  ⚠ {vid}: {reason} — {v['title'][:45]}")

        if not_public:
            notes.append(f"{len(not_public)} video herkese açık değil "
                         f"({', '.join(sorted({v['privacy'] for v in not_public.values()}))}). "
                         "Sorun değil, ama son 90 gün yükleme şartını bunlar saymaz.")

        kids = [v for v in stats_by_id.values() if v["kids"] is True]
        if kids:
            notes.append(f"{len(kids)} video 'çocuklara yönelik' işaretli. "
                         "Bu videolarda kişiselleştirilmiş reklam çıkmaz, geliri düşürür.")

    # ---- AI declaration ---------------------------------------------
    declared = video_metadata.load_declared(root)
    live = set(stats_by_id) if stats_by_id else set(video_ids)
    undeclared = live - declared
    print(f"\nYapay zeka beyanı: {len(declared & live)}/{len(live)} video")
    if undeclared:
        problems.append(
            f"{len(undeclared)} videoda yapay zeka beyanı kaydı yok. "
            "Beyan edilmemiş sentetik içerik, 'özgün olmayan içerik' "
            "incelemesinde en pahalı bulgudur."
        )

    # ---- verdict -----------------------------------------------------
    print("\n" + "=" * 58)
    if problems:
        print(f"⚠️  {len(problems)} SORUN — başvurudan önce bakılmalı\n")
        for item in problems:
            print(f"  • {item}")
    else:
        print("✅ Makine ile görülebilen bir engel yok.")
    if notes:
        print("\nNotlar (engel değil):")
        for item in notes:
            print(f"  • {item}")

    # Said out loud, because a short clean report is easy to read as
    # "everything is fine" when most of the decision is made elsewhere.
    print("""
BU KONTROLÜN GÖREMEDİKLERİ — API bunları vermiyor:
  • Topluluk Kuralları ihtarı / telif hak talebi geçmişi
  • 'Özgün olmayan içerik' değerlendirmesi (incelemeyi insan yapıyor)
  • AdSense hesabının durumu ve vergi bilgisi
  • Kanal geçmişi / ileri seviye özellik doğrulaması ayrıntısı
  • Studio'daki para kazanma politikası uyarıları
Bunlar Studio'dan elle kontrol edilmeli.""")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
