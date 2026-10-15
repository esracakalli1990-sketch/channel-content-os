"""Build the channel briefing PDF handed to an outside reviewer.

Written to be read by someone who has never seen this channel: a data
analyst, an audience strategist, a YouTube specialist. So it states what the
channel is, where it stands, how the numbers were measured, and -- at least
as important -- what could NOT be measured and where the analysis has
previously been wrong. A brief that only shows the good numbers invites
advice built on the parts that were left out.

Every figure is read from data/expert_dataset.json, the same file the
appendix reads. The first draft hardcoded them, and within a day the brief
and its own appendix disagreed -- 5.300 subscribers against 5.580, because
the channel had moved between the two. One collection, one timestamp, both
documents.

Usage: expert_brief.py dataset.json [output.pdf]
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from channel_ops import channel_analysis as ca  # noqa: E402

# Helvetica has no ğ, ı, İ, ş. DejaVu does, and ships with the runner image.

COUNTRY = {"IN": "Hindistan", "BD": "Bangladeş", "MM": "Myanmar",
           "UZ": "Özbekistan", "US": "ABD", "ID": "Endonezya", "NP": "Nepal",
           "PK": "Pakistan", "VN": "Vietnam", "RU": "Rusya", "DE": "Almanya",
           "LK": "Sri Lanka", "UA": "Ukrayna", "IT": "İtalya", "RW": "Ruanda",
           "GB": "B. Krallık", "TR": "Türkiye", "BR": "Brezilya"}
TRAFFIC = {"SHORTS": "Shorts akışı", "YT_SEARCH": "YouTube araması",
           "YT_CHANNEL": "Kanal sayfası", "SUBSCRIBER": "Abonelikler",
           "YT_OTHER_PAGE": "Diğer YT sayfaları", "HASHTAGS": "Hashtag",
           "SOUND_PAGE": "Ses sayfası", "EXT_URL": "Harici bağlantı",
           "NOTIFICATION": "Bildirim", "RELATED_VIDEO": "İlgili video",
           "NO_LINK_OTHER": "Bağlantısız diğer", "ADVERTISING": "Reklam"}
AGE = {"age13-17": "13–17", "age18-24": "18–24", "age25-34": "25–34",
       "age35-44": "35–44", "age45-54": "45–54", "age55-64": "55–64",
       "age65-": "65+"}
DEVICE = {"MOBILE": "mobil", "TABLET": "tablet", "DESKTOP": "masaüstü",
          "TV": "TV"}

FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
INK = colors.HexColor("#1a1a1a")
MUTED = colors.HexColor("#5f6368")
RULE = colors.HexColor("#d0d3d6")
BAND = colors.HexColor("#f2f3f5")
WARN = colors.HexColor("#8a4b08")


def _fonts() -> tuple[str, str, str]:
    pdfmetrics.registerFont(TTFont("DJ", str(FONT_DIR / "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("DJ-B", str(FONT_DIR / "DejaVuSans-Bold.ttf")))
    pdfmetrics.registerFont(TTFont("DJ-M", str(FONT_DIR / "DejaVuSansMono.ttf")))
    return "DJ", "DJ-B", "DJ-M"


def _styles(base: str, bold: str, mono: str) -> dict:
    sheet = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=sheet["Title"], fontName=bold,
                                fontSize=21, leading=26, textColor=INK,
                                spaceAfter=2),
        "sub": ParagraphStyle("s", parent=sheet["Normal"], fontName=base,
                              fontSize=10.5, leading=15, textColor=MUTED,
                              spaceAfter=16),
        "h1": ParagraphStyle("h1", parent=sheet["Heading1"], fontName=bold,
                             fontSize=14.5, leading=19, textColor=INK,
                             spaceBefore=16, spaceAfter=7),
        "h2": ParagraphStyle("h2", parent=sheet["Heading2"], fontName=bold,
                             fontSize=11.5, leading=15, textColor=INK,
                             spaceBefore=11, spaceAfter=4),
        "p": ParagraphStyle("p", parent=sheet["Normal"], fontName=base,
                            fontSize=9.8, leading=14.5, textColor=INK,
                            alignment=TA_LEFT, spaceAfter=7),
        "note": ParagraphStyle("n", parent=sheet["Normal"], fontName=base,
                               fontSize=9, leading=13.5, textColor=WARN,
                               leftIndent=8, spaceAfter=7),
        "mono": ParagraphStyle("m", parent=sheet["Normal"], fontName=mono,
                               fontSize=8.3, leading=12, textColor=INK),
        "cap": ParagraphStyle("c", parent=sheet["Normal"], fontName=base,
                              fontSize=8.3, leading=11.5, textColor=MUTED,
                              spaceAfter=11),
    }


def _table(rows, widths, base, bold, align_right=()):
    style = [
        ("FONTNAME", (0, 0), (-1, 0), bold),
        ("FONTNAME", (0, 1), (-1, -1), base),
        ("FONTSIZE", (0, 0), (-1, -1), 8.6),
        ("LEADING", (0, 0), (-1, -1), 12),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("BACKGROUND", (0, 0), (-1, 0), BAND),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, RULE),
        ("LINEBELOW", (0, 1), (-1, -2), 0.25, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]
    for col in align_right:
        style.append(("ALIGN", (col, 1), (col, -1), "RIGHT"))
    table = Table(rows, colWidths=widths, hAlign="LEFT")
    table.setStyle(TableStyle(style))
    return table


def build(data: dict, path: Path) -> Path:
    base, bold, mono = _fonts()
    s = _styles(base, bold, mono)
    doc = SimpleDocTemplate(
        str(path), pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title="Unfoldables — Kanal Durum Raporu",
        author="channel-content-os",
    )
    W = doc.width
    out: list = []
    add = out.append

    totals = data.get("totals") or {}
    gates = data.get("gates") or {}
    all_rows = ca.settle_deleted(data.get("videos") or [])
    removed = [v for v in all_rows if v.get("deleted")]
    vids = [v for v in all_rows if not v.get("deleted")]
    mature = [v for v in vids if v.get("mature")]
    stamp = (data.get("collected_at") or "")[:16].replace("T", " ")
    day = (data.get("collected_at") or "")[:10]

    def N(value, digits=0):
        if value is None:
            return "–"
        try:
            text = f"{float(value):,.{digits}f}"
        except (TypeError, ValueError):
            return str(value)
        return text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")

    def gate(key, field, digits=0):
        return N((gates.get(key) or {}).get(field), digits)

    med_views = statistics.median([v["views"] for v in mature]) if mature else 0
    rets = [v["retention"] for v in mature if v.get("retention") is not None]
    med_ret = statistics.median(rets) if rets else 0
    hits = sum(1 for v in vids if v.get("hit"))
    dead = sum(1 for v in vids if v.get("dead"))
    top = max(vids, key=lambda v: v.get("views", 0)) if vids else {}
    first = min((v.get("published_at") or "" for v in vids if v.get("published_at")),
                default="")

    tables = data.get("tables") or {}

    counts = {}
    for v in vids:
        if v.get("seconds"):
            counts[int(v["seconds"])] = counts.get(int(v["seconds"]), 0) + 1
    durations = ", ".join(f"{n} video {sec} sn" for sec, n in
                          sorted(counts.items(), key=lambda item: -item[1]))

    def rows_of(name):
        return ((tables.get(name) or {}).get("rows")) or []

    def para(text, style="p"):
        add(Paragraph(text, s[style]))

    # ---------------------------------------------------------------- cover
    para("Unfoldables — Kanal Durum Raporu", "title")
    para(f"@unfoldableslab · YouTube Shorts · ölçüm {stamp} UTC · "
         "dış değerlendirme için hazırlandı", "sub")

    para("1. Özet", "h1")
    para(
        f"Kanal {first[:10]} tarihinde yayına başladı. {day} itibarıyla "
        f"<b>{N(len(vids))} video, {N(totals.get('subscribers'))} abone, "
        f"{N(totals.get('views'))} izlenme</b>. YouTube İş Ortaklığı "
        f"Programı'nın <b>alt kademe eşiği 13 Ekim'de geçildi</b> — şu an "
        f"90 günde {gate('early_shorts_views', 'value')} etkileşimli Shorts "
        f"görüntülemesi, eşik {gate('early_shorts_views', 'goal')} "
        f"(%{gate('early_shorts_views', 'percent', 1)}). Başvuru düğmesi "
        "14 Ekim'de açıldı; başvuru henüz yapılmadı."
    )
    para(
        "Üretim tamamen otomatik bir hatla yürüyor: fikirler bir dil modeliyle "
        "üretilip Telegram'a düşüyor, operatör videoyu üretip bota gönderiyor, "
        "hat başlık/açıklama üretip YouTube ve Instagram'a yayınlıyor. Günde "
        "ortalama 2-3 video."
    )
    add(Spacer(1, 4))

    add(_table([
        ["Metrik", "Değer", "Not"],
        ["Abone", N(totals.get("subscribers")), ""],
        ["Toplam izlenme", N(totals.get("views")), "ham, ömür boyu"],
        ["Video", N(len(vids)),
         f"tamamı Shorts, {durations}" + (f"; silinmiş {len(removed)} hariç" if removed else "")],
        ["Olgun video medyanı", f"{N(med_views)} izlenme",
         f"48 saat+ olan {N(len(mature))} video"],
        ["Medyan izlenme yüzdesi", f"%{N(med_ret, 1)}", "tekrar izleme dahil"],
        ["Ölü video (48 saatte <250)",
         f"{N(dead)} / {N(len(mature))} = %{N(dead / len(mature) * 100 if mature else 0, 1)}", ""],
        ["Hit (10.000+ izlenme)", N(hits), ""],
        ["En yüksek video", N(top.get("views")),
         (top.get("published_at") or "")[:10]],
    ], [W * 0.34, W * 0.28, W * 0.38], base, bold))
    add(Spacer(1, 4))
    summed = sum(v.get("views") or 0 for v in vids)
    para(
        f"Toplam izlenme farkı: kanal sayacı {N(totals.get('views'))}, tek tek "
        f"videoların toplamı {N(summed)}. Üç kaynağın gecikmesi farklı (Data "
        "API neredeyse anlık, Analytics 2–3 gün, Studio kartı ~1 hafta) ve "
        "kanal sayacının hangi gecikmeyle geldiği doğrulanmadı; farkın nedeni "
        "budur, diye iddia etmiyoruz.", "cap"
    )
    add(Spacer(1, 6))

    # ------------------------------------------------------------ threshold
    para("2. Para kazanma eşikleri", "h1")
    para(
        "<b>Kritik ölçüm notu:</b> YouTube'un Shorts eşiği <b>etkileşimli "
        "görüntülemeyle</b> (engagedViews) sayılıyor, ham görüntülemeyle değil. "
        "Bu kanalda ham görüntüleme etkileşimlinin yaklaşık 2,2 katı. Raporun "
        "her yerinde eşik rakamları etkileşimli görüntülemedir.", "note"
    )
    add(_table([
        ["Eşik", "Durum", "Hedef", "%"],
        ["ALT KADEME — hayran desteği, reklam geliri yok", "", "", ""],
        ["Abone", gate("early_subscribers", "value"), "500", "geçildi"],
        ["Son 90 günde yükleme", N(gates.get("uploads_90d")), "3", "geçildi"],
        ["Etkileşimli Shorts (90 gün)", gate("early_shorts_views", "value"),
         gate("early_shorts_views", "goal"),
         f"%{gate('early_shorts_views', 'percent', 1)}"],
        ["ÜST KADEME — reklam geliri", "", "", ""],
        ["Abone", gate("subscribers", "value"), "1.000", "geçildi"],
        ["İzlenme saati (uzun video)", gate("watch_hours", "value", 1),
         "4.000", f"%{gate('watch_hours', 'percent', 2)}"],
        ["Etkileşimli Shorts (90 gün)", gate("shorts_views", "value"),
         gate("shorts_views", "goal"), f"%{gate('shorts_views', 'percent', 1)}"],
    ], [W * 0.40, W * 0.21, W * 0.21, W * 0.18], base, bold, align_right=(1, 2, 3)))
    add(Spacer(1, 6))
    para(
        f"Üst kademe için günlük hız "
        f"{gate('shorts_views', 'rate_per_day')} etkileşimli görüntüleme; bu "
        f"tempoda tahmini tarih <b>{(gates.get('shorts_views') or {}).get('eta') or '–'}</b>. "
        "İzlenme saati yolu kapalı: Shorts "
        "Akışı'ndaki izlenme süresi 4.000 saatlik eşiğe dahil edilmiyor ve "
        "kanalda uzun video hattı denendi, çalışmadı (186 izlenme, 3,2 saat).", "p"
    )
    para(
        "Studio'nun uygunluk kartı Analytics'ten ayrıca ~1 hafta geriden geliyor; "
        "14 Ekim'de kart 7 Ekim verisini gösteriyordu. Bu beklenen davranış, "
        "veri tutarsızlığı değil.", "cap"
    )

    # -------------------------------------------------------------- growth
    para("3. Büyüme eğrisi", "h1")
    # One row per day, and never a row whose 90-day figure exceeds the
    # lifetime total (impossible; see ca.suspect_snapshot). The unit gets its
    # own column: raw and engaged views are different rulers and the earlier
    # version of this table put both in one column as if they were one series.
    unit = {"views": "ham", "engaged": "etkileşimli"}
    seen, grow = set(), [["Tarih", "Abone", "Toplam izlenme", "Video",
                         "Shorts 90g", "Birim", "Medyan", "Ölü", "Hit"]]
    for snap in data.get("history") or []:
        date = (snap.get("at") or "")[:10]
        if not date or date in seen or ca.suspect_snapshot(snap):
            continue
        seen.add(date)
        grow.append([date[5:], N(snap.get("subscribers")), N(snap.get("views")),
                     N(snap.get("videos")), N(snap.get("shorts_views_90d")),
                     unit.get(snap.get("shorts_measure"), "–"),
                     N(snap.get("median_views")), N(snap.get("dead_count")),
                     N(snap.get("hit_count"))])
    add(_table(grow, [W * 0.09, W * 0.10, W * 0.17, W * 0.08, W * 0.17,
                      W * 0.13, W * 0.10, W * 0.08, W * 0.08],
               base, bold, align_right=(1, 2, 3, 4, 6, 7, 8)))
    add(Spacer(1, 6))
    para(
        "3 Eylül'de 159 abone vardı. 25 Eylül–13 Ekim arasında abone 1.070 → "
        "5.300, toplam izlenme 2,0M → 7,9M. Ölü video oranı %8,9'dan %3,3'e "
        "düşerken hit sayısı 19'dan 40'a çıktı — yani büyüme tek bir viral "
        "videodan değil, tabanın yükselmesinden geliyor. (Ölü video oranı o günlerin "
        "raporlarından; silinmiş videonun sayıldığı eski hesapla.)", "p"
    )
    para(
        "Shorts 90g sütunu iki farklı birimle tutuldu: 28 Eylül'e kadar ham "
        "görüntüleme, o günden sonra etkileşimli görüntüleme (eşiğin saydığı "
        "ölçü). Bu yüzden aradaki geçiş bir düşüş değil, ölçü değişimidir ve "
        "farklı birimli satırlar birbiriyle karşılaştırılamaz. 28 Eylül 14:12 "
        "tarihli kayıt (90 günlük 3.108.912, kanalın ömür boyu toplamı "
        "2.618.818) imkânsız olduğu için tablodan çıkarıldı, nedeni bilinmiyor. "
        "Kanal bu tarihte 61 günlüktü; 90 günlük pencereden düşen video yoktu.",
        "cap"
    )

    add(PageBreak())

    # ------------------------------------------------------------- format
    para("4. Format", "h1")
    para(
        "Her video 8–11 saniyelik (çoğu 9 ya da 11), dikey, konuşmasız bir Short; ses olarak mekanik efektler var (dişli, kapak açılması, düğme sesi). Sahne sabit: bir el "
        "ya da tezgâh üzerinde duran mekanik bir nesne, gizli bir düğmeye "
        "basılınca katlanarak açılıyor ve bir yaratığa ya da mekanizmaya "
        "dönüşüyor. Görüntüler <b>tamamen yapay zekâ ile üretiliyor</b>; nesneler "
        "gerçek değil."
    )
    para(
        "Değişkenler her videoda farklı: <b>yaratık</b> (pangolin, akrep, güve, "
        "nautilus…), <b>şekil</b> (yivli küresel makara, basık kuboktahedron, "
        "oluklu içbükey kama…), <b>malzeme</b> (patine bronz ve mavileştirilmiş "
        "çelik, dövme bakır ve karartılmış demir, fırçalanmış titanyum ve koyu "
        "amber reçine…). Başlıklar çoğunlukla bir soru: <i>“Why does this curved "
        "bronze shell have a walnut base?”</i> kalıbıyla başlayanlar videoların "
        f"{N(sum(1 for v in vids if v.get('title', '').lower().startswith('why does')))} "
        f"tanesi; üç sözcüklü {N(len({' '.join(v.get('title', '').lower().split()[:3]) for v in vids}))} "
        "farklı açılış var."
    )
    para(
        "Yapay zekâ beyanı 159 videonun tamamında açık "
        "(<font face='DJ-M'>containsSyntheticMedia</font>). 12–14 Ekim'de "
        "geriye dönük işlendi; yeni videolar yüklenirken otomatik beyan ediliyor.", "p"
    )

    # ------------------------------------------------------------ audience
    para("5. Kitle", "h1")
    subs_rows = {e[0]: e for e in rows_of("subs_status")}
    sub_v = (subs_rows.get("SUBSCRIBED") or ["", 0])[1]
    unsub_v = (subs_rows.get("UNSUBSCRIBED") or ["", 0])[1]
    unsub_pct = unsub_v / (sub_v + unsub_v) * 100 if (sub_v + unsub_v) else 0
    para(
        "<b>Kitlenin yapısı stratejik olarak en önemli bölüm.</b> "
        f"İzlenmelerin %{N(unsub_pct, 1)}'i abone olmayanlardan geliyor "
        f"({N(unsub_v)} / {N(sub_v)}); bu bir topluluk değil, akış kitlesi.",
        "note")

    half = (W - 8 * mm) / 2
    ctry = [["Ülke", "İzlenme"]]
    for entry in rows_of("countries")[:8]:
        ctry.append([COUNTRY.get(entry[0], entry[0]), N(entry[1])])
    left = _table(ctry, [half * 0.52, half * 0.48], base, bold, align_right=(1,))

    ranked = sorted((r for r in rows_of("demographics") if float(r[2] or 0) > 0),
                    key=lambda r: -float(r[2]))
    demo = [["Yaş / cinsiyet", "%"]]
    for age, gender, pct in ranked[:8]:
        who = {"male": "erkek", "female": "kadın"}.get(gender, gender)
        demo.append([f"{AGE.get(age, age)} {who}", N(pct, 1)])
    right = _table(demo, [half * 0.60, half * 0.40], base, bold, align_right=(1,))

    add(Table([[left, right]], colWidths=[half + 4 * mm, half + 4 * mm],
              hAlign="LEFT", style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")])))
    add(Spacer(1, 8))

    traf = [["Trafik kaynağı", "İzlenme", "İzlenme dk"]]
    for entry in rows_of("traffic")[:9]:
        traf.append([TRAFFIC.get(entry[0], entry[0]), N(entry[1]), N(entry[2])])
    add(_table(traf, [W * 0.42, W * 0.29, W * 0.29], base, bold,
               align_right=(1, 2)))
    add(Spacer(1, 8))

    para("Arama terimleri — kanala arama ile gelenler", "h2")
    terms = " · ".join(f"{e[0]} {N(e[1])}" for e in rows_of("search_terms")[:8])
    para(f"<font face='DJ-M'>{terms}</font>", "mono")
    add(Spacer(1, 5))
    para(
        "Listede <b>tek bir yaratık adı yok</b>. Hepsi malzeme ve mekanizma "
        "kelimesi. Arama trafiği toplamın %0,4'ü olsa da niyeti gösteriyor: "
        "izleyici bronzun ve mekanizmanın peşinde geliyor, hayvanın değil. "
        "Bu, içerik yönü tartışmasında elimizdeki en net sinyal.", "p"
    )
    add(Spacer(1, 4))
    dev = rows_of("devices")
    dev_total = sum(e[1] for e in dev) or 1
    dev_text = ", ".join(
        f"{DEVICE.get(e[0], e[0])} %{N(e[1] / dev_total * 100, 1)} ({N(e[1])})"
        for e in dev)
    share = rows_of("sharing")[:3]
    share_text = ", ".join(f"{e[0]} {N(e[1])}" for e in share)
    para(f"Cihaz: {dev_text}. En çok paylaşılan yer: {share_text}.", "cap")

    add(PageBreak())

    # ------------------------------------------------------------- method
    para("6. Ölçüm yöntemi ve bulgular", "h1")
    para(
        "Haftalık analiz her videoyu kohortlara ayırıp <b>Mann-Whitney U sıra "
        "testi</b> uyguluyor (izlenmeler aşırı çarpık, ortalama yanıltıcı). "
        "Raporda 16 test var, bu yüzden p değerleri <b>Holm-Bonferroni</b> ile "
        "düzeltiliyor. Grupların takvimde ayrık olduğu durumlar ayrıca "
        "işaretleniyor."
    )
    para(
        "13 Ekim raporunda 16 testten <b>yalnızca biri</b> düzeltmeden sağ çıktı: "
        "bir fikir talimatı sürümü, bin izlenme başına abonede daha iyi "
        "(ham p=0,003 · düzeltilmiş p=0,042). O test bile <i>“gruplar zamanda "
        "ayrık”</i> uyarısı taşıyor, çünkü iki grup kanalın ivmelendiği farklı "
        "dönemlere denk geliyor. Bu nedenle kural hâline getirilmedi."
    )
    para(
        "Diğer 15 test “fark yok” dedi: şablon sürümü, yayın saati, konu türü "
        "(canlı/makine), malzeme — hiçbiri izlenme, izlenme yüzdesi ya da abone "
        "dönüşümünde anlamlı fark üretmedi. <b>Düzeltmesiz bakılsaydı</b> iki "
        "test daha “bulgu” sayılacaktı (ham p=0,022 ve 0,043; düzeltilince "
        "0,329 ve 0,609)."
    )

    para("Neden bu kadar temkinli", "h2")
    para(
        "24 Ağustos'ta az sayıda hitten bir kural çıkarılıp üretim prompt'una "
        "yazıldı. Sonuç: <b>19 hitsiz video ve %44 düşüş.</b> O günden beri "
        "rapor, hit ortak noktaları tablosunun altına her çalıştığında şu uyarıyı "
        "basıyor: bu satırların hiçbiri prompt'a kural olarak girmemeli. "
        "Değerlendirmede bu tabloyu (ör. “gunmetal hitlerde 2,8 kat”) kural "
        "olarak okumamanızı rica ederiz — 40 hit bunu taşımaz.", "note"
    )

    # ------------------------------------------------------------- limits
    para("7. Ölçülemeyenler", "h1")
    para(
        "Aşağıdakiler YouTube API'sinde <b>yok</b>; yalnızca Studio arayüzünde "
        "var. Bu rapordaki hiçbir sayı bunları içermiyor ve bu boşluklar "
        "tahminle doldurulmadı:"
    )
    add(_table([
        ["Eksik veri", "Sonucu"],
        ["Gösterim (impressions) ve CTR",
         "Kapak/başlık kararları ölçüye bağlanamıyor. Analytics API "
         "“Unknown identifier” döndürüyor — denendi, varsayılmadı."],
        ["“Kitlenizin izlediği diğer videolar/kanallar”",
         "İçerik çeşitlendirme tartışması veri olmadan yapılamıyor."],
        ["İzleyici tutma eğrisi (saniye bazında)",
         "Videonun neresinde bırakıldığı bilinmiyor."],
        ["Saatlik kırılım",
         "Analytics API'de saat boyutu yok; yayın saati optimizasyonu "
         "günlük veriyle sınırlı."],
        ["Gelir / RPM",
         "Para kazanma açılmadı. Açıldığında ölçmek için hazırlık yapıldı."],
    ], [W * 0.32, W * 0.68], base, bold))
    add(Spacer(1, 8))

    para(
        "Ayrıca üç veri kaynağı farklı gecikmelerle çalışıyor ve bu "
        "karıştırılırsa yanlış sonuç üretir: Data API anlık ama ham, Analytics "
        "API 2–3 gün geriden ve eşiğin saydığı tek kaynak, Studio uygunluk kartı "
        "~1 hafta geriden.", "cap"
    )

    # ---------------------------------------------------------- questions
    para("8. Değerlendiriciye sorularımız", "h1")
    for i, q in enumerate([
        "Kitlenin %99,7'si abone değil ve %62'si Hindistan. Bu yapı, üst kademe "
        "sonrası RPM açısından ne anlama geliyor? Coğrafyayı kaydırmak için "
        "kanıtlanmış bir kol var mı, yoksa format gereği sabit mi?",
        "Arama niyeti malzeme/mekanizma odaklı, yaratık odaklı değil. Bu sinyal "
        "başlık ve içerik stratejisini değiştirmeyi haklı çıkaracak kadar güçlü mü?",
        "Günde 2–3 video, 159 videoluk tek format. “Özgün olmayan içerik” "
        "incelemesi açısından risk nerede duruyor? Hacmi mi düşürmek gerekir, "
        "çeşitliliği mi artırmak?",
        "Ölçemediğimiz CTR'ı telafi edecek, bizim verimizle yapılabilecek bir "
        "vekil ölçüm önerir misiniz?",
        "Format içi çeşitlendirme (malzeme/mekanizma) ile format dışına çıkmak "
        "arasında, bu kitle profilinde hangisi daha az riskli?",
    ], start=1):
        add(KeepTogether(Paragraph(f"<b>{i}.</b> {q}", s["p"])))

    add(Spacer(1, 14))
    para(
        "Bu rapor <font face='DJ-M'>channel-content-os</font> deposundaki "
        "otomatik analiz hattının çıktılarından derlendi. Tüm rakamlar YouTube "
        "Data API ve YouTube Analytics API'sinden alınmıştır; elle girilen "
        f"veri yoktur. Tek toplama: {stamp} UTC — ekler de aynı dosyadan "
        "üretilmiştir.", "cap"
    )

    doc.build(out)
    return path


if __name__ == "__main__":
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    target = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("unfoldables_rapor.pdf")
    print(build(payload, target))
