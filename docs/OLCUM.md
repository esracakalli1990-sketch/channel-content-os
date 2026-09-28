# Ölçüm sistemi

## Ne ne yapıyor

| Dosya | İş |
|---|---|
| `src/channel_ops/channel_data.py` | API'lerden veriyi bir kez toplar. Denetim de analiz de bunu okur, yani ikisi asla farklı rakam göremez. |
| `src/channel_ops/channel_analysis.py` | Hüküm verir: eşiklere mesafe, hangi değişiklik işe yaradı, hangi videolar başarısız. |
| `scripts/channel_audit.py` | Ham tabloları basar (elle bakmak için). |
| `scripts/channel_report.py` | Haftalık/aylık raporu basar, Telegram'a özet atar, ölçümü kaydeder. |
| `data/channel_metrics.json` | Her ölçümün kalıcı kaydı. Hız ve "geçen haftaya göre" farkları buradan gelir. |
| `data/studio_manual.json` | Studio'dan elle girilen gösterim/CTR kayıtları. |

## İki kademe

Rapor iki ayrı eşik takımı gösteriyor. Karıştırmamak önemli:

| | Alt kademe | Üst kademe |
|---|---|---|
| Abone | 500 | 1.000 |
| Son 90 günde yükleme | 3 | — |
| İzlenme saati (12 ay) | 3.000 | 4.000 |
| **veya** 90 günlük Shorts | 3.000.000 | 10.000.000 |
| **Ne veriyor** | Hayran desteği (Super Thanks, üyelik, mağaza) | **Reklam geliri** |

### Shorts eşiği hangi sayıyla ölçülüyor

**Ham görüntülemeyle DEĞİL.** Mart 2025'ten beri Shorts'ta her oynatma ve
tekrar "görüntüleme" sayılıyor, ama eşikler **etkileşimli görüntülemeyi**
(engagedViews) sayıyor — eski, daha katı ölçüm.

Bu kanalda oran **%44,8**: 88 günde 2.259.715 ham görüntülemeye karşılık
1.012.547 etkileşimli. Studio'nun Gelir sekmesi de aynısını söylüyor —
23 Eylül'de 795 B geçerli Shorts görüntülemesi.

28 Eylül'de eşik ham görüntülemeyle hesaplandığı için **geçilmiş** diye
raporlandı, oysa üçte birindeydi. Rapor artık etkileşimliyi kullanıyor; o veri
yoksa rakamı basıyor ama yanına güvenilmez uyarısı koyuyor.

Alt kademe reklam geliri **vermiyor**. Her kademede abone şartı ve izlenme
şartı birlikte gerekiyor; alternatif olanlar sadece iki izlenme yolu.

Eşik rakamları YouTube'un yayınlanmış kurallarından geliyor ve YouTube bunları
zaman zaman değiştiriyor — Studio'nun kendi gösterdiği rakamla karşılaştır.

## Ne zaman çalışıyor

- **Haftalık:** her Cuma 22:00 UTC
- **Aylık:** ayın 1'i 22:00 UTC (haftalığın her şeyi + uzun video bölümü)
- Elle: Actions → "Kanal analizi" → weekly/monthly

## Ölçülemeyen şey

**Gösterim (impressions) ve CTR Analytics API'de YOK.** Denendi, `HTTP 400
Unknown identifier (impressions)` döndü. Bunlar Studio'ya özel. Yani kapak ve
başlık kararları CTR'a göre otomatikleştirilemez; bunu yaptığını iddia eden bir
sistem uyduruyordur.

Studio'dan okuyup kaydetmek istersen `data/studio_manual.json`:

```json
[{"date": "2026-09-25", "video_id": "N1POiCYZw2o", "impressions": 1200000, "ctr": 4.1}]
```

Girersen rapor o bölümü doldurur.

## Neden istatistik eşiği var

Bu kanalda dört kez az veriden sonuç çıkarıp yanlış karar verildi:

| Tarih | İddia | Sonuç |
|---|---|---|
| 24 Ağustos | Tek hitten prompt kuralı yazıldı | 19 hitsiz video, %44 düşüş |
| 25 Ağustos | "Ölü videoların sebebi hacim" | Yanlış, ölü videolar devam etti |
| 10 Eylül | "Makineler daha iyi gidiyor" | 18 Eylül'de geri çekildi |
| 18 Eylül | "Derleme 4.000 saati getirir" | 21 Eylül'de geri çekildi |

Bu yüzden hiçbir karşılaştırma "bence fark var" diyemiyor. Her biri iki şartı
geçmek zorunda:

1. Her iki grupta en az **8** olgun video (`MIN_GROUP`)
2. Sıra toplamı testinde **p < 0,05** (`SIGNIFICANCE`)

Geçemezse rapor "yeterli veri yok" veya "fark yok" yazar. **Bunlar gerçek
cevaplardır, sistemin çalışmadığı anlamına gelmez.**

Ortalama değil medyan ve sıra testi kullanılıyor, çünkü bu kanalda tek bir
video tüm izlenmenin %57'si — ortalamaya dayanan her test aslında o videonun
nereye düştüğünü ölçer.

## Olgunluk

48 saatten genç videolar hiçbir karşılaştırmaya girmez. Sabah yayınlanan bir
video henüz dağıtılıyor; medyana katılması karşılaştırmayı "en son ne zaman
değişiklik yaptık" ölçümüne çevirir.
