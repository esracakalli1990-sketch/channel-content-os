# Kurulum Rehberi — Mekanik Dönüşüm Shorts Kanalı

Bu belge **senin yapacağın hesap/erişim işlerini** anlatır. Kod tarafını Claude hallediyor.
Her adımı bitirdikçe kutucuğu işaretle.

> **🔒 EN ÖNEMLİ KURAL:** Bu rehberdeki hiçbir anahtarı, token'ı, şifreyi **sohbete yapıştırma.**
> Hepsi doğrudan GitHub Secrets'a girilecek. Claude'un onları görmesine gerek yok.

---

## 0. Önce Karar: Yeni Kanal Açılmalı

Mevcut `config/channel.json` içindeki kanal **"Decoded"** — 8-12 dakikalık İngilizce iş/teknoloji
belgeseli kanalı. Mekanik dönüşüm Shorts'ları **tamamen farklı bir format**: sözsüz, 8 saniyelik,
görsel.

**Bu ikisini aynı kanalda birleştirme.** YouTube algoritması kanalı bir konuya oturtmaya çalışır;
karışık format hem izleyiciyi hem algoritmayı şaşırtır, ikisi de zarar görür.

➡️ **Karar: Shorts için ayrı, yeni bir kanal.** "Decoded" olduğu gibi dursun.

---

## 1. Kanal İsmi Seçimi

İçerik sözsüz ve global — İngilizce isim her pazarda çalışır.

**Öneriler:**

| İsim | Handle | Neden |
|---|---|---|
| **Unfoldables** | `@unfoldables` | Kısa, markalaşır, tam olarak işi anlatıyor. Birinci tercihim. |
| **Clockwork Creatures** | `@clockworkcreatures` | Çağrışımı güçlü, steampunk hissi veriyor |
| **Press & Unfold** | `@pressandunfold` | Videodaki eylemi birebir anlatıyor |
| **Tiny Automata** | `@tinyautomata` | "Automata" bu sanatın gerçek adı, niş ama doğru |

### ⚠️ Kritik: Handle'ı üç platformda AYNI ANDA kontrol et

Bir isme karar vermeden önce **YouTube + Instagram + TikTok üçünde de** aynı handle'ın boş
olduğundan emin ol. Üçünde de aynı olması marka açısından çok önemli. Biri doluysa o ismi ele,
listedeki bir sonrakine geç.

- [ ] İsim seçildi: `________________`
- [ ] Handle üç platformda da müsait: `@________________`

---

## 2. Ortak Görsel Malzemeler (üç platformda da aynı kullanılacak)

