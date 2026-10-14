"""The appendix volume: every row behind the brief, nothing summarised.

Landscape, because the per-video table has thirteen columns and squeezing it
into portrait would mean dropping some -- and which ones to drop is exactly
the judgement an outside reviewer is being handed the data to make for
themselves.

Reads data/expert_dataset.json, so these pages and the brief cannot disagree:
there is one collection, with one timestamp, and both read it.

Usage: expert_appendix.py dataset.json out.pdf
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
INK = colors.HexColor("#1a1a1a")
MUTED = colors.HexColor("#5f6368")
RULE = colors.HexColor("#d0d3d6")
BAND = colors.HexColor("#f2f3f5")
ZEBRA = colors.HexColor("#fafbfc")
HIT = colors.HexColor("#e8f3ea")
DEAD = colors.HexColor("#fdeaea")

COUNTRY = {
    "IN": "Hindistan", "BD": "Bangladeş", "MM": "Myanmar", "UZ": "Özbekistan",
    "US": "ABD", "ID": "Endonezya", "NP": "Nepal", "PK": "Pakistan",
    "GB": "Birleşik Krallık", "TR": "Türkiye", "BR": "Brezilya",
    "PH": "Filipinler", "EG": "Mısır", "VN": "Vietnam", "RU": "Rusya",
    "DE": "Almanya", "FR": "Fransa", "MX": "Meksika", "LK": "Sri Lanka",
    "IQ": "Irak", "SA": "Suudi Arabistan", "NG": "Nijerya", "CA": "Kanada",
}
TRAFFIC = {
    "SHORTS": "Shorts akışı", "YT_SEARCH": "YouTube araması",
    "YT_CHANNEL": "Kanal sayfası", "SUBSCRIBER": "Abonelikler",
    "YT_OTHER_PAGE": "Diğer YouTube sayfaları", "HASHTAGS": "Hashtag",
    "SOUND_PAGE": "Ses sayfası", "EXT_URL": "Harici bağlantı",
    "NOTIFICATION": "Bildirim", "RELATED_VIDEO": "İlgili video",
    "YT_PLAYLIST_PAGE": "Oynatma listesi", "PLAYLIST": "Oynatma listesi",
    "NO_LINK_OTHER": "Bağlantısız diğer", "ADVERTISING": "Reklam",
    "END_SCREEN": "Bitiş ekranı",
}
DEVICE = {"MOBILE": "Mobil", "TABLET": "Tablet", "DESKTOP": "Masaüstü",
          "TV": "TV", "GAME_CONSOLE": "Oyun konsolu"}


def _fonts():
    pdfmetrics.registerFont(TTFont("DJ", str(FONT_DIR / "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("DJ-B", str(FONT_DIR / "DejaVuSans-Bold.ttf")))
    pdfmetrics.registerFont(TTFont("DJ-M", str(FONT_DIR / "DejaVuSansMono.ttf")))


def _styles():
    sheet = getSampleStyleSheet()
    return {
        "h1": ParagraphStyle("h1", parent=sheet["Heading1"], fontName="DJ-B",
                             fontSize=14, leading=18, textColor=INK,
                             spaceBefore=4, spaceAfter=5),
        "h2": ParagraphStyle("h2", parent=sheet["Heading2"], fontName="DJ-B",
                             fontSize=10.5, leading=14, textColor=INK,
                             spaceBefore=10, spaceAfter=4),
        "p": ParagraphStyle("p", parent=sheet["Normal"], fontName="DJ",
                            fontSize=8.8, leading=12.5, textColor=INK,
                            spaceAfter=6),
        "cap": ParagraphStyle("c", parent=sheet["Normal"], fontName="DJ",
                              fontSize=7.8, leading=11, textColor=MUTED,
                              spaceAfter=8),
    }


def _num(value, digits=0):
    if value is None or value == "":
        return "–"
    try:
        text = f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return str(value)
    return text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def _grid(rows, widths, *, size=6.6, right=(), zebra_from=1, marks=None):
    style = [
        ("FONTNAME", (0, 0), (-1, 0), "DJ-B"),
        ("FONTNAME", (0, 1), (-1, -1), "DJ"),
        ("FONTSIZE", (0, 0), (-1, -1), size),
        ("LEADING", (0, 0), (-1, -1), size + 3),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("BACKGROUND", (0, 0), (-1, 0), BAND),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, RULE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 1.6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.6),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]
    for index in range(zebra_from, len(rows)):
        if index % 2 == 0:
            style.append(("BACKGROUND", (0, index), (-1, index), ZEBRA))
    for index, colour in (marks or {}).items():
        style.append(("BACKGROUND", (0, index), (-1, index), colour))
    for col in right:
        style.append(("ALIGN", (col, 1), (col, -1), "RIGHT"))
    table = Table(rows, colWidths=widths, hAlign="LEFT", repeatRows=1)
    table.setStyle(TableStyle(style))
    return table


def build(dataset: dict, path: Path) -> Path:
    _fonts()
    s = _styles()
    doc = SimpleDocTemplate(
        str(path), pagesize=landscape(A4),
        leftMargin=12 * mm, rightMargin=12 * mm,
        topMargin=11 * mm, bottomMargin=11 * mm,
        title="Unfoldables — Ekler", author="channel-content-os",
    )
    W = doc.width
    out: list = []
    add = out.append

    def para(text, style="p"):
        add(Paragraph(text, s[style]))

    stamp = dataset.get("collected_at", "")[:16].replace("T", " ")

    # ------------------------------------------------------- EK A: videos
    para("EK A — Tüm videolar", "h1")
    para(
        f"159 videonun tamamı, yayın sırasına göre. Veri {stamp} UTC'de "
        "toplandı. <b>Yeşil</b> satırlar hit (10.000+ izlenme), <b>kırmızı</b> "
        "satırlar ölü video (48 saati doldurmuş ve 250 altında). İzlenme "
        "yüzdesi %100'ü aşabilir — Shorts'ta tekrar izlemeler sayılır."
    )

    videos = sorted(dataset.get("videos") or [],
                    key=lambda r: r.get("published_at", ""))
    head = ["#", "Tarih", "Sa", "Gün", "Yaratık", "Metal", "sn", "İzlenme",
            "İzl%", "Ab/1k", "Beğ/1k", "Yor/1k", "Şablon"]
    rows, marks = [head], {}
    for index, row in enumerate(videos, start=1):
        if row.get("hit"):
            marks[index] = HIT
        elif row.get("dead"):
            marks[index] = DEAD
        rows.append([
            str(index),
            (row.get("published_at") or "")[:10],
            row.get("hour", ""),
            row.get("weekday", ""),
            (row.get("creature") or "")[:26],
            row.get("metal", ""),
            str(row.get("seconds") or "–"),
            _num(row.get("views")),
            _num(row.get("retention"), 1),
            _num(row.get("subs_per_1k"), 2),
            _num(row.get("likes_per_1k"), 1),
            _num(row.get("comments_per_1k"), 2),
            row.get("template", ""),
        ])
    widths = [W * w for w in (.030, .068, .026, .034, .185, .072, .028,
                              .080, .058, .058, .060, .058, .063)]
    add(_grid(rows, widths, size=6.4, right=(7, 8, 9, 10, 11), marks=marks))
    add(PageBreak())

    # -------------------------------------------------- EK B: daily series
    para("EK B — Günlük zaman serisi", "h1")
    para("Analytics API'nin verdiği en uzun pencere. Abone kaybı dahil.")
    daily = (dataset.get("tables") or {}).get("daily") or {}
    drows = daily.get("rows") or []
    head = ["Gün", "İzlenme", "İzlenme dk", "Abone +", "Abone −", "Net abone"]
    rows = [head]
    for day, views, minutes, gained, lost in drows:
        rows.append([day, _num(views), _num(minutes), _num(gained),
                     _num(lost), _num((gained or 0) - (lost or 0))])
    half = (W - 10 * mm) / 2
    widths = [half * w for w in (.22, .17, .19, .14, .14, .14)]
    middle = len(rows) // 2 + 1
    left = _grid(rows[:middle], widths, size=6.6, right=(1, 2, 3, 4, 5))
    right = _grid([head] + rows[middle:], widths, size=6.6, right=(1, 2, 3, 4, 5))
    add(Table([[left, right]], colWidths=[half + 5 * mm, half + 5 * mm],
              hAlign="LEFT",
              style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")])))
    add(PageBreak())

    # ----------------------------------------------------- EK C: breakdown
    para("EK C — Kitle ve trafik kırılımları", "h1")
    para("Analytics API'nin döndürdüğü tam listeler; kesilmedi.")

    tables = dataset.get("tables") or {}

    def simple(name, title, head, translate=None, digits=0):
        payload = tables.get(name) or {}
        data = payload.get("rows") or []
        if not data:
            return None
        body = [head]
        for entry in data:
            label = entry[0]
            if translate:
                label = translate.get(label, label)
            body.append([str(label)] + [_num(v, digits) for v in entry[1:]])
        return body

    third = (W - 12 * mm) / 3
    blocks = []
    for name, title, head, tr in (
        ("countries", "Ülke", ["Ülke", "İzlenme"], COUNTRY),
        ("traffic", "Trafik kaynağı",
         ["Kaynak", "İzlenme", "İzl. dk"], TRAFFIC),
        ("devices", "Cihaz", ["Cihaz", "İzlenme"], DEVICE),
    ):
        body = simple(name, title, head, tr)
        if body:
            cols = len(head)
            widths = [third * (0.46 if cols == 2 else 0.40)] + \
                     [third * (0.54 if cols == 2 else 0.30)] * (cols - 1)
            blocks.append([Paragraph(title, s["h2"]),
                           _grid(body, widths, size=6.8,
                                 right=tuple(range(1, cols)))])
    add(Table([[b[0] for b in blocks]], colWidths=[third + 4 * mm] * len(blocks),
              hAlign="LEFT", style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                               ("LEFTPADDING", (0, 0), (-1, -1), 0)])))
    add(Table([[b[1] for b in blocks]], colWidths=[third + 4 * mm] * len(blocks),
              hAlign="LEFT", style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                               ("LEFTPADDING", (0, 0), (-1, -1), 0)])))
    add(Spacer(1, 8))

    blocks = []
    for name, title, head, digits in (
        ("demographics", "Yaş ve cinsiyet",
         ["Yaş", "Cinsiyet", "%"], 1),
        ("search_terms", "Arama terimi", ["Terim", "İzlenme"], 0),
        ("sharing", "Paylaşım uygulaması", ["Uygulama", "Paylaşım"], 0),
    ):
        payload = tables.get(name) or {}
        data = payload.get("rows") or []
        if not data:
            continue
        body = [head]
        for entry in data:
            body.append([str(entry[0])] + [_num(v, digits if i == len(entry) - 2 else 0)
                                           for i, v in enumerate(entry[1:])]
                        if len(entry) > 2 else [str(entry[0]), _num(entry[1], digits)])
        cols = len(head)
        widths = [third * (0.46 if cols == 2 else 0.34)] + \
                 [third * (0.54 if cols == 2 else 0.33)] * (cols - 1)
        blocks.append([Paragraph(title, s["h2"]),
                       _grid(body, widths, size=6.8,
                             right=(cols - 1,))])
    add(Table([[b[0] for b in blocks]], colWidths=[third + 4 * mm] * len(blocks),
              hAlign="LEFT", style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                               ("LEFTPADDING", (0, 0), (-1, -1), 0)])))
    add(Table([[b[1] for b in blocks]], colWidths=[third + 4 * mm] * len(blocks),
              hAlign="LEFT", style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                               ("LEFTPADDING", (0, 0), (-1, -1), 0)])))
    add(PageBreak())

    # ------------------------------------------------- EK D: measurements
    para("EK D — Ölçüm anlık görüntüleri", "h1")
    para(
        "Her haftalık analiz çalışmasında kaydedilen anlık görüntü. "
        "<b>shorts_measure</b> sütunu önemli: 2 Ekim'de ölçü birimi ham "
        "görüntülemeden etkileşimli görüntülemeye çevrildi, ve iki birim "
        "birbirine karıştırılmadan karşılaştırılamaz. Bundan önceki "
        "kayıtlar farklı bir cetvelle alınmıştır."
    )
    head = ["Zaman (UTC)", "Abone", "Toplam izlenme", "Video",
            "Shorts 90g", "Ölçü", "Medyan", "İzl%", "Ölü", "Hit", "Olgun"]
    rows = [head]
    for snap in dataset.get("history") or []:
        rows.append([
            (snap.get("at") or "")[:16].replace("T", " "),
            _num(snap.get("subscribers")),
            _num(snap.get("views")),
            _num(snap.get("videos")),
            _num(snap.get("shorts_views_90d")),
            snap.get("shorts_measure", "–"),
            _num(snap.get("median_views")),
            _num(snap.get("median_retention"), 1),
            _num(snap.get("dead_count")),
            _num(snap.get("hit_count")),
            _num(snap.get("mature_count")),
        ])
    widths = [W * w for w in (.145, .080, .120, .070, .110, .085,
                              .085, .070, .060, .060, .075)]
    add(_grid(rows, widths, size=7, right=tuple(range(1, 5)) + (6, 7, 8, 9, 10)))
    add(Spacer(1, 10))

    para(
        f"Bu ekler data/expert_dataset.json dosyasından üretildi "
        f"(toplama: {stamp} UTC). Tüm rakamlar YouTube Data API ve YouTube "
        "Analytics API'sinden gelir; elle girilen veri yoktur. Eksik ölçüm "
        f"bildirimi: {dataset.get('errors') or 'yok'}.", "cap"
    )

    doc.build(out)
    return path


if __name__ == "__main__":
    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    print(build(data, Path(sys.argv[2])))
