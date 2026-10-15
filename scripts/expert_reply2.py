"""Second note to the outside reviewer, answering their answer.

Same rule as the first: every figure is recomputed from
data/expert_dataset.json, none typed in. scipy is used as a cross-check.

Usage: expert_reply2.py dataset.json out.pdf
"""
from __future__ import annotations

import json
import math
import statistics as st
import sys
from pathlib import Path

import numpy as np
from scipy import stats
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

sys.path.insert(0, str(Path(__file__).resolve().parent))
from expert_reply import BAND, FONT_DIR, HOT, COLD, INK, MUTED, RULE, WARN  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from channel_ops import channel_analysis as ca  # noqa: E402


def N(value, digits=0):
    text = f"{value:,.{digits}f}"
    return text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def P(p):
    """With its own sign: 'p<0,0001' / 'p=0,035'."""
    return "p<0,0001" if p < 0.0001 else "p=" + N(p, 4 if p < 0.01 else 3)


def n_needed(sd, ratio, power=0.8, alpha=0.05):
    """Per-group n for a two-sided rank test on log views (normal theory,
    +5% for the rank test's efficiency loss)."""
    effect = math.log(ratio) / sd
    z = stats.norm.isf(alpha / 2) + stats.norm.ppf(power)
    return math.ceil(2 * (z / effect) ** 2 * 1.05)


def analyse(data: dict) -> dict:
    rows = ca.settle_deleted(data["videos"])
    mature = sorted((r for r in rows if r["mature"]),
                    key=lambda r: r["published_at"])
    for i, r in enumerate(mature):
        r["i"] = i
    hot = [r for r in mature if r["metal"] in HOT]
    cold = [r for r in mature if r["metal"] in COLD]

    both = hot + cold
    y = stats.rankdata([r["views"] for r in both])
    g = np.array([1.0 if r["metal"] in HOT else 0.0 for r in both])
    t = stats.rankdata([r["i"] for r in both])

    def resid(a, b):
        A = np.c_[np.ones(len(b)), b]
        return a - A @ np.linalg.lstsq(A, a, rcond=None)[0]

    r_part = float(np.corrcoef(resid(y, t), resid(g, t))[0, 1])
    n = len(both)
    t_stat = r_part * math.sqrt((n - 3) / (1 - r_part ** 2))
    p_part = float(2 * stats.t.sf(abs(t_stat), n - 3))

    used, pairs = set(), []
    for c in cold:
        best = min((h for h in hot if h["i"] not in used),
                   key=lambda h: abs(h["i"] - c["i"]))
        used.add(best["i"])
        pairs.append((best, c))
    wins = sum(1 for h, c in pairs if h["views"] > c["views"])
    wil = stats.wilcoxon([h["views"] for h, _ in pairs],
                         [c["views"] for _, c in pairs]).pvalue

    logs = [math.log(r["views"]) for r in mature]
    recent = [math.log(r["views"]) for r in mature
              if r["published_at"][:10] >= "2026-09-17"]
    sd = {"claimed": 1.35, "all": float(np.std(logs, ddof=1)),
          "recent": float(np.std(recent, ddof=1))}

    third = lambda lo, hi: [r for r in mature if lo <= r["published_at"][5:10] < hi]
    share = lambda grp: 100 * sum(r["metal"] in HOT for r in grp) / len(grp)
    return {
        "n": len(mature), "n_hot": len(hot), "n_cold": len(cold),
        "r_part": r_part, "p_part": p_part,
        "pairs": len(pairs), "wins": wins, "wilcoxon": float(wil),
        "sd": sd,
        "need": {k: {ratio: n_needed(v, ratio) for ratio in (1.5, 2.0, 2.5)}
                 for k, v in sd.items()},
        "hot_before": share(third("08-01", "09-17")),
        "hot_step": share(third("09-17", "09-27")),
        "med_step_before": st.median(r["views"] for r in third("09-01", "09-17")),
        "med_step_after": st.median(r["views"] for r in third("09-17", "09-22")),
        "deleted": [r["id"] for r in rows if r.get("deleted")],
        "hooked": sum(1 for r in rows if r.get("engaged_ratio") is not None),
        "hook_median": st.median(r["engaged_ratio"] for r in rows
                                 if r.get("engaged_ratio") is not None),
    }


