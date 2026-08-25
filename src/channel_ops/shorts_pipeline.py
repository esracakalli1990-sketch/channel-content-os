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
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

from . import (
    instagram_uploader,
    media_host,
    notifications,
    telegram_inbox,
    video_overlay,
    youtube_uploader,
)
from .config_loader import find_project_root
from .providers.base import AIProvider
from .shorts_metadata import generate_metadata
from . import shorts_prompts
from .shorts_prompts import Concept, PromptPair, generate_prompt_pairs

logger = logging.getLogger(__name__)

PENDING_FILE = "data/shorts_pending.json"
PUBLISHED_FILE = "data/shorts_published.json"
QUEUE_FILE = "data/shorts_queue.json"

# When queued videos go out, as UTC hours. Videos used to publish the moment
# they were sent, which tied release time to whenever the operator happened to
# be free — often the middle of the night. The clips are now made in one
# morning session and released across the day instead.
#
# The hours come from where the viewers actually are (channel-os
# shorts-audience), not from generic advice. Over 90 days: US 49%, Indonesia
# 14%, Vietnam 12%, India 9%, then Brazil, the Philippines, Thailand and Iraq
# at 4% each. That is one large American block and a second, nearly as large,
# spread across UTC+5:30 to +8.
#
#   13:00 UTC — evening across South East Asia (20:00 in Jakarta and Hanoi,
#               18:30 in India), morning in the United States
#   23:00 UTC — American prime time (19:00 New York, 16:00 Los Angeles)
#
# Two a day, not three. On 25 August two consecutive uploads took 9 and 5
# views in eight hours while the back catalogue earned 53,000 in the same day
# — and YouTube reported both as processed, accepted, unrejected, 9:16 and the
# right length. Nothing was wrong with the videos; they were simply never put
# into the feed. Four weeks of three machine-made uploads a day, right after
# the channel's best day ever, is the likeliest thing to have tripped a
# volume check, so the rate comes down while that is tested. The 17:00 slot
# is the one dropped: it sat between the other two, and cutting it leaves ten
# hours between releases instead of four.
#
# The three slots performed alike (medians 3,076 / 3,316 / 3,671 over 26
# mature videos), so this costs nothing in placement.
#
# Only the file id is queued, never the video: Telegram keeps file ids valid
# indefinitely, so the clip is fetched again at publish time.
PUBLISH_SLOTS_UTC = (13, 23)

# How far ahead the first release must be. The prompts arrive at midnight
# Turkish time (21:00 UTC) and the clips are made straight away, which puts
# them in hand around 22:00 UTC — an hour before the 23:00 slot. Without this
# the batch's first video would go out almost immediately and the day's other
# two would trail it, instead of the batch opening at 13:00 the next day.
MIN_LEAD_HOURS = 4

# Typed into the Telegram chat to ask for a performance report.
REPORT_COMMANDS = frozenset({"rapor", "report", "istatistik", "stats"})

# Typed into the Telegram chat to have the unused ideas sent again. Their
# prompts scroll out of reach within a day or two, and an idea nobody can find
# is the same as no idea at all.
PROMPT_COMMANDS = frozenset({"promptlar", "prompt", "fikirler", "fikir", "ideas"})

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


def has_recent_batch(hours: float, root: Path | None = None) -> bool:
    """Whether a batch of ideas already went out within *hours*."""
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    for entry in _read_json(_data_path(PENDING_FILE, root), []):
        try:
            if datetime.fromisoformat(str(entry.get("batch", ""))) >= cutoff:
                return True
        except ValueError:
            continue
    return False


