# Hacim denemesi: günde 2 yerine 3 video

**Başlangıç:** 18 Ekim 2026, 11:00 UTC (ilk üçlü gün) · **Süre:** 14 gün (18–31 Ekim,
42 video) · **Karar tarihi:** 6 Kasım (videoların olgunlaşması ve Analytics'in
3 gün gecikmesi için) · **Karar veren:** kanal sahibi

## Neden

Kanalın dağıtımı hit odaklı: ilk 10 video toplamın %82'sini taşıyor ve bugüne
kadar bir hiti kontrol eden bir şey (konu, malzeme, başlık, saat) bulunamadı.
Elde kalan tek kol çekiliş sayısı. 25 Ağustos'ta hacmi düşüren kuram sonradan
çürütüldü (`shorts_pipeline.py`, `PUBLISH_SLOTS_UTC`), ve haftalık kayıtta hacmin
zarar verdiğine dair bir iz yok: 18–19 videoluk haftalar 4–5 hit, 13–14 videoluk
haftalar 7–8 hit verdi (sıçrama hacimden değil başka bir şeyden geldi).

Beklenen kazanç, iyimser senaryoda (her video bağımsız çalışırsa) etkileşimli
izlenmede yaklaşık %18 artış, yani 10M tahmininde 6–7 gün öne çekme. Hitler çok
çarpık dağıldığı için 14 günde bundan çok farklı bir sonuç çıkabilir.

## Ne değişti

| Şey | Önce | Şimdi |
|---|---|---|
| Yayın saatleri (UTC) | 17:00, 23:00 | **11:00**, 17:00, 23:00 |
| Günlük fikir sayısı | 2 | **3** |

11:00 UTC = 14:00 TRT = 16:30 Hindistan = 07:00 New York. Gün 6-6-12 saat
aralıklarla bölünüyor. Bu saat bir seçim: güncel rejimde o saatte hiç video
yayınlanmadı, eski öğle kaydı (11:00–15:00, hit oranı %11'e karşı akşamların
%16–20'si) ilk haftalardan geliyor ve onlardan ayrılamıyor. Bu yüzden saatin
kendisini değil, **hacmi** ölçüyoruz; 11:00'i ayrıca, kendi başına okuyoruz.

## Deney süresince DEĞİŞMEYECEKLER

- prompt şablonu, başlık biçimi, video biçimi ve süresi
- 17:00 ve 23:00 yayın saatleri
- Instagram açıklamaları ve akışı
- Üst kademeye (10M) kadar zaten donmuş olan her şey

Planlı bir değişiklik olursa tarihi buraya yazılır ve deney kaydırılır.

## Ölçüm (analiz oturumu hesaplar)

Kıyas dönemi: **4–17 Ekim** (günde 2 video). Deney dönemi: 18–31 Ekim.

1. **Toplam günlük etkileşimli izlenme** (Analytics, `engaged_daily`), deney
   dönemi ve kıyas dönemi. Kanal ivmesi sürekli değiştiği için tek başına
   sonuç sayılmaz; ±7 gün içinde yayınlanan videolar arasındaki yüzdelik ile
   birlikte okunur.
2. **17:00 ve 23:00 videolarının medyan izlenmesi**, kıyas dönemine karşı
   (sulandırma kontrolü: üçüncü video diğer ikisini inceltiyor mu?).
3. **Ölü video oranı** (48 saatte 250 altı).
4. **11:00 videoları** kendi başına: medyan, hit sayısı (n=14, yalnızca
   betimleyici, test yapılmaz).
5. Hit sayısı: 10.000+ izlenme alan video sayısı, deney ve kıyas dönemi.

Hiçbir metrik için "hacim işe yaradı" iddiası p-değeri olmadan yazılmaz. n çok
küçük: küçük etkiler ayırt edilemez, "yeterli veri yok" meşru bir sonuçtur.

## Durdurma kuralları (hemen eski tempoya dön: 17:00 ve 23:00, günde 2)

- YouTube ya da AdSense'ten **tekrarlayan / özgün olmayan içerik, kısıtlama ya
  da sarı simge** gelirse; kanal e-postasını her gün tarıyorum ve haber veririm.
- 17:00 ve 23:00 videolarının medyanı kıyas dönemine göre **%30'dan fazla**
  düşerse (7. günde ve 14. günde bakılır).
- 42 videonun **4 veya daha fazlası ölü** çıkarsa (~%10; kıyas dönemi ~%2,5).
- Üretim sürdürülemezse (operatör yetişemiyorsa): yarım bırakmak yerine eski
  tempoya dönmek tercih edilir.

## Karar (6 Kasım)

Durdurma kuralı tetiklenmediyse devam / eski tempoya dönüş kararı kanal
sahibinindir. Deney sonucu "belirsiz" ise (çoğu ihtimalle böyle olacak) tempo,
üretim kapasitesine göre seçilir; bunun maliyeti operatör zamanıdır, kanalın
verisinden kanıtlanmış bir kazanç değildir.