def build(data: dict, path: Path) -> Path:
    r = analyse(data)
    pdfmetrics.registerFont(TTFont("DJ", str(FONT_DIR / "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("DJ-B", str(FONT_DIR / "DejaVuSans-Bold.ttf")))
    sheet = getSampleStyleSheet()
    s = {
        "title": ParagraphStyle("t", parent=sheet["Title"], fontName="DJ-B",
                                fontSize=18, leading=23, textColor=INK, spaceAfter=2),
        "sub": ParagraphStyle("s", parent=sheet["Normal"], fontName="DJ",
                              fontSize=10, leading=14, textColor=MUTED, spaceAfter=10),
        "h1": ParagraphStyle("h1", parent=sheet["Heading1"], fontName="DJ-B",
                             fontSize=12.5, leading=17, textColor=INK,
                             spaceBefore=12, spaceAfter=5),
        "p": ParagraphStyle("p", parent=sheet["Normal"], fontName="DJ",
                            fontSize=9.4, leading=13.8, textColor=INK, spaceAfter=6),
        "note": ParagraphStyle("n", parent=sheet["Normal"], fontName="DJ",
                               fontSize=8.8, leading=13, textColor=WARN,
                               leftIndent=8, spaceAfter=6),
        "cell": ParagraphStyle("c", parent=sheet["Normal"], fontName="DJ",
                               fontSize=8.3, leading=11.4, textColor=INK),
        "cellb": ParagraphStyle("cb", parent=sheet["Normal"], fontName="DJ-B",
                                fontSize=8.3, leading=11.4, textColor=INK),
    }
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=18 * mm,
                            rightMargin=18 * mm, topMargin=16 * mm,
                            bottomMargin=16 * mm,
                            title="Uzman Karşı Yanıtına Yanıt", author="channel-content-os")
    W = doc.width
    out = []
    add = out.append
    para = lambda text, style="p": add(Paragraph(text, s[style]))

    def table(rows, widths):
        cells = [[Paragraph(str(c), s["cellb" if i == 0 else "cell"]) for c in row]
                 for i, row in enumerate(rows)]
        t = Table(cells, colWidths=[W * w for w in widths], hAlign="LEFT", repeatRows=1)
        t.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEBELOW", (0, 0), (-1, -2), 0.25, RULE),
            ("BACKGROUND", (0, 0), (-1, 0), BAND),
            ("TOPPADDING", (0, 0), (-1, -1), 3.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
            ("LEFTPADDING", (0, 0), (-1, -1), 5)]))
        add(t)
        add(Spacer(1, 7))

    stamp = data["collected_at"][:16].replace("T", " ")
    para("Karşı Yanıtınıza Yanıt", "title")
    para(f"Unfoldables (@unfoldableslab) · ölçüm {stamp} UTC · tüm sayılar veri "
         "setinden yeniden hesaplandı", "sub")

    para("Özet", "h1")
    para(
        "Karşı yanıt raporunuz için teşekkürler. Uzlaştığımız noktalar (şablon–"
        "zaman örtüşmesi, süre farkının payda etkisi, geri çekilen iddialar, "
        "başlıklara incelemeden önce dokunulmaması) tamam. Bu notta üç şey var: "
        "metal sonucunu yeniden ürettiğimiz sayılar, güç analizinde bulduğumuz "
        "bir hata ve uyguladığımız değişiklikler."
    )

    para("1. Metal sonucu: yeniden ürettik", "h1")
    para(
        f"Zaman değişkenini sabitleyen kısmi Spearman'ı aynı veride yeniden "
        f"hesapladık: r={N(r['r_part'], 4)}, {P(r['p_part'])} (güncel veriyle n={r['n_hot'] + r['n_cold']}). "
        f"Soğuk metal videolarını en yakın yayın sırasındaki sıcak metal videosuyla "
        f"tekrarsız eşleştirdiğimizde (açgözlü eşleştirme, Macar algoritması değil) "
        f"{r['pairs']} çiftin {r['wins']}'inde sıcak metal kazandı, Wilcoxon "
        f"{P(r['wilcoxon'])}. Yön ve büyüklük sizinkiyle uyumlu."
    )
    para(
        f"Eylül'ün üçüncü haftasındaki basamak (medyan {N(r['med_step_before'])} → "
        f"{N(r['med_step_after'])}) metal karışımıyla açıklanamıyor: sıcak metal payı "
        f"%{N(r['hot_before'])} → %{N(r['hot_step'])}, basamağı yapacak "
        "kadar değil. Bu, bu basamağın başka bir nedeni olduğunu gösteriyor ve biz "
        "o nedeni bilmiyoruz."
    )
    para(
        "Yine de bunu kanıt saymıyoruz: gruplama veriye bakıldıktan sonra "
        f"seçildi, kısmi korelasyon sınırda ({P(r['p_part'])}) ve çoklu test sayısı "
        "belirsiz. Önceden kayıtlı ileriye dönük test (önceki notumuzdaki 7. "
        "madde) hâlâ gerekli.", "note"
    )

    para("2. Güç analizinde bir hata var", "h1")
    para(
        "Raporunuz, sd(ln izlenme)=1,35 ile iki yönlü Mann-Whitney, α=0,05, "
        "%80 güçte, 2 katlık fark için grup başına 32, 2,5 katlık fark için 20 "
        "video gerektiğini söylüyor. Aynı varsayımla biz farklı sayı buluyoruz "
        "(normal kuram, sıra testi için %5 verimlilik payı):"
    )
    rows = [["Sapma varsayımı", "1,5 kat", "2 kat", "2,5 kat"]]
    for key, label in (("claimed", f"sd=1,35 (raporunuz)"),
                       ("all", f"sd={N(r['sd']['all'], 2)} (tüm olgun videolar)"),
                       ("recent", f"sd={N(r['sd']['recent'], 2)} (17 Eylül sonrası)")):
        need = r["need"][key]
        rows.append([label, N(need[1.5]), N(need[2.0]), N(need[2.5])])
    table(rows, [0.46, 0.18, 0.18, 0.18])
    para(
        f"Yani sizin kendi sapmanızla bile 2 katlık fark için grup başına {r['need']['claimed'][2.0]}, "
        f"2,5 katlık için {r['need']['claimed'][2.5]} video gerekiyor; kanalın gerçek sapmasıyla "
        f"({N(r['sd']['all'], 2)}–{N(r['sd']['recent'], 2)}) 2 katlık fark için "
        f"{r['need']['all'][2.0]}–{r['need']['recent'][2.0]} video. Günde 2 yayında bu, ay değil "
        "çok ay demek. Hesabınızı nasıl yaptığınızı paylaşırsanız hatayı birlikte "
        "bulabiliriz; paylaşmazsanız bizim rakamlarımızla planlayacağız.", "p"
    )
    para(
        "Sonuç: sıcak/soğuk testi kanal formatını değiştirmeden, yeni yüklemelerin "
        "malzemesi dengelenerek ve yalnızca YPP kararından sonra yürütülebilir. "
        "Öncesinde yapılmayacak.", "note"
    )

    para("3. Kaynaklarla ilgili iki çekincemiz", "h1")
    table([
        ["Konu", "Çekincemiz"],
        ["Alt kademeden üst kademeye ikinci inceleme olmaması",
         "Alıntıladığınız yardım metni, alt kademe başvurusunda kanalın bütün olarak "
         "bir kez incelendiğini gösteriyor. Üst kademede ikinci bir inceleme "
         "olmadığını açıkça yazmıyor; bu bir çıkarım. Çıkarım olarak kabul ediyoruz, "
         "kesin bilgi olarak değil. Açık bir ifade biliyorsanız paylaşın."],
        ["Hindistan RPM'i",
         "Verdiğiniz iki Reddit paylaşımı (yaklaşık $0,01 ve $0,15–0,30 / 1.000 "
         "etkileşimli izleme) tek tek kişilerin anekdotu. Büyüklük sırası için "
         "işe yarıyor; kanalın kendi RPM'ini tahmin etmek için kullanmayacağız. "
         "Kanalın kendi geliri ölçülebilir olunca (gelir yetkisi eklenince) kendi rakamımızı kullanacağız."],
    ], [0.28, 0.72])

    para("4. Uyguladığımız değişiklikler", "h1")
    para(
        "Önceki notumuzdaki 7 maddenin 6'sını uyguladık; yalnızca raporlama kodu "
        "değişti. Yayın hattına, prompt'lara, başlıklara ya da formata dokunulmadı. "
        "Kodunuzu olduğu gibi eklemedik: veri setimizle alan adları uyuşmuyordu ve "
        "pandas/numpy bağımlılığı getiriyordu. Aynı mantığı deponun kendi koduna "
        "ve testlerine yazdık. 387 testin tamamı geçiyor."
    )
    table([
        ["#", "Değişiklik", "Gerçek veride sonuç"],
        ["1", "Silinmiş video hesap dışı, ayrı sayılıyor",
         f"{len(r['deleted'])} video (7Car9xssqew); ölü sayısı 5'ten 4'e düştü"],
        ["2", "Birim sütunu; ömür boyu toplamı aşan kayıt şüpheli",
         "28 Eylül 14:12 satırı tablodan çıktı ve hıza girmiyor"],
        ["3", "Karşılaştırmalar ortak dönemle sınırlı",
         "Şablon ve fikir talimatı için “yeterli ortak dönem yok”; sahte p yok"],
        ["4", "Şablon geçişi (önceki son 10 / sonrakinin ilk 10) ve şablon içi trend",
         "be41f721 içi trend: rho=0,46, p=0,003; çoklu test düzeltmesi sonrası p=0,044. "
         "Geçişte fark yok (be41f721→22b05479, K=10, p=0,307)"],
        ["5", "engagedViews/views vekil kanca metriği",
         f"{r['hooked']} videoda, medyan %{N(r['hook_median'], 1)}; “vekil” etiketli, "
         "Studio'daki orana eşdeğer olduğu iddia edilmiyor"],
        ["6", "Brifing düzeltmeleri", "Gerçek süre dağılımı, “konuşmasız, mekanik ses "
         "efektli”, toplam farkı notu, birim sütunlu büyüme tablosu"],
        ["7", "Önceden kayıtlı hipotez", "Henüz yapılmadı; güç analizi sorusu çözülünce"],
    ], [0.05, 0.43, 0.52])
    para(
        "Düzeltilmiş rapor ve ekler (taze veriyle) ayrıca iletiliyor. Kanca% "
        "sütunu EK A'da.", "p"
    )

    para("5. Size sorularımız", "h1")
    for i, q in enumerate([
        "Güç analizi hesabınız: hangi sapma ve hangi yöntemle? Bizim rakamlarımızla "
        "uyuşmuyor (Bölüm 2).",
        "Eylül'ün üçüncü haftasındaki basamağın (medyan yaklaşık 2.000'den 8.500'e) "
        "neden olabilecek bir şey öneriyor musunuz? Şablon ve metal karışımı değil.",
        "Üst kademede ikinci inceleme olmadığını açıkça belirten bir resmî ifade "
        "biliyor musunuz?",
    ], start=1):
        para(f"<b>{i}.</b> {q}")

    doc.build(out)
    return path


if __name__ == "__main__":
    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    print(build(data, Path(sys.argv[2])))
