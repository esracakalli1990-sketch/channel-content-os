"""Build the channel briefing PDF handed to an outside reviewer.

Written to be read by someone who has never seen this channel: a data
analyst, an audience strategist, a YouTube specialist. So it states what the
channel is, where it stands, how the numbers were measured, and -- at least
as important -- what could NOT be measured and where the analysis has
previously been wrong. A brief that only shows the good numbers invites
advice built on the parts that were left out.

Figures are passed in rather than fetched, so the document is reproducible
and nobody has to wonder whether two sections were written on two days.

Usage: expert_brief.py [output.pdf]
"""
from __future__ import annotations

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

# Helvetica has no ğ, ı, İ, ş. DejaVu does, and ships with the runner image.
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


def build(path: Path) -> Path:
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

    def para(text, style="p"):
        add(Paragraph(text, s[style]))

    # ---------------------------------------------------------------- cover
    para("Unfoldables — Kanal Durum Raporu", "title")
    para("@unfoldableslab · YouTube Shorts · 14 Ekim 2026 · "
         "dış değerlendirme için hazırlandı", "sub")

    para("1. Özet", "h1")
    para(
        "Kanal 29 Temmuz 2026'da yayına başladı. 14 Ekim itibarıyla "
        "<b>159 video, 5.470 abone, 7,9 milyon izlenme</b>. YouTube İş Ortaklığı "
        "Programı'nın <b>alt kademe eşiği 13 Ekim'de geçildi</b> "
        "(90 günde 3.202.930 etkileşimli Shorts görüntülemesi, eşik 3.000.000). "
        "Başvuru düğmesi 14 Ekim'de açıldı; başvuru henüz yapılmadı."
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
        ["Abone", "5.470", "14 Eki"],
        ["Toplam izlenme", "7.942.552", "13 Eki ölçümü"],
        ["Video", "159", "tamamı Shorts, 9–11 sn"],
        ["Olgun video medyanı", "2.897 izlenme", "48 saat+ olanlar"],
        ["Medyan izlenme yüzdesi", "%131", "tekrar izleme dahil"],
        ["Ölü video (48 saatte <250)", "5 / 152 = %3,3", "en düşük seviye"],
        ["Hit (10.000+ izlenme)", "40", ""],
        ["En yüksek video", "1.907.124", "12 Eyl"],
    ], [W * 0.34, W * 0.28, W * 0.38], base, bold))
    add(Spacer(1, 10))

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
        ["Abone", "5.470", "500", "geçildi"],
        ["Son 90 günde yükleme", "157", "3", "geçildi"],
        ["Etkileşimli Shorts (90 gün)", "3.202.930", "3.000.000", "%106,8"],
        ["ÜST KADEME — reklam geliri", "", "", ""],
        ["Abone", "5.470", "1.000", "geçildi"],
        ["İzlenme saati (uzun video)", "3,2", "4.000", "%0,08"],
        ["Etkileşimli Shorts (90 gün)", "3.202.930", "10.000.000", "%32,0"],
    ], [W * 0.40, W * 0.21, W * 0.21, W * 0.18], base, bold, align_right=(1, 2, 3)))
    add(Spacer(1, 6))
    para(
        "Üst kademe için günlük hız 137.400 etkileşimli görüntüleme; bu tempoda "
        "tahmini tarih <b>1 Aralık 2026</b>. İzlenme saati yolu kapalı: Shorts "
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
    add(_table([
        ["Tarih", "Abone", "Toplam izlenme", "Video", "Etkileşimli 90g", "Medyan", "Ölü", "Hit"],
        ["25 Eyl", "1.070", "2.048.124", "123", "1.748.243", "2.159", "11", "19"],
        ["27 Eyl", "1.540", "2.618.818", "127", "2.260.227", "2.286", "9", "22"],
        ["2 Eki", "2.610", "4.206.937", "136", "1.656.383", "2.569", "7", "28"],
        ["3 Eki", "2.770", "4.426.241", "138", "1.822.334", "2.489", "9", "28"],
        ["8 Eki", "4.160", "6.529.734", "148", "2.623.498", "2.707", "7", "35"],
        ["10 Eki", "4.550", "6.702.647", "151", "2.784.527", "2.799", "7", "35"],
        ["13 Eki", "5.300", "7.942.552", "157", "3.202.930", "2.897", "5", "40"],
    ], [W * 0.11, W * 0.11, W * 0.19, W * 0.09, W * 0.21, W * 0.12, W * 0.08, W * 0.09],
        base, bold, align_right=(1, 2, 3, 4, 5, 6, 7)))
    add(Spacer(1, 6))
    para(
        "3 Eylül'de 159 abone vardı. 25 Eylül–13 Ekim arasında abone 1.070 → "
        "5.300, toplam izlenme 2,0M → 7,9M. Ölü video oranı %8,9'dan %3,3'e "
        "düşerken hit sayısı 19'dan 40'a çıktı — yani büyüme tek bir viral "
        "videodan değil, tabanın yükselmesinden geliyor.", "p"
    )
    para(
        "2 Ekim satırındaki etkileşimli düşüş (2,26M → 1,66M) hata değil: 90 "
        "günlük kayan pencereden eski videoların düşmesi.", "cap"
    )

    add(PageBreak())

    # ------------------------------------------------------------- format
    para("4. Format", "h1")
    para(
        "Her video 9–11 saniyelik, dikey, sessiz bir Short. Sahne sabit: bir el "
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
        "amber reçine…). Başlık her zaman bir soru: <i>“Why does this curved "
        "bronze shell have a walnut base?”</i>"
    )
    para(
        "Yapay zekâ beyanı 159 videonun tamamında açık "
        "(<font face='DJ-M'>containsSyntheticMedia</font>). 12–14 Ekim'de "
        "geriye dönük işlendi; yeni videolar yüklenirken otomatik beyan ediliyor.", "p"
    )

    # ------------------------------------------------------------ audience
    para("5. Kitle", "h1")
    para(
        "<b>Kitlenin yapısı stratejik olarak en önemli bölüm.</b> İzlenmelerin "
        "%99,7'si abone olmayanlardan geliyor; bu bir topluluk değil, akış "
        "kitlesi.", "note"
    )

    half = (W - 8 * mm) / 2
    left = _table([
        ["Ülke", "İzlenme"],
        ["Hindistan", "3.725.969"],
        ["Bangladeş", "197.392"],
        ["Myanmar", "168.740"],
        ["Özbekistan", "148.581"],
        ["ABD", "125.643"],
        ["Endonezya", "95.859"],
        ["Nepal", "76.437"],
        ["Pakistan", "66.837"],
    ], [half * 0.52, half * 0.48], base, bold, align_right=(1,))
    right = _table([
        ["Yaş / cinsiyet", "%"],
        ["65+ erkek", "19"],
        ["25–34 erkek", "17"],
        ["55–64 erkek", "17"],
        ["45–54 erkek", "14"],
        ["35–44 erkek", "13"],
        ["18–24 erkek", "6"],
        ["25–34 kadın", "4"],
        ["35–44 kadın", "2"],
    ], [half * 0.60, half * 0.40], base, bold, align_right=(1,))
    add(Table([[left, right]], colWidths=[half + 4 * mm, half + 4 * mm],
              hAlign="LEFT", style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")])))
    add(Spacer(1, 8))

    add(_table([
        ["Trafik kaynağı", "İzlenme", "İzlenme dk"],
        ["Shorts akışı", "6.838.147", "770.581"],
        ["YouTube araması", "33.240", "3.375"],
        ["Kanal sayfası", "19.748", "2.453"],
        ["Abonelikler", "10.751", "1.417"],
        ["Diğer YouTube sayfaları", "8.388", "1.043"],
        ["Hashtag", "3.140", "294"],
        ["Ses sayfası", "2.073", "284"],
        ["Harici bağlantı", "702", "101"],
    ], [W * 0.42, W * 0.29, W * 0.29], base, bold, align_right=(1, 2)))
    add(Spacer(1, 8))

    para("Arama terimleri — kanala arama ile gelenler", "h2")
    para(
        "<font face='DJ-M'>copper 316 · brass 155 · automata 130 · steampunk 123 · "
        "bronze 116 · mechanism 105 · titanium 66</font>", "mono"
    )
    add(Spacer(1, 5))
    para(
        "Listede <b>tek bir yaratık adı yok</b>. Hepsi malzeme ve mekanizma "
        "kelimesi. Arama trafiği toplamın %0,4'ü olsa da niyeti gösteriyor: "
        "izleyici bronzun ve mekanizmanın peşinde geliyor, hayvanın değil. "
        "Bu, içerik yönü tartışmasında elimizdeki en net sinyal.", "p"
    )
    add(Spacer(1, 4))
    para(
        "Cihaz: %97,5 mobil (6.747.741), tablet 73.086, masaüstü 56.400, TV 39.281. "
        "Paylaşımların yarısı WhatsApp üzerinden (329), ardından kopyala-yapıştır (116).", "cap"
    )

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
        "veri yoktur. Ölçüm tarihleri bölüm başlıklarında belirtilmiştir.", "cap"
    )

    doc.build(out)
    return path


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("unfoldables_rapor.pdf")
    print(build(target))
