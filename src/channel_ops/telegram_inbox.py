"""Inbound Telegram handling for the Shorts pipeline.

``notifications.py`` covers the outbound direction (bot → user). This module
covers the inbound one (user → bot): polling for new updates, pulling the
video the user recorded in Google Flow, and remembering which updates have
already been handled.

The poller is designed to run as a short-lived GitHub Actions job rather than
a long-running process, so the read cursor has to survive between runs. It is
kept in ``data/telegram_state.json``, which the workflow commits back to the
repository after each poll.

Get your chat ID after messaging the bot once::

    python -m channel_ops telegram-whoami
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from .config_loader import find_project_root
from .notifications import TELEGRAM_API

logger = logging.getLogger(__name__)

STATE_FILE = "data/telegram_state.json"

# getFile cannot serve anything larger than this, no matter the plan.
TELEGRAM_DOWNLOAD_LIMIT_BYTES = 20 * 1024 * 1024


class VideoTooLargeError(RuntimeError):
    """Raised when a video exceeds what the Bot API is willing to hand over."""


class OwnerUnknownError(RuntimeError):
    """Raised when TELEGRAM_CHAT_ID is missing or unreadable.

    Deliberately fatal. Without it there is no way to tell the owner's
    messages from a stranger's, and the safe answer to "whose video is this?"
    is to refuse, never to accept everything.
    """


def _owner_chat_id() -> int:
    """The one chat the pipeline will take work from.

    A security audit on 12 October found that nothing in the inbound path
    ever compared a message's chat against this value -- it was read only by
    ``notifications.py``, to address outgoing messages. Telegram's getUpdates
    returns messages from every chat the bot is in, and bot privacy mode
    covers groups, not direct messages, so any stranger who found the bot
    could send it an MP4 and have it published to the channel. There was no
    second gate: the private-first upload step had been removed on the
    reasoning that clips are reviewed before they are sent to the bot, which
    is exactly the assumption a third-party sender breaks.
    """
    raw = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not raw:
        raise OwnerUnknownError(
            "TELEGRAM_CHAT_ID is not set, so an incoming message cannot be "
            "attributed to the channel owner. Run `python -m channel_ops "
            "telegram-whoami` and store the id in the repository secrets."
        )
    try:
        return int(raw)
    except ValueError as exc:
        raise OwnerUnknownError(
            f"TELEGRAM_CHAT_ID is not a whole number ({raw!r}); it is the "
            "numeric chat id, not the bot token or a username."
        ) from exc


def from_owner(update: dict) -> bool:
    """Whether *update* came from the configured owner chat.

    The single predicate both the video path and the command path go
    through. Kept separate from the extractors so there is one place to be
    right, and one place for a test to aim at.
    """
    chat = (update.get("message") or {}).get("chat") or {}
    try:
        return int(chat.get("id")) == _owner_chat_id()
    except (TypeError, ValueError):
        # A message with no usable chat id is not the owner's.
        return False


def _owner_updates(updates: list[dict]) -> list[dict]:
    """Drop everything that did not come from the owner, and say how much.

    The count is logged, never the content: an attacker's caption is not
    something to echo into a log the owner reads.
    """
    kept = [update for update in updates if from_owner(update)]
    dropped = len(updates) - len(kept)
    if dropped:
        logger.warning(
            "Ignored %d update(s) from chats other than the configured owner",
            dropped,
        )
    return kept


@dataclass(frozen=True)
class IncomingVideo:
    """A video the user sent to the bot."""

    update_id: int
    file_id: str
    file_size: int
    caption: str
    sent_by: str

    @property
    def size_mb(self) -> float:
        return self.file_size / (1024 * 1024)


def _token() -> str:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not set. Create a bot at https://t.me/BotFather "
            "and add the token to your GitHub repository secrets."
        )
    return token


def _call(method: str, **params: object) -> dict:
    """Call a Bot API method with query parameters and return the result field."""
    url = f"{TELEGRAM_API.format(token=_token())}/{method}"
    if params:
        url = f"{url}?{urlencode({k: v for k, v in params.items() if v is not None})}"

    try:
        with urlopen(url, timeout=60) as response:
            payload = json.load(response)
    except HTTPError as exc:
        raise RuntimeError(f"Telegram {method} failed (HTTP {exc.code})") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach Telegram: {exc.reason}") from exc

    if not payload.get("ok"):
        raise RuntimeError(f"Telegram {method} rejected: {payload.get('description', '?')}")
    return payload.get("result", {})


# -----------------------------------------------------------------------
# Read cursor
# -----------------------------------------------------------------------

def _state_path(root: Path | None = None) -> Path:
    return (root or find_project_root()) / STATE_FILE


def load_offset(root: Path | None = None) -> int:
    """Return the next update_id to ask for, or 0 on a first run."""
    path = _state_path(root)
    if not path.exists():
        return 0
    try:
        return int(json.loads(path.read_text(encoding="utf-8")).get("offset", 0))
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        # A corrupt cursor would otherwise wedge the poller forever. Starting
        # over only risks re-handling updates Telegram still has (24h window).
        logger.warning("Unreadable Telegram cursor at %s (%s) — starting from scratch", path, exc)
        return 0


def save_offset(offset: int, root: Path | None = None) -> Path:
    """Persist the read cursor so the next workflow run resumes after it."""
    path = _state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"offset": offset}, indent=2) + "\n", encoding="utf-8")
    return path


# -----------------------------------------------------------------------
# Polling
# -----------------------------------------------------------------------

def fetch_updates(offset: int = 0, *, limit: int = 20) -> list[dict]:
    """Fetch pending updates, acknowledging everything before *offset*.

    Telegram drops an update once a later offset has been requested, so the
    caller must persist the new cursor before treating the work as done.
    """
    result = _call(
        "getUpdates",
        offset=offset or None,
        limit=limit,
        timeout=0,  # return immediately; the workflow schedule is our clock
        allowed_updates=json.dumps(["message"]),
    )
    updates = result if isinstance(result, list) else []
    logger.info("Fetched %d update(s) from offset %d", len(updates), offset)
    return updates


def extract_videos(updates: list[dict]) -> list[IncomingVideo]:
    """Pick out the owner's video messages, ignoring chatter and strangers.

    Videos sent from the gallery arrive as ``video``; the same file sent with
    "send as file" (which keeps Flow's quality intact) arrives as ``document``,
    so both are accepted — from the owner's chat only. Everything this returns
    is published publicly without a further review step, so the filter is the
    only thing standing between a stranger's upload and the channel.
    """
    videos: list[IncomingVideo] = []
    for update in _owner_updates(updates):
        message = update.get("message") or {}
        media = message.get("video")
        if not media:
            document = message.get("document") or {}
            if str(document.get("mime_type", "")).startswith("video/"):
                media = document
        if not media:
            continue

        sender = message.get("from") or {}
        videos.append(
            IncomingVideo(
                update_id=int(update.get("update_id", 0)),
                file_id=str(media.get("file_id", "")),
                file_size=int(media.get("file_size", 0)),
                caption=str(message.get("caption", "")).strip(),
                sent_by=str(sender.get("username") or sender.get("first_name") or "unknown"),
            )
        )
    logger.info("Found %d video(s) in %d update(s)", len(videos), len(updates))
    return videos


def extract_commands(updates: list[dict]) -> list[str]:
    """Return the lowercase text commands the owner sent to the bot.

    A leading slash is optional so both "/rapor" and "rapor" work — the bot is
    used from a phone where the slash is an extra keystroke. Filtered by chat
    for the same reason videos are: these commands spend YouTube API quota and
    Gemini credit, and a stranger should not be able to spend either.
    """
    commands: list[str] = []
    for update in _owner_updates(updates):
        text = str((update.get("message") or {}).get("text", "")).strip().lower()
        if text:
            commands.append(text.lstrip("/").split("@")[0].split()[0])
    return commands


def download_video(video: IncomingVideo, destination: Path) -> Path:
    """Download an incoming video to *destination*."""
    return download_file(video.file_id, video.file_size, destination)


def download_file(file_id: str, file_size: int, destination: Path) -> Path:
    """Download a Telegram file by id.

    Taking the id rather than the update lets a video be fetched long after it
    arrived: publishing is deferred to a scheduled slot, and Telegram keeps
    file ids valid indefinitely even though the download path from ``getFile``
    expires within the hour.

    Raises
    ------
    VideoTooLargeError
        If the file is beyond the Bot API's 20 MB ceiling. This is a hard
        Telegram limit, so the only fix is to export a smaller clip.
    """
    if file_size > TELEGRAM_DOWNLOAD_LIMIT_BYTES:
        size_mb = file_size / (1024 * 1024)
        raise VideoTooLargeError(
            f"Video is {size_mb:.1f} MB; Telegram bots cannot download more than 20 MB. "
            f"Re-export the clip at a lower bitrate or resolution and send it again."
        )

    file_path = _call("getFile", file_id=file_id).get("file_path", "")
    if not file_path:
        raise RuntimeError("Telegram did not return a download path for the video.")

    url = f"https://api.telegram.org/file/bot{_token()}/{file_path}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urlopen(url, timeout=300) as response:
            destination.write_bytes(response.read())
    except (HTTPError, URLError) as exc:
        raise RuntimeError(f"Downloading the video from Telegram failed: {exc}") from exc

    logger.info("Downloaded %.1f MB to %s", file_size / (1024 * 1024), destination)
    return destination


def describe_chats() -> list[dict]:
    """Return the chats that have messaged the bot, for one-time setup.

    Backs ``channel-os telegram-whoami``: message the bot once, run the
    command, and it prints the chat ID to store as ``TELEGRAM_CHAT_ID``.

    Deliberately unfiltered, and the only thing here that is. Its whole job
    is to report the chat id before one has been configured, so filtering by
    the configured id would make the setup step depend on its own output.
    It reports chats; it never feeds the publishing path.
    """
    seen: dict[int, dict] = {}
    for update in fetch_updates(offset=0):
        chat = (update.get("message") or {}).get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is not None and chat_id not in seen:
            seen[chat_id] = {
                "chat_id": chat_id,
                "type": chat.get("type", ""),
                "name": chat.get("username") or chat.get("first_name") or chat.get("title", ""),
            }
    return list(seen.values())
