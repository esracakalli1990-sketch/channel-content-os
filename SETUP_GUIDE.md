# Kurulum Rehberi — Unfoldables (Mobil)

Bu belge **senin telefonda yapacağın işleri** anlatır. Kod tarafını Claude hallediyor.
Hiç yapmamış gibi, adım adım yazıldı. Bitirdiğin kutucuğu işaretle.

> **🔒 EN ÖNEMLİ KURAL:** Buradaki hiçbir anahtarı/token'ı **sohbete yapıştırma.**
> Hepsi doğrudan GitHub Secrets'a girilecek. Claude'un görmesine gerek yok.

---

## 0. Mobil Hazırlık — bunu atlarsan takılırsın

### 0.1 Hesap karışmasını önle
Bu proje için **yeni bir mail** kullanıyorsun. Telefonda eski Google hesabın da açıksa Google
işlemleri sessizce yanlış hesapta yapar; API'yi bir hesapta açarsın, kanal öbür hesapta kalır.

- [ ] Chrome'da **gizli sekme** aç (sağ üst ⋮ → *Yeni gizli sekme*) ve **sadece yeni mail** ile gir
- [ ] Tüm Google/GitHub işlerini bu gizli sekmede yap

### 0.2 "Masaüstü sitesi" modunu aç
Google Cloud Console ve GitHub Ayarları mobil görünümde menülerin yarısını gizler.

- [ ] Chrome'da ⋮ → **Masaüstü sitesi** kutusunu işaretle
- [ ] Yazılar küçülecek, iki parmakla yakınlaştırarak kullan — normal

Bu iki ayar açıkken devam et.

---

## 1. Kanal Kimliği — ✅ KESİNLEŞTİ

| | Değer |
|---|---|
| **Kanal adı (görünen ad)** | `Unfoldables` |
| **Handle (üç platformda da)** | `@unfoldableslab` |

- [x] YouTube / Instagram / TikTok hesapları açıldı

> ⚠️ **Kontrol et:** YouTube kanalı **yeni mail** ile mi açıldı? Eski mailde kaldıysa yükleme
> yanlış kanala gider. YouTube uygulaması → profil → hesabın mailini doğrula.

---

## 2. Telegram Botu (~3 dakika, en kolayı)

Eski `@kanal3kontrol_bot` yerine yeni bot açıyoruz: bir bot token'ını aynı anda tek bir sistem
dinleyebilir, ileride iki sistem birbirinin mesajlarını çalmasın.

- [ ] Telegram → arama → **@BotFather** → *Başlat*
- [ ] `/newbot` yaz
- [ ] Sorduğu isim: `Unfoldables Lab`
- [ ] Sorduğu kullanıcı adı: `unfoldableslab_bot` *(dolu derse sonuna `1` ekle)*
- [ ] BotFather uzun bir **token** verecek → kopyala, birazdan Secrets'a koyacaksın
- [ ] **Yeni botunu aç ve `/start` yaz** ← bunu atlama, bot sana yazamaz

---

## 3. Gemini API Anahtarı (~2 dakika)

Prompt ve açıklama üretimi için. **Yeni mail ile.**