- [ ] **Profil fotoğrafı** — 800×800 px, PNG. (Öneri: siyah zemin üzerinde altın/pirinç bir dişli
      veya kapalı haldeki oyuncak objenin fotoğrafı. Flow'da ürettiğin karelerden biri iş görür.)
- [ ] **YouTube banner** — 2048×1152 px
- [ ] **Bio metni** (üçünde de aynı, İngilizce):
      > *Impossible machines that unfold. One button. One transformation. New short every day.*

---

## 3. YouTube Kurulumu

### 3.1 Kanalı aç
- [ ] youtube.com → sağ üst profil → **Ayarlar → Yeni kanal oluştur** (Marka Hesabı olarak aç,
      kişisel hesap değil — sonradan yönetici eklemek ve devretmek için şart)
- [ ] Kanal adını ve `@handle`'ı ayarla
- [ ] Profil fotoğrafı + banner + açıklama yükle
- [ ] **Telefon doğrulaması yap** (Ayarlar → Kanal → Özellik uygunluğu). Özel kapak fotoğrafı ve
      15 dk üstü video için gerekli, şimdiden hallet.

### 3.2 ⚠️ "Çocuklara Yönelik" ayarı — buraya çok dikkat
YouTube kanal kurulumunda "İçeriğin çocuklara yönelik mi?" diye soracak.

**Cevap: HAYIR.** İçerik *oyuncak* içeriyor diye "evet" dersen:
- Yorumlar kapanır
- Bildirimler çalışmaz
- Kişiselleştirilmiş reklam gitmez → **gelir çöker**

Bu içerik genel izleyiciye yönelik, çocuklara özel değil.

- [ ] Kanal ayarında **"Hayır, çocuklara yönelik değil"** seçildi

### 3.3 API erişimi (Claude'un yükleme yapabilmesi için)
- [ ] [console.cloud.google.com](https://console.cloud.google.com) → yeni proje oluştur
      (isim: `channel-content-os`)
- [ ] **APIs & Services → Library** → "YouTube Data API v3" → **Enable**
- [ ] **OAuth consent screen** → User Type: **External** → uygulama adını gir, kendi mailini
      destek maili olarak ekle
- [ ] **Scopes** ekle: `https://www.googleapis.com/auth/youtube.upload` ve
      `https://www.googleapis.com/auth/youtube`
- [ ] **Credentials → Create Credentials → OAuth client ID → Desktop app** →
      `client_secret.json` dosyasını indir

### 3.4 ⚠️⚠️ EN KRİTİK ADIM — bunu atlarsan sistem 7 günde ölür

OAuth consent screen'de uygulamanın durumu varsayılan olarak **"Testing"** gelir.
**Testing modunda Google'ın verdiği refresh token 7 gün sonra geçersiz olur.** Yani sistem bir
hafta çalışır, sonra sessizce durur.

**Çözüm:** OAuth consent screen sayfasında **"PUBLISH APP" / "Uygulamayı yayınla"** düğmesine bas,
durumu **"In production"** yap.

Giriş yaparken "Google bu uygulamayı doğrulamadı" uyarısı çıkacak — **normal**, kendi uygulaman.
"Gelişmiş → Devam et" diyip geçersin. Doğrulama başvurusuna gerek yok, sadece sen kullanacaksın.

- [ ] OAuth consent screen durumu **"In production"** yapıldı

### 3.5 Kota bilgisi (bilgi amaçlı, yapman gereken bir şey yok)
Günlük ücretsiz kota 10.000 birim, bir yükleme 1.600 birim → **günde ~6 video**.
Günde 1 video hedefimiz için fazlasıyla yeterli.

---

## 4. Instagram Kurulumu

### 4.1 Hesap
- [ ] Yeni Instagram hesabı aç, handle aynı olsun
- [ ] **Ayarlar → Hesap türü → Profesyonel hesaba geç → İşletme (Business)**
      *(Creator da çalışıyor ama API'nin belgelenmiş yolu Business, garantiye alalım)*
- [ ] Profil fotoğrafı + bio ekle

### 4.2 Facebook sayfası (API için zorunlu)
Instagram'ın yayınlama API'si, hesabın bir Facebook sayfasına bağlı olmasını **şart koşuyor**.
- [ ] Yeni bir Facebook **Sayfası** oluştur (aynı isim)
- [ ] Instagram → Ayarlar → **Sayfa bağla** → oluşturduğun sayfayı bağla

### 4.3 Meta geliştirici uygulaması
- [ ] [developers.facebook.com](https://developers.facebook.com) → **My Apps → Create App**
- [ ] Uygulama tipi: **Business**
- [ ] Ürün ekle: **Instagram Graph API** (veya "Instagram API setup with Facebook Login")
- [ ] Sayfanı ve Instagram hesabını uygulamaya bağla
- [ ] Şu değerleri bir kenara not et (sohbete değil!): **App ID**, **App Secret**,
      **Instagram Business Account ID**, **Page ID**

### 4.4 ⚠️ Token ömrü
Meta'nın verdiği uzun ömürlü token **60 günde bir yenilenmeli**. Claude bunu otomatik yenileyen
kodu yazacak, ama ilk token'ı senin üretmen gerekiyor. O adıma geldiğimizde birlikte yaparız.

---

## 5. TikTok Kurulumu

### 5.1 Hesap
- [ ] TikTok hesabı aç, handle aynı olsun
- [ ] Profil fotoğrafı + bio ekle

### 5.2 API — beklentiyi baştan netleştirelim
TikTok'un yayınlama API'si (Content Posting API) geliştirici başvurusu ve inceleme istiyor.
**İnceleme onaylanmadan API ile atılan videolar sadece "taslak" olarak** hesabına düşer,
otomatik yayınlanmaz.

**Planımız:**
1. **Şimdi:** Video TikTok'a taslak olarak gider, sen uygulamadan tek tık yayınlarsın.
   (Zaten videoyu Flow'da elinle üretiyorsun, akışta zaten varsın — büyük yük değil.)
2. **Paralelde:** [developers.tiktok.com](https://developers.tiktok.com) üzerinden başvuruyu
   yaparsın, onay gelirse tam otomatiğe çeviririz.

- [ ] TikTok hesabı açıldı
- [ ] (Opsiyonel, acelesi yok) Geliştirici başvurusu yapıldı

---

## 6. Telegram Botu

Sistemin seninle konuştuğu yer burası.

- [ ] Telegram'da **@BotFather**'ı aç
- [ ] `/newbot` yaz → bota bir isim ve kullanıcı adı ver
- [ ] BotFather sana bir **token** verecek → not et (sohbete yapıştırma!)
- [ ] **Kendi botuna bir mesaj at** ("merhaba" yeter) — bu şart, yoksa bot sana yazamaz
- [ ] Claude sana `chat_id`'yi nasıl bulacağını söyleyecek (tek komut, kolay)

---

## 7. GitHub Secrets — hepsi buraya girilecek

Repo → **Settings → Secrets and variables → Actions → New repository secret**

| Secret adı | Nereden geliyor |
|---|---|
| `TELEGRAM_BOT_TOKEN` | BotFather (adım 6) |
| `TELEGRAM_CHAT_ID` | Claude bulmanı sağlayacak |
| `GEMINI_API_KEY` | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| `YOUTUBE_CLIENT_ID` | `client_secret.json` içinden |
| `YOUTUBE_CLIENT_SECRET` | `client_secret.json` içinden |
| `YOUTUBE_REFRESH_TOKEN` | Claude'un yazacağı tek seferlik komutla üretilecek |
| `IG_USER_ID` | Meta uygulaması (adım 4.3) |
| `IG_ACCESS_TOKEN` | Meta uygulaması (adım 4.3) |
| `PROMPT_TEMPLATE` | Claude verecek — prompt formülü repoda açıkta durmasın diye |

> Repo public olduğu için prompt şablonunu koda değil Secret'a koyuyoruz.
> Secrets public repoda bile gizlidir, kimse göremez.

---

## 8. Sıralama Önerisi

Hepsini bir günde yapman gerekmiyor. Öncelik sırası:

1. **İsim + handle kontrolü** (her şey buna bağlı)
2. **YouTube kanalı + API + `In production` ayarı** ← Claude'un ilk ihtiyacı bu
3. **Telegram botu** ← ikinci ihtiyaç
4. **Instagram + Facebook sayfası** (biraz sonra lazım olacak)
5. **TikTok** (en son, acelesi yok)

İlk ikisi biterse sistem yayına girebilir; Instagram ve TikTok üstüne eklenir.

---

## Takıldığın Yerde

Adım adım anlatırım — hangi ekranda kaldığını yaz yeter.
**Ama anahtarları asla sohbete yapıştırma**, sadece "şu ekranda şunu göremiyorum" de.
