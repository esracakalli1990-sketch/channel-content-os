# Hacim denemesi: günde 2 yerine 3 video

**Başlangıç:** 19 Ekim 2026, 11:00 UTC · **Bitiş:** 1 Kasım 2026, 23:00 UTC
(14 gün, 42 video) · **Final ölçüm:** 7 Kasım (kontroller 27 Ekim ve 3 Kasım;
videoların olgunlaşması ve Analytics'in 3 gün gecikmesi için) · **Karar veren:** kanal sahibi

> **Dönem güncellemesi (19 Ekim, sonuçtan önce).** 18 Ekim'de 11:00 slotu
> kaçtı: klipler 4 saatlik ön süreden sonra geldi, o gün yalnızca 2 video
> yayınlandı. Bu yüzden:
> - **Deney dönemi:** 19 Ekim 11:00 UTC – 1 Kasım 23:00 UTC (14 gün, 42 video).
> - **18 Ekim geçiş günü**, iki dönemde de yok (17:00 stingray ve 23:00
>   badger ölçüm dışı). 2 videolu bir günü deneye almak karşılaştırılan şeyi
>   karıştırır; kıyasa almak da kıyası 4–17 Ekim'in dışına taşır.
> - **Kıyas dönemi** 4–17 Ekim, değişmedi.
> - **Final ölçüm 7 Kasım:** Analytics 3 gün geriden geldiği için 1 Kasım
>   videolarının 3. gün verisi ancak o gün geliyor.
> - **Kontroller** 27 Ekim ve 3 Kasım, değişmedi. **Eşikler ve durdurma
>   kuralları** `6c84d60`'taki gibi, değişmedi.
> - **Yayın düzeni:** günde 3 video, 1 Kasım 23:00 UTC yayınına kadar sürmeli.

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

Kıyas dönemi: **4–17 Ekim** (günde 2 video). Deney dönemi: 19 Ekim 11:00 –
1 Kasım 23:00 UTC (18 Ekim geçiş günü, ölçüm dışı).
Videolar **aynı yaşta** karşılaştırılır (yayın günü + 2 ya da 3 gün); deney
videoları kıyas videolarından 14 gün gençtir, yaşı eşitlemeden kıyas yapmak
sonucu yaşa bağlar. Kontrol tarihleri: **27 Ekim** (7. gün; 24 Ekim değil,
çünkü Analytics 3 gün geriden geliyor), **3 Kasım**, **7 Kasım**
(final). Ölçüm kodu ayrı dalda (`claude/hacim-olcum`), hat koduna dokunmuyor.

1. **Toplam günlük etkileşimli izlenme** (Analytics, `engaged_daily`), deney
   dönemi ve kıyas dönemi. Kanal ivmesi sürekli değiştiği için tek başına
   sonuç sayılmaz; ±7 gün içinde yayınlanan videolar arasındaki yüzdelik ile
   birlikte okunur.
2. **17:00 ve 23:00 videolarının medyan izlenmesi**, kıyas dönemine karşı
   (sulandırma kontrolü: üçüncü video diğer ikisini inceltiyor mu?).
3. **Düşük video oranı** (aşağıdaki "Eşikler"e bak). Eski "250 altı" ölü
   ölçüsü bu rejimde ayırt edici değil.
4. **11:00 videoları** kendi başına: medyan, hit sayısı (n=14, yalnızca
   betimleyici, test yapılmaz).
5. **Hit sayısı:** birincil ölçü **50.000+** izlenme, ikincil/betimleyici
   10.000+ (yaşa göre eşitlenmiş, iki dönemde).

### Eşikler (17 Ekim'de, deney başlamadan yazıldı)

Kıyas dönemi gerçek verisine bakılınca iki eski eşik ayırt edici çıktı: 2. gün
sonunda en düşük izlenme 1.791 (yani 250 altı "ölü" oranı %0, belgenin ilk
yazdığı %2,5 tüm geçmişin rakamıydı) ve kıyas videolarının üçte ikisi (15'te 10)
10.000'i 3. gün sonunda zaten geçiyor. Bu yüzden:

- **Düşük video:** yaşa eşitlenmiş (2. gün sonu) izlenmesi **2.000'in altında**
  olan video (kıyas döneminin alt %10'una yuvarlandı). Beklenen oran ~%10.
- **Hit:** birincil **50.000+**, ikincil 10.000+ (3. gün sonu).
- Bu eşikler deney başladıktan sonra **değiştirilmez**; sonuca göre seçilmiş
  eşik sonuç sayılmaz.

Hiçbir metrik için "hacim işe yaradı" iddiası p-değeri olmadan yazılmaz. n çok
küçük: küçük etkiler ayırt edilemez, "yeterli veri yok" meşru bir sonuçtur.

## Durdurma kuralları (hemen eski tempoya dön: 17:00 ve 23:00, günde 2)

- YouTube ya da AdSense'ten **tekrarlayan / özgün olmayan içerik, kısıtlama ya
  da sarı simge** gelirse; kanal e-postasını her gün tarıyorum ve haber veririm.
- 17:00 ve 23:00 videolarının medyanı kıyas dönemine göre (aynı yaşta)
  **%30'dan fazla** düşerse (27 Ekim ve 3 Kasım'da bakılır).
- Deney videolarının **%30'undan fazlası düşük** çıkarsa (2. gün sonunda
  2.000 altı; beklenen ~%10, 42 videoda 13 ve üzeri). 250 altı mutlak çöküş
  (4 ve üzeri video) ayrıca anında durdurur.
- Üretim sürdürülemezse (operatör yetişemiyorsa): yarım bırakmak yerine eski
  tempoya dönmek tercih edilir.

## Karar (7 Kasım)

Durdurma kuralı tetiklenmediyse devam / eski tempoya dönüş kararı kanal
sahibinindir. Deney sonucu "belirsiz" ise (çoğu ihtimalle böyle olacak) tempo,
üretim kapasitesine göre seçilir; bunun maliyeti operatör zamanıdır, kanalın
verisinden kanıtlanmış bir kazanç değildir.