def send_daily_prompts(
    provider: AIProvider,
    *,
    count: int = 1,
    root: Path | None = None,
    skip_if_recent_hours: float = 0,
) -> list[PromptPair]:
    """Generate *count* concepts, send them to Telegram, and hold them pending.

    One concept per run is the normal case: the job fires three times a day and
    each firing is both the idea and the reminder to make it. Choosing between
    several ideas was work without a payoff — nothing in the data said the
    rejected ones would have done worse.
    """
    # The catch-up runs use this: the job gets one shot a night, and twice now
    # Gemini answered 503 to every retry and the whole night was lost. Later
    # runs repeat the attempt and stand down once a batch has landed.
    if skip_if_recent_hours and has_recent_batch(skip_if_recent_hours, root):
        logger.info("A batch already went out in the last %g hours — nothing to do", skip_if_recent_hours)
        return []

    pairs = generate_prompt_pairs(provider, count=count, root=root)

    path = _data_path(PENDING_FILE, root)
    # Concepts accumulate rather than replace. The job runs three times a day
    # and a video made in the evening still has to match the idea sent that
    # morning, which overwriting the file would have thrown away.
    pending = [
        entry for entry in _read_json(path, [])
        if _is_fresh(entry) and not entry.get("used_at")
    ]
    # Each batch is numbered from one, because that is what the operator writes
    # on the clips. A running counter would have made tonight's first idea
    # "#11" while the caption still said "1".
    #
    batch = _fresh_batch_stamp(pending)

    if count == 1:
        notifications.send_message(
            "🎬 <b>Sıradaki video</b>\n"
            "Promptları Flow'da çalıştır, çıkan videoyu bu sohbete geri gönder."
        )
    else:
        notifications.send_message(
            f"☀️ <b>Yeni fikirler hazır</b> — {len(pairs)} tane.\n"
            f"Beğendiğini Flow'da üret, videoyu bu sohbete geri gönder.\n"
            f"<i>Hangisi olduğunu yazmak için başlığa numarayı ekle.</i>"
        )

    for index, pair in enumerate(pairs, start=1):
        notifications.send_message(_format_prompt_message(index, pair))
        pending.append({
            "index": index,
            "batch": batch,
            "created_at": batch,
            "concept": asdict(pair.concept),
            "text_to_image": pair.text_to_image,
            "image_to_video": pair.image_to_video,
        })

    _write_json(path, pending)
    logger.info("Sent %d prompt pair(s); %d now pending", len(pairs), len(pending))
    return pairs


def _fresh_batch_stamp(pending: list[dict]) -> str:
    """A batch stamp no existing entry uses.

    Two batches sharing a stamp would make a caption of "1" ambiguous again,
    which is the whole problem this is fixing.
    """
    existing = {str(entry.get("batch", "")) for entry in pending}
    now = datetime.now(UTC)
    while now.isoformat() in existing:
        now += timedelta(microseconds=1)
    return now.isoformat()


