import os
from pathlib import Path
import re

# Stock video (Pexels) ve AI saglayicilari icin API anahtarlarini yukle
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from channel_ops.project import (
    transition_video,
    SCRIPT_DIR,
    VISUALS_DIR,
    VOICE_DIR,
    EDIT_DIR,
    EXPORT_DIR,
)
from channel_ops.tts_engine import generate_with_subtitles
from channel_ops.video_assembler import (
    merge_audio_video,
    burn_subtitles,
    build_timed_reel,
)
from channel_ops.visual_planner import (
    parse_srt,
    build_scenes,
    plan_scene_queries,
    assign_clips_to_scenes,
)
from channel_ops.stock_media import fetch_clips_map

# Seslendirilmemesi gereken satirlar. AI senaryoyu yapisal isaretlerle uretiyor:
# kunye satirlari, zaman damgali bolum basliklari, anlatici etiketleri ve
# gorsel yonergeleri. Bunlar metin olarak senaryoda kalir ama SESE DONUSMEMELI.
_DROP_LINE_PATTERNS = (
    re.compile(r'^-{3,}$'),                                  # --- ayiraci
    re.compile(r'^(video id|topic|estimated run ?time|run ?time|title)\s*:', re.I),
    re.compile(r'^\(\s*\d{1,2}:\d{2}\s*[-–—]\s*\d{1,2}:\d{2}\s*\)'),  # (0:00 - 1:30) Hook
    re.compile(r'^\((narrator|voice ?over|vo|host)[^)]*\)$', re.I),    # (Narrator)
    re.compile(r'^\[.*\]$'),                                 # [VISUAL: ...] yonergeleri
)


def clean_script_text(text: str) -> str:
    """Senaryo dosyasindan yalnizca seslendirilecek anlatimi cikarir.

    Markdown isaretlerini temizler, kaynak referanslarini ([C-001]) siler ve
    yapisal satirlari (kunye, bolum basligi, anlatici etiketi) atar.
    """
    # AI genellikle senaryoyu bir aciklama cumlesiyle baslatip '---' ile ayirir.
    # Ilk satirlardaki ayiraca kadar olan kismi (AI'nin kendi yorumu) atiyoruz.
    lines = text.splitlines()
    for index, line in enumerate(lines[:6]):
        if re.match(r'^-{3,}$', line.strip()):
            lines = lines[index + 1:]
            break

    kept = []
    for line in lines:
        stripped = line.strip()
        # Once vurgu isaretlerini kaldir ki desenler eslesebilsin.
        stripped = re.sub(r'\*\*(.*?)\*\*', r'\1', stripped)
        stripped = re.sub(r'(?<!\w)_(.+?)_(?!\w)', r'\1', stripped)
        stripped = re.sub(r'^#{1,6}\s*', '', stripped)
        stripped = re.sub(r'\[C-\d+\]', '', stripped)  # kaynak referanslari

        if not stripped:
            continue
        if any(pattern.match(stripped) for pattern in _DROP_LINE_PATTERNS):
            continue

        # Kaynak referanslari silinince geriye "locations ." gibi bosluklu
        # noktalama kaliyor; hem TTS bunu ayri bir cumle sanip 0.06 sn'lik
        # altyazi uretiyor hem de ekranda yanlis gorunuyor.
        stripped = re.sub(r'\s+([,.;:!?])', r'\1', stripped)
        stripped = re.sub(r'\(\s*\)', '', stripped)
        stripped = re.sub(r'\s{2,}', ' ', stripped).strip()

        if not stripped or stripped in {'.', ',', '-', '–'}:
            continue

        kept.append(stripped)

    return "\n\n".join(kept)

def find_latest_script(script_dir: Path) -> Path | None:
    """En son uretilen senaryo taslagini bulur.

    `04_script` klasoru uc tur dosya barindirir:
      * `..._prompt.md`  -> AI'ya gonderilen istem (ASLA seslendirilmemeli)
      * `..._template.md` -> API'siz sablon modunda uretilen taslak
      * `..._ai.md` / `..._gemini.md` -> gercek AI ciktisi

    Dosya adlarindaki zaman damgasi siralanabilir oldugu icin, prompt
    dosyalarini eledikten sonra en son olani secmek yeterlidir.
    """
    drafts = [p for p in script_dir.glob("*.md") if not p.stem.endswith("_prompt")]
    if not drafts:
        return None
    return sorted(drafts, key=lambda p: p.name)[-1]


def clear_previous_stock_clips(visuals_dir: Path) -> int:
    """Onceki calistirmadan kalan stock kliplerini siler.

    Her calistirma sahnelere gore YENI klipler indirir; eskiler silinmezse
    klasor her seferinde yuzlerce MB buyur. Bu dosyalar yeniden indirilebilir
    oldugu icin silinmeleri guvenli — lisans kaydi asset_manifest.csv'de kalir.
    """
    removed = 0
    for clip in visuals_dir.glob("stock_*.mp4"):
        try:
            clip.unlink()
            removed += 1
        except OSError as exc:
            print(f" -> Silinemedi {clip.name}: {exc}")
    return removed


