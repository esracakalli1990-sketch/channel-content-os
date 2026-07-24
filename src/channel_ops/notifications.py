"""Telegram notification bot for Channel Content OS.

Sends status updates, approval requests, and weekly reports.
Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env.

Create a bot: https://t.me/BotFather → /newbot
Get chat ID: send a message to your bot, then visit
  https://api.telegram.org/bot<TOKEN>/getUpdates
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}"


def _get_config() -> tuple[str, str]:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set.\n"
            "Create a bot at https://t.me/BotFather"
        )
    return token, chat_id


def send_message(
    text: str,
    *,
    parse_mode: str = "HTML",
    disable_preview: bool = True,
) -> dict:
    """Send a text message to the configured Telegram chat.

    Parameters
    ----------
    text:
        Message text (supports HTML or Markdown formatting).
    parse_mode:
        ``"HTML"`` or ``"Markdown"``.
    """
    token, chat_id = _get_config()
    url = f"{TELEGRAM_API.format(token=token)}/sendMessage"

    body = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": disable_preview,
    }).encode("utf-8")

    request = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=15) as response:
            result = json.load(response)
        logger.info("Telegram message sent (message_id=%s)", result.get("result", {}).get("message_id"))
        return result
    except HTTPError as exc:
        logger.error("Telegram API error: HTTP %d", exc.code)
        raise RuntimeError(f"Telegram API returned HTTP {exc.code}") from exc
    except URLError as exc:
        logger.error("Telegram connection error: %s", exc.reason)
        raise RuntimeError(f"Could not reach Telegram: {exc.reason}") from exc


def send_document(file_path: Path, *, caption: str = "") -> dict:
    """Send a file (report, log, etc.) to the configured Telegram chat."""
    import email.mime.multipart
    token, chat_id = _get_config()
    url = f"{TELEGRAM_API.format(token=token)}/sendDocument"

    # Multipart form upload
    import mimetypes
    boundary = "----ChannelOSBoundary"
    mime_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"

    file_data = file_path.read_bytes()
    body_parts = []
    # chat_id field
    body_parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{chat_id}")
    # caption field
    if caption:
        body_parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{caption}")
    # file field
    body_parts.append(
        f"--{boundary}\r\n"
        f"Content-Disposition: form-data; name=\"document\"; filename=\"{file_path.name}\"\r\n"
        f"Content-Type: {mime_type}\r\n\r\n"
    )

    body = ("\r\n".join(body_parts)).encode("utf-8") + b"\r\n" + file_data + f"\r\n--{boundary}--\r\n".encode("utf-8")

    request = Request(
        url, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as exc:
        raise RuntimeError(f"Telegram API returned HTTP {exc.code}") from exc


# -----------------------------------------------------------------------
# Predefined notification templates
# -----------------------------------------------------------------------

def notify_video_ready(video_id: str, topic: str) -> dict:
    """Notify that a video is ready for review."""
    return send_message(
        f"🎬 <b>Video hazır — onay bekliyor</b>\n\n"
        f"<b>ID:</b> {video_id}\n"
        f"<b>Konu:</b> {topic}\n\n"
        f"Private olarak YouTube'a yüklendi. Yayın onayı bekleniyor."
    )


def notify_published(video_id: str, topic: str, youtube_url: str = "") -> dict:
    """Notify that a video has been published."""
    url_line = f"\n🔗 {youtube_url}" if youtube_url else ""
    return send_message(
        f"✅ <b>Video yayında!</b>\n\n"
        f"<b>ID:</b> {video_id}\n"
        f"<b>Konu:</b> {topic}{url_line}"
    )


def notify_score(video_id: str, score: float, qualified: bool) -> dict:
    """Notify about a topic score result."""
    emoji = "✅" if qualified else "⚠️"
    tag = "QUALIFIED" if qualified else "NOT QUALIFIED"
    return send_message(
        f"{emoji} <b>Konu skoru: {score}/100 — {tag}</b>\n\n"
        f"<b>ID:</b> {video_id}"
    )


def notify_weekly_report(summary: str) -> dict:
    """Send a weekly performance report."""
    return send_message(f"📊 <b>Haftalık Rapor</b>\n\n{summary}")