- [ ] Tarayıcıda [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
- [ ] Yeni mail ile giriş yap
- [ ] **Create API key** → proje seçmeni isterse yenisini oluşturmasına izin ver
- [ ] Çıkan anahtarı kopyala

---

## 4. Google Cloud — YouTube Yükleme İzni (~15 dakika, en uzunu)

Video yüklemek için **API Key yetmez**, OAuth gerekir. (API Key sadece herkese açık veriyi
*okur*; kendi kanalına *yazmak* OAuth ister.)

### 4.1 Proje aç
- [ ] [console.cloud.google.com](https://console.cloud.google.com) (gizli sekme + masaüstü modu)
- [ ] Şartları kabul et
- [ ] Üstteki proje seçiciye dokun → **New Project**
- [ ] İsim: `unfoldables-lab` → **Create**
- [ ] Oluşunca **o projenin seçili olduğundan emin ol** (üstte adı yazmalı)

### 4.2 YouTube API'sini aç
- [ ] Üstteki arama çubuğuna `YouTube Data API v3` yaz
- [ ] Çıkan sonuca gir → **Enable** / **Etkinleştir**

### 4.3 İzin ekranı (OAuth consent screen)
- [ ] Sol menü → **APIs & Services → OAuth consent screen**
      *(yeni arayüzde adı **Google Auth Platform** olabilir → **Get started**)*
- [ ] App name: `Unfoldables Lab`
- [ ] User support email: yeni mailin
- [ ] Audience / User type: **External**
- [ ] Developer contact: yeni mailin
- [ ] Kaydet

### 4.4 ⚠️⚠️ EN KRİTİK ADIM — atlarsan sistem 7 günde susar
İzin ekranının durumu varsayılan olarak **"Testing"** gelir. Bu moddaki refresh token'ı Google
**7 gün sonra iptal eder.** Sistem bir hafta çalışır, sonra hata bile vermeden durur.

- [ ] Aynı sayfada **PUBLISH APP** / *Uygulamayı yayınla* → onayla
- [ ] Durum **"In production"** yazmalı

Sonra izin ekranında "Google bu uygulamayı doğrulamadı" uyarısı çıkacak — **normal.** Kendi
uygulaman, kendi kanalın. *Gelişmiş → (uygulama adı)'na git* deyip geçeceksin. Doğrulama
başvurusu yapmana gerek yok.

### 4.5 OAuth istemcisi oluştur — mobil için "Web application"
> Masaüstünde "Desktop app" seçilir ama o yöntem bilgisayarda yerel sunucu ister.
> Telefonda **Web application** seçiyoruz, böylece her şey tarayıcıdan yürüyor.

- [ ] Sol menü → **Credentials** → **Create Credentials** → **OAuth client ID**
- [ ] Application type: **Web application**
- [ ] Name: `unfoldables-uploader`
- [ ] **Authorized redirect URIs** → *ADD URI* → tam olarak şunu yapıştır:
      ```
      https://developers.google.com/oauthplayground
      ```
- [ ] **Create**
- [ ] **Client ID**'yi kopyala

### 4.6 Client Secret'ı bulma — Google artık ekranda göstermiyor

Oluşturma ekranında Client ID görünür ama secret yerine **JSON dosyası indir** der. Secret o
dosyanın içinde. İki yol var:

**Yol 1 — dosya indirmeden (önce bunu dene):**
- [ ] Sol menü → **Credentials** → listede `unfoldables-uploader` adına dokun
- [ ] Detay sayfasında **"Additional information"** / **"Client secret"** bölümündeki
      kopyala simgesine bas

**Yol 2 — JSON'u telefonda açmak (secret gizliyse):**

`.json` dosyasına dokununca telefon çoğu zaman "açacak uygulama yok" der. Uzantıyı değiştir:

- **Android:** Dosyalar → İndirilenler → dosyaya **uzun bas** → *Yeniden adlandır* →
  sonundaki `.json` yerine `.txt` yaz → kaydet → dosyaya dokun, metin olarak açılır
- **iPhone:** Dosyalar → İndirilenler → dosyaya dokun, doğrudan metin olarak açılır

Açılan metin tek satır ve karışık görünecek, normal. İçinde şunu ara:

```
"client_secret":"GOCSPX-xxxxxxxxxxxxxxxxxxxx"
```

**`GOCSPX-` ile başlayan** değer senin secret'ın (tırnaklar dahil değil). Aynı dosyada
`"client_id"` de var, onu kaybettiysen oradan alabilirsin.

- [ ] Client Secret kopyalandı
- [ ] ⚠️ **JSON dosyasını telefondan sil** — kanalına erişim veren bir kimlik dosyası,
      İndirilenler klasöründe durmasın. Secrets'a girdikten sonra gerekmiyor.

---

## 5. Refresh Token Üretimi (~5 dakika, tamamen tarayıcıdan)

Google refresh token'ı hazır vermiyor, bir kez izin verip üreteceksin.

- [ ] [developers.google.com/oauthplayground](https://developers.google.com/oauthplayground)
- [ ] Sağ üstteki **⚙️ dişli** simgesine dokun
- [ ] **Use your own OAuth credentials** kutusunu işaretle
- [ ] Az önceki **Client ID** ve **Client Secret**'ı yapıştır
- [ ] Sol taraftaki **Step 1** kutusunda, en alttaki boş alana bu iki satırı (aralarında boşlukla) yaz:
      ```
      https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube
      ```
- [ ] **Authorize APIs** → yeni mail ile giriş → (doğrulama uyarısını geç) → **İzin ver**
- [ ] **Step 2** → **Exchange authorization code for tokens**
- [ ] Çıkan **Refresh token** değerini kopyala ← Secrets'a gidecek olan bu

> **Refresh token görünmüyorsa:** ⚙️ dişliden *Force prompt for consent* kutusunu işaretleyip
> 4. adımdan itibaren tekrarla.

---

## 6. GitHub Secrets — hepsini buraya gir

GitHub mobil **uygulaması** Secrets ekranını göstermez → **tarayıcıdan, masaüstü modunda** gir.

- [ ] `github.com/esracakalli1990-sketch/channel-content-os`
- [ ] **Settings** → sol menü **Secrets and variables → Actions**
- [ ] Her biri için **New repository secret** → isim + değer → *Add secret*

| Secret adı | Değer nereden |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Adım 2 — BotFather |
| `TELEGRAM_CHAT_ID` | Claude bulduracak (bota mesaj attıktan sonra) |
| `GEMINI_API_KEY` | Adım 3 |
| `YOUTUBE_CLIENT_ID` | Adım 4.5 |
| `YOUTUBE_CLIENT_SECRET` | Adım 4.5 |
| `YOUTUBE_REFRESH_TOKEN` | Adım 5 |
| `PROMPT_TEMPLATE` | Claude verecek (repo public, formül açıkta durmasın) |

---

## 7. YouTube Kanal Ayarı — gelirini etkiler

YouTube "İçeriğin çocuklara yönelik mi?" diye soracak. İçerikte oyuncak var diye **"evet"
dersen** yorumlar kapanır, bildirim gitmez, kişiselleştirilmiş reklam kalkar → **gelir çöker.**
Bu içerik genel izleyiciye yönelik, çocuklara özel değil.

- [ ] YouTube Studio → **Ayarlar → Kanal → Gelişmiş ayarlar**
- [ ] **"Hayır, çocuklara yönelik değil"** seçili

---

## 8. Instagram (sonra — acelesi yok)

- [ ] Instagram → **Ayarlar → Hesap türü → Profesyonel → İşletme (Business)**
- [ ] Facebook **Sayfası** oluştur (aynı isim) ve Instagram'a bağla
- [ ] [developers.facebook.com](https://developers.facebook.com) → **Create App** → tip: **Business**
- [ ] Ürün ekle: **Instagram Graph API**
- [ ] Not et: **App ID**, **App Secret**, **Instagram Business Account ID**, **Page ID**

> Meta'nın token'ı 60 günde bir yenilenmeli — Claude otomatik yenileyen kodu yazacak.

---

## 9. TikTok — elle paylaşım (karar verildi)

TikTok'un yayınlama API'si geliştirici incelemesinden geçmeden **herkese açık paylaşım
yapamıyor**; onay gelene kadar gönderilen videolar hesaba private düşüyor ve yine elle
yayınlanması gerekiyor. Yani entegrasyon, onay çıkmadan elle paylaşımdan bir kazanç sağlamıyor.

**Bu yüzden TikTok için API kurmuyoruz.** Bunun yerine sistem, her videoda Telegram'a
**ikinci bir mesaj** gönderiyor:

```
🎵 TikTok açıklaması — kopyala, videoyu elle yükle

One button and it becomes a mechanical moth. #moth #automaton #satisfying …
```

Açıklama kopyalanabilir blok halinde, içinde sadece metin var. Video da zaten aynı sohbette —
Flow'da üretip bota gönderdiğin dosya. Yani TikTok paylaşımı yaklaşık 30 saniye:

- [ ] Telegram'daki videoyu kaydet
- [ ] TikTok'a yükle
- [ ] Açıklama mesajına dokunup kopyala, yapıştır

> İleride TikTok onayı almak istersen [developers.tiktok.com](https://developers.tiktok.com)
> üzerinden başvurulur. Onay çıkarsa entegrasyon eklenebilir; çıkmazsa bu akış zaten çalışıyor.

---

## 10. Hangi Sırayla?

Hepsi bir günde bitmek zorunda değil. Sistemi ayağa kaldıran sıra:

1. **Telegram botu** (adım 2) — 3 dk
2. **Gemini anahtarı** (adım 3) — 2 dk
3. **Google Cloud + OAuth + refresh token** (adım 4-5) — 20 dk
4. **Secrets'a giriş** (adım 6) — 5 dk

**Bu dördü bitince sistem yayına girebilir.** Instagram (8) sonradan eklenir, beklemeye gerek
yok. TikTok (9) için kurulum gerekmiyor — açıklama Telegram'a geliyor, paylaşımı elle yapıyorsun.

---

## Takıldığında

Hangi ekranda kaldığını yaz, adım adım anlatırım.
**Anahtarları yapıştırma** — sadece "şu ekranda şu düğmeyi göremiyorum" demen yeterli.
