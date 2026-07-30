"""The two halves of the Unfoldables loop, as run by scheduled jobs.

Morning job: invent concepts, render the prompt pairs, send them to Telegram.
Inbox job:   pick up whatever video came back, caption it, upload it.

Video generation stays manual — Google Flow has no API on this plan, and the
user makes the clip by hand between the two jobs. Everything either side of
that is automated.

The clip itself is never written into the repository: it is downloaded to a
temporary directory, uploaded, and dropped. The repository is public and video
files would bloat it regardless. Only the text record is kept, in
``data/shorts_published.json``.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from . import (
    instagram_uploader,
    media_host,
    notifications,
    telegram_inbox,
    youtube_uploader,
)
from .config_loader import find_project_root
from .providers.base import AIProvider
from .shorts_metadata import generate_metadata
from .shorts_prompts import Concept, PromptPair, generate_prompt_pairs

logger = logging.getLogger(__name__)

PENDING_FILE = "data/shorts_pending.json"
PUBLISHED_FILE = "data/shorts_published.json"

# Typed into the Telegram chat to ask for a performance report.
REPORT_COMMANDS = frozenset({"rapor", "report", "istatistik", "stats"})

# Concepts older than this are no longer offered as a match for an arriving clip.
PENDING_EXPIRY_DAYS = 14


def _data_path(name: str, root: Path | None = None) -> Path:
    return (root or find_project_root()) / name


def _read_json(path: Path, default: list) -> list:
    if not path.exists():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else default
    except json.JSONDecodeError as exc:
        logger.warning("Unreadable %s (%s) — treating as empty", path, exc)
        return default


def _write_json(path: Path, payload: list) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


# -----------------------------------------------------------------------
# Morning job
# -----------------------------------------------------------------------

def _format_prompt_message(index: int, pair: PromptPair) -> str:
    """Render one concept as a Telegram message.

    The two prompts are sent in <pre> blocks so Telegram offers a tap-to-copy
    on each — the user is on a phone and has to paste them into Flow.
    """
    concept = pair.concept
    return (
        f"🧩 <b>#{index} — {concept.creature.title()}</b>\n"
        f"<i>{concept.shape} · {concept.material}</i>\n\n"
        f"<b>1) Görsel promptu (Text → Image)</b>\n"
        f"<pre>{_escape(pair.text_to_image)}</pre>\n"
        f"<b>2) Video promptu (Image → Video)</b>\n"
        f"<pre>{_escape(pair.image_to_video)}</pre>\n"
        f"⚙️ Flow'da en-boy oranını <b>9:16</b> seç."
    )


def _escape(text: str) -> str:
    """Escape the three characters Telegram's HTML parser cares about."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def send_daily_prompts(
    provider: AIProvider,
    *,
    count: int = 3,
    root: Path | None = None,
) -> list[PromptPair]:
    """Generate *count* concepts, send them to Telegram, and hold them pending."""
    pairs = generate_prompt_pairs(provider, count=count, root=root)

    notifications.send_message(
        f"☀️ <b>Günün fikirleri hazır</b> — {len(pairs)} tane.\n"
        f"Beğendiğini Flow'da üret, videoyu bu sohbete geri gönder.\n"
        f"<i>Hangisi olduğunu yazmak için başlığa numarayı ekle (örn. \"2\").</i>"
    )
    for index, pair in enumerate(pairs, start=1):
        notifications.send_message(_format_prompt_message(index, pair))

    pending = [
        {
            "index": index,
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "concept": asdict(pair.concept),
            "text_to_image": pair.text_to_image,
            "image_to_video": pair.image_to_video,
        }
        for index, pair in enumerate(pairs, start=1)
    ]
    _write_json(_data_path(PENDING_FILE, root), pending)
    logger.info("Sent %d prompt pair(s) and stored them as pending", len(pairs))
    return pairs


# -----------------------------------------------------------------------
# Inbox job
# -----------------------------------------------------------------------

def _is_fresh(entry: dict) -> bool:
    stamp = entry.get("created_at", "")
    try:
        age = datetime.now(UTC) - datetime.fromisoformat(stamp)
    except ValueError:
        return True  # an unparseable date should not silently drop a concept
    return age.days <= PENDING_EXPIRY_DAYS


def match_pending(caption: str, pending: list[dict]) -> dict | None:
    """Choose which pending concept an arriving video belongs to.

    Matching order: an explicit number in the caption, then a creature named in
    it, then the most recent concept. The last case is what makes the common
    path work — send a video with no caption at all and it still publishes.
    """
    fresh = [entry for entry in pending if _is_fresh(entry)]
    if not fresh:
        return None

    lowered = caption.lower()

    numbers = re.findall(r"\d+", caption)
    if numbers:
        wanted = int(numbers[0])
        for entry in fresh:
            if entry.get("index") == wanted:
                return entry

    for entry in fresh:
        creature = str(entry.get("concept", {}).get("creature", "")).lower()
        if creature and creature in lowered:
            return entry

    return fresh[-1]


def _record_published(entry: dict, root: Path | None = None) -> Path:
    path = _data_path(PUBLISHED_FILE, root)
    history = _read_json(path, [])
    history.append(entry)
    return _write_json(path, history)


