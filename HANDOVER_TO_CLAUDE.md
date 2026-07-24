# Channel Content OS - Handover & Context Document

## 📌 Proje Özeti
**Channel Content OS**, YouTube için otonom belgesel/içerik üreten Python tabanlı bir "Durum Makinesi" (State Machine) otomasyonudur. Bir fikirden başlayıp sırasıyla araştırma, senaryo, seslendirme, montaj ve yayınlanma aşamalarına kadar sistemi kendi kendine yürütür.

## 🏗 Sistem Mimarisi & Klasör Yapısı
* Sistem aşamaları (States): `idea` -> `research` -> `topic_approved` -> `script` -> `script_approved` -> `production` -> `qa` -> `private_uploaded` -> `publish_approved` -> `published`.
* Sistem klasör bazlı çalışır. Her video `videos/VIDEO-XYZ` klasöründe tutulur ve bir `manifest.json` ile takip edilir.
* **Kanonik klasör isimleri `project.py` içinde sabit olarak tanımlıdır ve TEK DOĞRU KAYNAKTIR:** `TOPIC_DIR`(01) … `ANALYTICS_DIR`(10). Kodun hiçbir yerinde `"05_visuals"` gibi elle string yazılmamalıdır — bu sabitler import edilmelidir. (Bu kural, `auto_produce.py`'nin kendi başına `05_audio`/`06_visuals`/`08_assembly` diye paralel klasörler uydurmuş olması yüzünden konuldu.)
  * Üretim çıktılarının yeri: ses+altyazı -> `06_voice`, stock görseller -> `05_visuals`, ara render'lar (raw/merged) -> `07_edit`, teslim edilen `final.mp4` -> `08_export`.
* Çekirdek modüller (`src/channel_ops/`):
  * `cli.py`: Komut satırı arayüzü (örnek: `python -m channel_ops dashboard`).
  * `project.py`: Durum makinesini işleten ve klasörleri oluşturan yapı.
  * `tts_engine.py`: **Edge-TTS** kullanarak metni sese (.mp3) ve altyazıya (.srt) çevirir.
  * `video_assembler.py`: **FFmpeg** kullanarak görselleri, sesi ve altyazıyı birleştirip videoyu (`.mp4`) oluşturur.
  * `providers/gemini_provider.py`: **Gemini API** ile LLM üretimlerini (araştırma, senaryo) gerçekleştirir.

## ⚙️ Mevcut Durum (Nerede Kaldık?)
1. Projenin ana iskeleti %100 tamamlandı.
2. `VIDEO-001` (McDonald's Belgeseli) başarıyla baştan sona çalıştırıldı.
3. Kök dizinde (root) otonom tüm adımları onay almadan arka arkaya çalıştıran **`auto_produce.py`** isimli bir otomasyon betiği oluşturduk. 
4. `auto_produce.py` yerel makinede çalıştırıldı ve başarıyla `08_assembly/final.mp4` dosyasını üretti. Sistem uçtan uca çalışır durumda.
5. **[TAMAMLANDI] Pexels stock video entegrasyonu.** Video artık sabit siyah arka plan yerine konuya uygun gerçek stock görüntülerle üretiliyor. Zincir: `visual_planner.plan_visual_queries()` (senaryodan Gemini ile İngilizce arama sorguları çıkarır) -> `stock_media.fetch_clips()` (Pexels'ten arar + `06_visuals/`e indirir + `asset_manifest.csv`e lisans kaydı düşer) -> `video_assembler.concat_video_clips()` (klipleri normalize edip tek arka plan reel'ine birleştirir). Herhangi bir adım başarısız olursa sistem otomatik olarak eski siyah arka plana (fallback) döner, pipeline kırılmaz.
   * Doğrulandı: `VIDEO-001` yeniden render edildi -> 1280x720, 80.1 sn, 6 klip, konuyla alakalı gerçek görüntüler (McDonald's binası, emlak ofisi vb.).

## 🚨 Önemli Kurallar ve Kısıtlamalar (Strict Rules)
1. **ASLA UYDURMA (Zero Hallucination):** Senaryo (Script) üretilirken sistemin kendi kendine bilgi uydurması KESİNLİKLE YASAKTIR. Sadece `research` aşamasında toplanan ve sisteme girilen `Source Ledger` (Kaynak Defteri) içerisindeki bilgiler kullanılmalıdır. (Bu sebeple 2 satır kaynak verildiğinde yapay zeka 8 dakikalık video uydurmayı reddedip 3 dakikalık video yazmıştır).
2. **Gemini API Model Seçimi:** Ücretsiz katman (Free Tier) API kullanıldığı için `gemini-2.0-flash` limitlere (limit:0) takılmaktadır. Bu projede **varsayılan model her zaman `gemini-2.5-flash` olmalıdır.** (Bu `ai.json` ve `gemini_provider.py` içinde zaten ayarlandı, değiştirilmemelidir).
3. **Otonomluk:** Kullanıcının özel bir müdahalesi olmadığı sürece AI ajanları izin istemeden (onayları `approval_note` parametresiyle otonom vererek) işlem yapmalıdır. (`auto_produce.py` bu şekilde tasarlandı).

## 🛠 Son Çözülen Bug'lar ve Bilinmesi Gereken Teknik Detaylar
* **Edge-TTS Altyazı Boşluğu (0 Byte Bug) — ÇÖZÜLDÜ.** Eski teşhis ("bazı Neural sesler WordBoundary desteklemiyor") **yanlıştı**. Gerçek sebep: edge-tts varsayılan olarak `SentenceBoundary` olayları gönderiyor, `WordBoundary` göndermiyor (`Communicate(boundary=...)` varsayılanı). Kod ise yalnızca `WordBoundary` dinleyip diğerini sessizce çöpe atıyordu, bu yüzden `SubMaker` boş kalıyor ve SRT 0 byte çıkıyordu. Test edildi: her ses aynı davranıyor, ses seçimiyle ilgisi yok.
  * Düzeltme `tts_engine.py` içinde: artık **her iki** sınır tipi de `submaker.feed()` edilir. Cümle bazlı altyazı belgesel için zaten daha okunaklıdır (kelime kelime yanıp sönmez).
  * Ayrıca `_fix_overlapping_cues()` eklendi: edge-tts cümle sınırları bazen ~50 ms çakışıyor ve ekranda iki altyazı birden görünüyordu; her altyazının bitişi bir sonrakinin başlangıcına kırpılır.
  * `auto_produce.py`'deki "SRT boşsa altyazıyı atla" fallback'i **korundu** (güvenlik ağı olarak), ama artık normal akışta devreye girmiyor.
* **YANLIŞ DOSYA SESLENDİRME BUG'I — ÇÖZÜLDÜ (kritik).** `auto_produce.py` senaryoyu `glob("*.md")[0]` ile seçiyordu; bu alfabetik olarak ilk dosyayı, yani **prompt dosyasını** getiriyordu. Sonuç: üretilen tüm videolar senaryo yerine AI'ya gönderilen istemi ("You are the research and editorial assistant...") seslendiriyordu. Bu bug, altyazılar düzelene kadar görünmezdi çünkü kimse sesi metne dökmüyordu.
  * Düzeltme: `find_latest_script()` fonksiyonu `_prompt.md` dosyalarını eler ve en güncel taslağı seçer.
  * `clean_script_text()` de yeniden yazıldı: künye satırları (`Video ID:`, `Topic:`), zaman damgalı bölüm başlıkları (`(0:00 - 1:30) Hook`), anlatıcı etiketleri (`(Narrator)`), `---` ayıraçları, `[VISUAL: ...]` yönergeleri ve AI'nin açılış cümlesi artık seslendirilmiyor.
  * Etki: video süresi 80 saniyeden **7 dakikaya** çıktı (gerçek senaryo artık okunuyor).
* **Pexels SSL Hatası (CERTIFICATE_VERIFY_FAILED):** Windows'taki Python kurulumu güvenilir sertifika deposunu her zaman doldurmuyor. `stock_media.py` içindeki `_ssl_context()` fonksiyonu `certifi` paketinin CA listesini kullanacak şekilde ayarlandı. Bu fonksiyon kaldırılmamalıdır.
* **Pexels 403 / Cloudflare error 1010:** Pexels Cloudflare arkasında ve urllib'in varsayılan `Python-urllib/3.x` User-Agent'ını bloklar. `stock_media.USER_AGENT` sabiti (tarayıcı UA'sı) hem API hem CDN indirme isteklerinde **zorunludur**, silinmemelidir.
* **FFmpeg RAM & Path Hataları:** Windows üzerinde FFmpeg'in `concat` veya ağır `zoompan` filtreleri bazen hata verebiliyor. Ayrıca Windows terminalindeki RAM kısıtlamalarına takılmamak adına FFmpeg parametreleri şu anlık `-vf scale=1280:720` ve `-preset ultrafast` olacak şekilde optimize edildi.

## 🚀 Sonraki Adımlar (Claude İçin Görev Listesi)
Kullanıcı projeyi inceledikten sonra devam etmek isteyecektir. Muhtemel bir sonraki aşamalar:
1. ~~Pexels/Pixabay stock video entegrasyonu.~~ **TAMAMLANDI** (yukarıdaki madde 5'e bakınız).
2. ~~Boş çıkan SRT altyazı sorunu.~~ **ÇÖZÜLDÜ** (Whisper'a gerek kalmadı — kök neden yanlış olay tipi dinlemekti, yukarıdaki teknik detaylara bakınız). Son render: 62 altyazı, videoya gömülü ve doğrulandı.
3. **[SIRADAKİ]** Videoları manuel değil, Google YouTube Data API ile direkt `private` olarak kanala yükleyecek modülün aktif edilmesi.
4. ~~Klasör isimlendirme tutarsızlığı.~~ **ÇÖZÜLDÜ:** Kanonik isimler `project.py`'de sabitlendi, tüm modüller (`auto_produce.py` dahil) bu sabitleri kullanacak şekilde güncellendi, `VIDEO-001`'in mevcut dosyaları yeni yerlerine taşındı ve uydurulan 3 klasör silindi. Tam pipeline yeniden çalıştırılarak doğrulandı.
5. ~~Görsel–anlatım hizalaması.~~ **ÇÖZÜLDÜ.** Görseller artık anlatımla hizalı:
   * `visual_planner.parse_srt()` + `build_scenes()` altyazı zamanlamalarından ~13 sn'lik sahneler çıkarır (sahne sınırları hep cümle bitiminde, görsel cümle ortasında değişmez).
   * `plan_scene_queries()` tek bir AI çağrısıyla her sahneye kendi arama sorgusunu atar.
   * `stock_media.fetch_clips_map()` sorgu->klip haritası döndürür. **Önemli:** indirme sınırını aşan durumda sorgular zaman çizgisine EŞİT DAĞITILARAK seçilir; ilk N alınırsa videonun ikinci yarısı hiç eşleşmiyordu (bu bug yaşandı ve düzeltildi).
   * `video_assembler.build_timed_reel()` her klibi kendi sahnesinin süresine tam oturtur (kısa klip döngüye alınır, uzun klip kesilir).
   * Sonuç: 36 sn'lik reel'in 11.7 kez tekrarlaması yerine, 420 sn'lik anlatıma birebir oturan 32 sahnelik reel.
   * `visual_planner.assign_clips_to_scenes()` sahneleri kliplere eşler. Kendi sorgusu indirilmiş sahne o klibi alır; kalanlar için **semantik eşleştirme** yapılır: sahnenin sorgusu + anlatımından çıkarılan anahtar kelimeler ile kliplerin indirilme sorguları arasında Jaccard kelime örtüşmesi hesaplanır. Çok kullanılan klibe puan cezası (`reuse_penalty`) uygulanır ve ard arda aynı klip asla gelmez.
   * Ölçüldü: eski round-robin yönteme göre ortalama benzerlik **2.7 kat** arttı (0.0317 -> 0.0863, aynı 32 sahne ve gerçek AI sorgularıyla).
   * **Bilinen sınır:** `max_downloads=16` olduğu için ~32 sahnenin yarısı kendi görseliyle birebir eşleşir, kalanı semantik olarak en yakın klibi alır. Oran her çalıştırmada ekrana yazılır.
6. `auto_produce.py` her çalıştırmada `05_visuals` içindeki eski `stock_*.mp4` dosyalarını siler; aksi halde klasör her çalıştırmada yüzlerce MB büyüyordu (3 çalıştırmada 668 MB'a ulaşmıştı).
7. Altyazı okunabilirliği **ÇÖZÜLDÜ**: Cümle bazlı altyazıların yarısı 120+ karakterdi (en uzunu 266) ve ekranın yarısını kaplayan 5 satırlık duvarlar oluşturuyordu. `tts_engine._split_long_cues()` uzun altyazıları kelime sınırlarından bölüp süreyi orantılı paylaştırır (max 90 karakter). Ayrıca `clean_script_text()` artık `[C-001]` silindikten sonra kalan boşluklu noktalamayı (`"locations ."`) temizliyor — bu hem ekranda yanlış görünüyor hem de TTS'in 0.06 sn'lik tek nokta altyazısı üretmesine yol açıyordu.

---
**Claude'a Not:** Bu belgeyi aldığında mevcut sistemin kusursuzca çalıştığını varsay ve kullanıcının senden isteyeceği bir sonraki geliştirme veya optimizasyon adımından direkt devam et. Yeni modül eklerken projedeki `src/channel_ops/` dizini mimarisine sadık kal.