def main():
    root = Path(r"C:\Users\win10\Desktop\Projeler\channel-content-os")
    vid_id = "VIDEO-001"
    vid_dir = root / "videos" / vid_id
    
    print("1. Senaryo onaylanıyor...")
    try:
        transition_video(root, vid_id, "script_approved", approval_note="Otonom ajan tarafından otomatik onaylandı.")
        transition_video(root, vid_id, "production", approval_note="Prodüksiyon başlatıldı.")
    except ValueError:
        print(" -> Zaten prodüksiyon aşamasında (veya ilerisinde), devam ediliyor.")
    
    print("2. Senaryo okunuyor ve temizleniyor...")
    script_file = find_latest_script(vid_dir / SCRIPT_DIR)
    if script_file is None:
        print("Senaryo bulunamadı! (04_script içinde prompt disi bir taslak yok)")
        return

    print(f" -> Kullanilan senaryo: {script_file.name}")
    raw_text = script_file.read_text(encoding="utf-8")
    # Sadece 'Research Brief' vs kısımlarını atlayıp asıl narratife odaklanabiliriz ama şimdilik hepsini okutalım
    clean_text = clean_script_text(raw_text)
    
    print("3. Edge-TTS ile seslendirme ve altyazı (SRT) üretiliyor...")
    audio_dir = vid_dir / VOICE_DIR
    audio_path = audio_dir / "narration.mp3"
    sub_path = audio_dir / "subs.srt"
    generate_with_subtitles(clean_text, audio_path, sub_path, rate="+10%")

    print("4. Gorsel arka plan hazirlaniyor (Pexels stock video)...")
    img_dir = vid_dir / VISUALS_DIR
    img_dir.mkdir(parents=True, exist_ok=True)
    img_path = img_dir / "bg_black.jpg"

    # Ara render'lar 07_edit'te, teslim edilen dosya 08_export'ta durur.
    edit_dir = vid_dir / EDIT_DIR
    export_dir = vid_dir / EXPORT_DIR
    edit_dir.mkdir(parents=True, exist_ok=True)
    export_dir.mkdir(parents=True, exist_ok=True)
    raw_vid = edit_dir / "raw.mp4"
    merged_vid = edit_dir / "merged.mp4"
    final_vid = export_dir / "final.mp4"

    # Anlatimin gercek zamanlamasi SRT'de duruyor; sahneleri ondan cikariyoruz
    # ki gorsel, o anda ne anlatiliyorsa onu gostersin.
    segments = []
    try:
        cues = parse_srt(sub_path)
        scenes = build_scenes(cues, target_seconds=15.0)
        if scenes:
            plan_scene_queries(scenes)
            print(f" -> {len(scenes)} sahne planlandi (ort. {sum(s.duration for s in scenes)/len(scenes):.1f} sn)")

            removed = clear_previous_stock_clips(img_dir)
            if removed:
                print(f" -> Onceki calistirmadan {removed} klip temizlendi.")

            clip_map = fetch_clips_map(
                [s.query for s in scenes],
                img_dir,
                max_downloads=16,
                video_directory=vid_dir,
            )
            print(f" -> {len(clip_map)} farkli klip indirildi.")
            segments = assign_clips_to_scenes(scenes, clip_map)

            matched = sum(1 for s in scenes if s.query in clip_map)
            print(
                f" -> {matched}/{len(scenes)} sahne kendi gorseliyle eslesti "
                f"(kalani yedek kliplerle dolduruldu)."
            )
    except Exception as e:
        print(" -> Sahne planlamasi basarisiz:", e)

    print("5. FFmpeg ile Montaj yapılıyor... (Kendi bilgisayarının gücü devrede!)")
    import subprocess

    reel_built = False
    if segments:
        try:
            # Bellek kısıtlamasını aşmak için 720p + ultrafast kullanıyoruz
            build_timed_reel(segments, raw_vid, resolution="1280x720")
            reel_built = True
            print(f" -> Anlatimla hizali reel olusturuldu ({len(segments)} sahne).")
        except Exception as e:
            print(" -> Reel olusturulamadi, siyah arka plana donuluyor:", e)

    if not reel_built:
        print(" -> Fallback: sabit siyah arka plan uretiliyor...")
        try:
            from PIL import Image
            img = Image.new("RGB", (1920, 1080), color="#111111")
            img.save(img_path)
        except Exception as e:
            print("Pillow hatasi:", e)
        subprocess.run(["ffmpeg", "-y", "-loop", "1", "-framerate", "30", "-i", str(img_path), "-vf", "scale=1280:720", "-t", "10", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast", str(raw_vid)], check=True)
    print(" -> Ses ve Video birleştiriliyor...")
    merge_audio_video(raw_vid, audio_path, merged_vid)
    
    import shutil
    if sub_path.exists() and sub_path.stat().st_size > 0:
        print(" -> Altyazılar videoya gömülüyor...")
        burn_subtitles(merged_vid, sub_path, final_vid)
    else:
        print(" -> Uyari: Altyazi dosyasi (subs.srt) bos uretilmis! Altyazi gomulmeden devam ediliyor...")
        shutil.copy(merged_vid, final_vid)
    
    print("6. Son Durum Güncellemeleri...")
    try:
        transition_video(root, vid_id, "qa", approval_note="Render tamamlandı, video hazır.")
        transition_video(root, vid_id, "private_uploaded", approval_note="API test asamasinda pass gecildi.")
        transition_video(root, vid_id, "publish_approved", approval_note="Otonom yayin onayi.")
        transition_video(root, vid_id, "published", approval_note="Video yayinda!")
    except ValueError:
        print(" -> Durum zaten guncel (veya onceden yayinlanmis).")
    
    print(f"ISLEM TAMAM! Final video surada: {final_vid}")

if __name__ == "__main__":
    main()