def resend_pending(root: Path | None = None) -> list[PromptPair]:
    """Re-render every unused concept with the current template and resend it.

    The rendered prompts are stored, not regenerated on demand, so a corrected
    template does not reach concepts that were rendered before it. Rather than
    discard perfectly good ideas, they are rebuilt from the concept and sent
    again.
    """
    path = _data_path(PENDING_FILE, root)
    pending = _read_json(path, [])
    unused = [entry for entry in pending if _is_fresh(entry) and not entry.get("used_at")]
    if not unused:
        return []

    # The pool can hold leftovers from several nights, each numbered from one,
    # so Telegram showed "1, 2, 1" and a caption of "1" was ambiguous — it
    # would have matched the newest batch rather than the first idea listed.
    # Resending consolidates everything into one batch numbered 1..N.
    batch = _fresh_batch_stamp(pending)

    pairs: list[PromptPair] = []
    notifications.send_message(
        f"♻️ <b>Promptlar yenilendi</b> — {len(unused)} fikir.\n"
        f"<b>Eski mesajlardaki promptları kullanma</b>, aşağıdakileri kullan.\n"
        f"<i>Videoyu gönderirken açıklamaya numarasını yaz.</i>"
    )
    for index, entry in enumerate(unused, start=1):
        pair = shorts_prompts.render(Concept(**entry["concept"]))
        entry["text_to_image"] = pair.text_to_image
        entry["image_to_video"] = pair.image_to_video
        entry["index"] = index
        entry["batch"] = batch
        notifications.send_message(_format_prompt_message(index, pair))
        pairs.append(pair)

    _write_json(path, pending)
    logger.info("Re-rendered %d pending concept(s)", len(pairs))
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

    Concepts already published are skipped. Without that, sending three
    uncaptioned videos in one day matched all three to the same concept and
    published them under one title.
    """
    fresh = [entry for entry in pending if _is_fresh(entry) and not entry.get("used_at")]
    if not fresh:
        return None

    lowered = caption.lower()

    numbers = re.findall(r"\d+", caption)
    if numbers:
        wanted = int(numbers[0])
        # A number means "the Nth idea of this morning's batch", which is what
        # the operator is looking at when labelling the clips. Older ideas keep
        # their own numbering and stay reachable by naming the creature, but
        # they must not win a bare "1" against today's first idea.
        newest = max((str(entry.get("batch", "")) for entry in fresh), default="")
        for entry in fresh:
            if str(entry.get("batch", "")) == newest and entry.get("index") == wanted:
                return entry
        for entry in fresh:
            if entry.get("index") == wanted:
                return entry

    for entry in fresh:
        creature = str(entry.get("concept", {}).get("creature", "")).lower()
        if creature and creature in lowered:
            return entry

    return fresh[-1]


# How far back to look when telling the model which hooks are already spent.
RECENT_HOOK_WINDOW = 8


def _recent_hooks(root: Path | None = None) -> list[str]:
    """The hooks burned onto the most recent videos, newest last."""
    history = _read_json(_data_path(PUBLISHED_FILE, root), [])
    hooks = [str(record.get("hook", "")).strip() for record in history[-RECENT_HOOK_WINDOW:]]
    return [hook for hook in hooks if hook]


def _record_published(entry: dict, root: Path | None = None) -> Path:
    path = _data_path(PUBLISHED_FILE, root)
    history = _read_json(path, [])
    history.append(entry)
    return _write_json(path, history)


def process_inbox(provider: AIProvider, *, root: Path | None = None) -> list[dict]:
    """Take in whatever Telegram is holding and release whatever is due.

    Returns one record per video actually published, which is usually not the
    video that just arrived: clips are queued on arrival and go out at their
    slot. Both halves run on every poll of the watch loop, which is what keeps
    release times accurate to a couple of minutes despite GitHub starting
    scheduled jobs hours late.
    """
    root = root or find_project_root()

    # The two halves are independent and must stay that way. Reading the inbox
    # used to run first and uncaught: one failed Telegram fetch took the whole
    # poll down, so a video whose slot had arrived sat in the queue instead of
    # going out. Releasing is the half with a deadline, so it always runs.
    intake_failure: Exception | None = None
    try:
        _accept_incoming(provider, root)
    except (RuntimeError, OSError) as exc:
        logger.exception("Reading the Telegram inbox failed")
        intake_failure = exc

    records = publish_due(provider, root)

    # Re-raised after releasing so the watch loop still reports the outage —
    # swallowing it would make a broken inbox look like a quiet one.
    if intake_failure is not None:
        raise intake_failure
    return records


def _accept_incoming(provider: AIProvider, root: Path) -> list[dict]:
    """Queue every video waiting in Telegram. Returns the queued items."""
    offset = telegram_inbox.load_offset(root)
    updates = telegram_inbox.fetch_updates(offset)

    if not updates:
        logger.debug("Nothing waiting in Telegram")
        return []

    # Acknowledge immediately: Telegram replays unconfirmed updates, and a
    # crash mid-upload must not make the next run upload the same clip again.
    telegram_inbox.save_offset(max(u.get("update_id", 0) for u in updates) + 1, root)

    # A typed command is handled before videos so asking for a report never
    # depends on having sent a clip.
    commands = telegram_inbox.extract_commands(updates)
    if REPORT_COMMANDS.intersection(commands):
        _send_report(root)
    if PROMPT_COMMANDS.intersection(commands):
        _resend_prompts(root)

    videos = telegram_inbox.extract_videos(updates)
    if not videos:
        logger.info("Updates contained no video")
        return []

    pending = _read_json(_data_path(PENDING_FILE, root), [])
    queued: list[dict] = []

    # Sorted by caption so "1", "2", "3" claim the day's slots in that order
    # however Telegram happened to deliver them.
    for video in sorted(videos, key=lambda v: _caption_order(v.caption)):
        try:
            queued.append(enqueue(video, pending, root))
        except telegram_inbox.VideoTooLargeError as exc:
            notifications.send_message(f"⚠️ <b>Video alınamadı</b>\n{_escape(str(exc))}")
        except RuntimeError as exc:
            logger.exception("Queueing failed")
            notifications.send_message(f"❌ <b>Sıraya alınamadı</b>\n{_escape(str(exc))}")

    # Which concepts got used has to outlive this run, or the next video
    # reuses one that is already waiting in the queue.
    if queued:
        _write_json(_data_path(PENDING_FILE, root), pending)
    return queued


def _caption_order(caption: str) -> tuple[int, str]:
    """Sort key putting a numbered caption first, in numeric order."""
    numbers = re.findall(r"\d+", caption)
    return (int(numbers[0]), caption) if numbers else (10**6, caption)


# -----------------------------------------------------------------------
# Release queue
# -----------------------------------------------------------------------

def next_slot(taken: list[datetime], now: datetime | None = None) -> datetime:
    """The next release time not already claimed by a queued video.

    Slots fill forward: three clips sent in one morning take today's three
    remaining slots, and anything beyond that rolls into the following days
    rather than going out together.
    """
    now = now or datetime.now(UTC)
    earliest = now + timedelta(hours=MIN_LEAD_HOURS)
    claimed = {slot.replace(second=0, microsecond=0) for slot in taken}
    day = now.date()
    for offset in range(14):  # a fortnight is far past any sane backlog
        for hour in sorted(PUBLISH_SLOTS_UTC):
            candidate = datetime.combine(
                day + timedelta(days=offset), time(hour=hour), tzinfo=UTC
            )
            if candidate >= earliest and candidate not in claimed:
                return candidate
    # Every slot for two weeks is spoken for, which means something is wrong
    # upstream; releasing now beats holding the video indefinitely.
    return now


def _queued_times(queue: list[dict]) -> list[datetime]:
    times: list[datetime] = []
    for item in queue:
        try:
            times.append(datetime.fromisoformat(item["publish_at"]))
        except (KeyError, ValueError):
            continue
    return times


def enqueue(video: telegram_inbox.IncomingVideo, pending: list[dict], root: Path) -> dict:
    """Match an arriving clip to a concept and hold it for its release slot."""
    entry = match_pending(video.caption, pending)
    if entry is None:
        raise RuntimeError(
            "No pending concept to attach this video to. Run the prompt job first, "
            "or send the video within two weeks of receiving its prompt."
        )

    path = _data_path(QUEUE_FILE, root)
    queue = _read_json(path, [])
    publish_at = next_slot(_queued_times(queue))

    item = {
        "queued_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "publish_at": publish_at.isoformat(timespec="seconds"),
        "file_id": video.file_id,
        "file_size": video.file_size,
        "caption": video.caption,
        "concept": entry["concept"],
    }
    queue.append(item)
    _write_json(path, queue)

    # Retire the concept now rather than at publish time: the next clip in the
    # same batch must not match it again while this one is still waiting.
    entry["used_at"] = item["queued_at"]

    concept = Concept(**entry["concept"])
    notifications.send_message(
        f"🗓 <b>Sıraya alındı</b> — {_escape(concept.creature)}\n"
        f"Yayın saati: <b>{publish_at.strftime('%d.%m %H:%M')} UTC</b> "
        f"({(publish_at + timedelta(hours=3)).strftime('%H:%M')} TRT)"
    )
    logger.info("Queued %s for %s", concept.creature, publish_at.isoformat())
    return item


def publish_due(provider: AIProvider, root: Path | None = None) -> list[dict]:
    """Publish every queued video whose slot has arrived."""
    root = root or find_project_root()
    path = _data_path(QUEUE_FILE, root)
    queue = _read_json(path, [])
    if not queue:
        return []

    now = datetime.now(UTC)
    records: list[dict] = []
    remaining: list[dict] = []

    for item in queue:
        try:
            due = datetime.fromisoformat(item["publish_at"]) <= now
        except (KeyError, ValueError):
            due = True  # an unreadable slot must not strand the video forever
        if not due:
            remaining.append(item)
            continue
        try:
            records.append(_publish_queued(item, provider, root))
        except telegram_inbox.VideoTooLargeError as exc:
            notifications.send_message(f"⚠️ <b>Video alınamadı</b>\n{_escape(str(exc))}")
        except RuntimeError as exc:
            logger.exception("Publishing failed")
            notifications.send_message(f"❌ <b>Yükleme başarısız</b>\n{_escape(str(exc))}")

    # Written whatever happened: a failed item is dropped rather than retried
    # forever, and the failure was already reported to Telegram.
    _write_json(path, remaining)
    return records


def _publish_queued(item: dict, provider: AIProvider, root: Path) -> dict:
    """Fetch a queued clip from Telegram and publish it."""
    concept = Concept(**item["concept"])
    metadata = generate_metadata(provider, concept, recent_hooks=_recent_hooks(root))
    title, description = metadata.youtube()

    with tempfile.TemporaryDirectory() as workspace:
        destination = Path(workspace) / "short.mp4"
        telegram_inbox.download_file(item["file_id"], int(item.get("file_size", 0)), destination)
        destination = _burn_hook(destination, metadata.hook)
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
        "size_mb": round(int(item.get("file_size", 0)) / (1024 * 1024), 1),
        # When the clip arrived versus when it went out, so a slot's effect on
        # reach can be read back from the record.
        "queued_at": item.get("queued_at", ""),
        "hook": metadata.hook,
        # Which videos carried the corner badge. Empty since it was switched
        # off, and kept so the before/after split stays readable in the record.
        "badge": "",
        # Which wording produced this video. Template changes are tested by
        # comparing videos before and after, which is guesswork without a
        # marker on each record.
        "template_version": shorts_prompts.template_version(),
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


CHANNEL_HANDLE = "@unfoldableslab"

# The corner badge — the series number and the handle, drawn over every frame —
# ran from video 43 to 62 and is now off.
#
# It was added on the theory that nothing on screen told a viewer a channel
# existed. Twenty videos later it has never shown a benefit, and both readings
# of the data point the other way: subscribers per thousand views went 0.36 ->
# 0.24 and likes per thousand 4.13 -> 3.80. Neither is conclusive — the badged
# videos are newer and have had less time to collect subscribers — but the one
# thing the badge was supposed to improve is the one thing that did not
# improve, twice measured.
#
# The overlay code keeps its optional badge argument so the idea can be
# retested properly if there is ever a reason to. `badge` stays in the
# published record, now empty, so the before/after split stays readable.


def _burn_hook(clip: Path, hook: str, badge: str = "") -> Path:
    """Return the clip with its hook drawn on, or untouched on failure.

    A missing overlay costs some reach; refusing to publish would cost the
    whole video, so every problem here falls back to the original file.
    """
    if not hook and not badge:
        return clip
    try:
        return video_overlay.add_hook(clip, clip.with_name("hooked.mp4"), hook, badge)
    except (video_overlay.OverlayUnavailable, OSError) as exc:
        logger.warning("Publishing without the overlays: %s", exc)
        return clip


def _resend_prompts(root: Path) -> None:
    """Answer a /promptlar command. Never raises — a failed resend must not
    stop a video waiting behind it from being published."""
    try:
        pairs = resend_pending(root)
        if not pairs:
            notifications.send_message(
                "📭 <b>Havuzda kullanılmamış fikir yok.</b>\n"
                "Bu gece yenileri gelecek."
            )
        logger.info("Resent %d pending prompt(s)", len(pairs))
    except (RuntimeError, OSError) as exc:
        logger.warning("Resend failed: %s", exc)
        notifications.send_message(f"⚠️ <b>Promptlar gönderilemedi</b>\n{_escape(str(exc))}")


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