def process_inbox(provider: AIProvider, *, root: Path | None = None) -> list[dict]:
    """Handle every video waiting in Telegram. Returns one record per upload."""
    root = root or find_project_root()
    offset = telegram_inbox.load_offset(root)
    updates = telegram_inbox.fetch_updates(offset)

    if not updates:
        logger.info("Nothing waiting in Telegram")
        return []

    # Acknowledge immediately: Telegram replays unconfirmed updates, and a
    # crash mid-upload must not make the next run upload the same clip again.
    telegram_inbox.save_offset(max(u.get("update_id", 0) for u in updates) + 1, root)

    # A typed command is handled before videos so asking for a report never
    # depends on having sent a clip.
    if REPORT_COMMANDS.intersection(telegram_inbox.extract_commands(updates)):
        _send_report(root)

    videos = telegram_inbox.extract_videos(updates)
    if not videos:
        logger.info("Updates contained no video")
        return []

    pending = _read_json(_data_path(PENDING_FILE, root), [])
    records: list[dict] = []

    for video in videos:
        try:
            records.append(_publish_one(video, pending, provider, root))
        except telegram_inbox.VideoTooLargeError as exc:
            notifications.send_message(f"⚠️ <b>Video alınamadı</b>\n{_escape(str(exc))}")
        except RuntimeError as exc:
            logger.exception("Publishing failed")
            notifications.send_message(f"❌ <b>Yükleme başarısız</b>\n{_escape(str(exc))}")
    return records


def _publish_one(
    video: telegram_inbox.IncomingVideo,
    pending: list[dict],
    provider: AIProvider,
    root: Path,
) -> dict:
    """Download one clip, caption it, and publish it to YouTube and Instagram."""
    entry = match_pending(video.caption, pending)
    if entry is None:
        raise RuntimeError(
            "No pending concept to attach this video to. Run the prompt job first, "
            "or send the video within two weeks of receiving its prompt."
        )

    concept = Concept(**entry["concept"])
    metadata = generate_metadata(provider, concept)
    title, description = metadata.youtube()

    with tempfile.TemporaryDirectory() as workspace:
        destination = Path(workspace) / "short.mp4"
        telegram_inbox.download_video(video, destination)
        # Published straight to public: the clip is reviewed in Flow before it
        # is ever sent to the bot, so a private-first step adds no second look —
        # it only left videos stranded unlisted when publishing was forgotten.
        result = youtube_uploader.upload_video(
            destination,
            title,
            description,
            tags=metadata.youtube_tags(),
            privacy="public",
            made_for_kids=False,
        )
        # Instagram is attempted while the file is still on disk, but only
        # after YouTube has the video: a Reels failure must not cost the upload
        # that already worked.
        reel_url, reel_media_id = _publish_to_instagram(destination, metadata.instagram())

    youtube_id = result.get("id", "")
    record = {
        "published_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "creature": concept.creature,
        "title": title,
        "youtube_video_id": youtube_id,
        "youtube_url": f"https://youtube.com/watch?v={youtube_id}",
        "instagram_url": reel_url,
        # Stored so reports can fetch insights without searching the account's
        # media for a matching permalink.
        "instagram_media_id": reel_media_id,
        "instagram_caption": metadata.instagram(),
        "tiktok_caption": metadata.tiktok(),
        "size_mb": round(video.size_mb, 1),
    }
    _record_published(record, root)

    instagram_line = f"📸 Instagram: {reel_url}\n" if reel_url else ""
    notifications.send_message(
        f"✅ <b>Yayınlandı</b>\n\n"
        f"<b>{_escape(title)}</b>\n"
        f"▶️ {record['youtube_url']}\n"
        f"{instagram_line}"
    )

    # TikTok has no automated path yet: its API cannot post publicly before the
    # developer audit clears. The caption is sent as its own tap-to-copy message
    # so the clip — already in this chat — can be posted by hand in seconds.
    notifications.send_message(
        f"🎵 <b>TikTok açıklaması</b> — kopyala, videoyu elle yükle\n\n"
        f"<pre>{_escape(metadata.tiktok())}</pre>"
    )
    return record


def _send_report(root: Path) -> None:
    """Answer a /rapor command. Never raises — a failed report must not stop
    the video waiting behind it from being published."""
    from . import reporting

    try:
        reporting.send_report(root)
        logger.info("Sent a performance report")
    except (RuntimeError, OSError) as exc:
        logger.warning("Report failed: %s", exc)
        notifications.send_message(f"⚠️ <b>Rapor hazırlanamadı</b>\n{_escape(str(exc))}")


def _publish_to_instagram(video_path: Path, caption: str) -> tuple[str, str]:
    """Publish the clip as a Reel and return ``(link, media_id)``.

    Both are "" when nothing was published. Instagram fetches the video from a
    URL rather than accepting an upload, so the clip is staged publicly for the
    duration of the call and removed again. Every failure here is reported and
    swallowed — the video is already on YouTube by this point.
    """
    if not (os.getenv("IG_USER_ID") and os.getenv("IG_ACCESS_TOKEN")):
        logger.info("Instagram credentials not set — skipping Reels")
        return "", ""

    staged = None
    try:
        staged = media_host.stage(video_path, name="short.mp4")
        reel = instagram_uploader.publish_reel(staged.url, caption)
        link = reel.permalink or f"https://instagram.com/reel/{reel.media_id}"
        return link, reel.media_id
    except (instagram_uploader.InstagramError, media_host.HostingError) as exc:
        logger.warning("Instagram publish failed: %s", exc)
        notifications.send_message(
            f"⚠️ <b>Instagram'a yüklenemedi</b> (YouTube tamam)\n{_escape(str(exc))}"
        )
        return "", ""
    finally:
        if staged is not None:
            media_host.unstage(staged)
