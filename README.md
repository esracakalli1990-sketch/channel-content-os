# Channel Content OS

İngilizce Business + Technology Documentary YouTube kanalı için **otonom içerik üretim ve yayın sistemi**.

Sistem, konu seçiminden video yayınına kadar tüm süreci yönetir. Kritik kalite kararlarında insan onayı gerekir; rutin işler otomatiktir.

## Hızlı Başlangıç

```powershell
# 1. Projeyi kur
python -m pip install -e ".[all,dev]"

# 2. .env dosyasını oluştur
copy .env.example .env
# → Gemini API key'ini ekle: https://aistudio.google.com/apikey

# 3. Durum kontrolü
channel-os status

# 4. Yeni video başlat
channel-os init-video VIDEO-002 --topic "How NVIDIA Accidentally Built the AI Economy"
```

## Mimari

```
Konu Tarama → Skorlama → [İnsan Onayı] → Araştırma → Senaryo →
[İnsan Onayı] → Ses + Görsel → Montaj → Kalite Kontrol →
YouTube Private Upload → [İnsan Onayı] → Yayın → Analitik
```

## Modüller

| Modül | Görev |
|-------|-------|
| `config_loader` | channel.json + ai.json yükler |
| `workflow` | Durum makinesi (idea → published) |
| `scoring` | 100 puanlık konu değerlendirme |
| `production_ai` | AI ile senaryo, araştırma, storyboard |
| `providers/` | Gemini (ücretsiz), OpenAI, Ollama |
| `tts_engine` | Edge-TTS ile seslendirme |
| `stock_media` | Pexels API ile stok görsel/video |
| `video_assembler` | FFmpeg ile video montaj |
| `youtube_uploader` | Private YouTube yükleme |
| `youtube_analytics` | Performans raporları |
| `notifications` | Telegram bildirimleri |

## Durum Akışı

```
idea → research → topic_approved → script → script_approved →
production → qa → private_uploaded → publish_approved → published
```

- `topic_approved`, `script_approved`, `publish_approved`, `published` → **insan onayı** zorunlu
- Geri dönüş (revizyon) desteklenir

## AI Provider'lar

| Provider | Maliyet | Varsayılan Model |
|----------|---------|-----------------|
| **Gemini** (varsayılan) | Ücretsiz | gemini-2.0-flash |
| OpenAI | Ücretli | gpt-4o-mini |
| Ollama | Ücretsiz (yerel) | llama3.1:8b |

## API Anahtarları

Tüm anahtarlar `.env` dosyasında tutulur, Git'e **asla** gönderilmez.

| Servis | Anahtar | Zorunlu? |
|--------|---------|---------|
| Gemini AI | `GEMINI_API_KEY` | Evet (ücretsiz) |
| Pexels Stok | `PEXELS_API_KEY` | Evet (ücretsiz) |
| YouTube Data | `YOUTUBE_API_KEY` | Araştırma için |
| YouTube Upload | OAuth credentials | Yükleme için |
| Telegram | `TELEGRAM_BOT_TOKEN` | Bildirim için |
| Edge-TTS | — | Gerekmez (ücretsiz) |

## Gereksinimler

- Python 3.11+
- FFmpeg (video montaj için): `winget install FFmpeg`
- Edge-TTS: `pip install edge-tts`

## Testler

```powershell
python -m pytest tests/ -v
```
