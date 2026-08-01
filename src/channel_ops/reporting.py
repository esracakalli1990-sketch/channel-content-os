"""Performance reports for the shorts published to YouTube and Instagram.

Everything reported here comes from ``data/shorts_published.json``, so the
report covers exactly what this system published and nothing else.

Two deliberate limits shape what can be shown:

* YouTube watch time, retention and subscribers gained come from the YouTube
  *Analytics* API, which needs the ``yt-analytics.readonly`` scope. The refresh
  token was minted for upload and Data API access only, so those numbers are
  out of reach until the app is re-authorised. Views, likes and comments come
  from the Data API and are available now.
* Instagram views, reach, saves and shares are insights, which need
  ``instagram_business_manage_insights``. Likes and comments live on the media
  node itself and always work. Insights are attempted and quietly dropped when
  the token lacks the permission, so the report degrades instead of failing.

A missing figure is shown as "—" rather than zero; the two are not the same and
reporting no data as zero views would be a lie.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from .config_loader import find_project_root
from .instagram_uploader import GRAPH_BASE, API_VERSION, InstagramError, _credentials
from .shorts_pipeline import PUBLISHED_FILE

logger = logging.getLogger(__name__)


@dataclass
class VideoReport:
    """What is known about one published short."""

    creature: str
    title: str
    published_at: str
    youtube_url: str = ""
    instagram_url: str = ""
    youtube: dict[str, int] = field(default_factory=dict)
    instagram: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _load_published(root: Path | None = None) -> list[dict]:
    path = (root or find_project_root()) / PUBLISHED_FILE
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except json.JSONDecodeError as exc:
        logger.warning("Unreadable %s (%s)", path, exc)
        return []


# -----------------------------------------------------------------------
# Instagram
# -----------------------------------------------------------------------

def _graph(path: str, params: dict, *, timeout: int = 30) -> dict:
    url = f"{GRAPH_BASE}/{API_VERSION}/{path.lstrip('/')}?{urlencode(params)}"
    try:
        with urlopen(url, timeout=timeout) as response:
            return json.load(response)
    except HTTPError as exc:
        try:
            message = json.loads(exc.read().decode("utf-8", "replace")).get("error", {}).get("message", "")
        except (json.JSONDecodeError, ValueError):
            message = f"HTTP {exc.code}"
        raise InstagramError(message or f"HTTP {exc.code}") from exc
    except URLError as exc:
        raise InstagramError(f"unreachable: {exc.reason}") from exc


def resolve_media_id(permalink: str, token: str) -> str:
    """Find the media id behind a Reel permalink.

    Records published before the id was stored only have the permalink, so the
    account's recent media is scanned to match it.
    """
    user_id, _ = _credentials()
    page = _graph(f"{user_id}/media", {"fields": "id,permalink", "limit": 50, "access_token": token})
    for item in page.get("data", []):
        if str(item.get("permalink", "")).rstrip("/") == permalink.rstrip("/"):
            return str(item.get("id", ""))
    return ""


def instagram_stats(media_id: str, token: str) -> tuple[dict[str, int], list[str]]:
    """Return Instagram figures for one Reel, plus notes on anything missing."""
    stats: dict[str, int] = {}
    notes: list[str] = []

    node = _graph(media_id, {"fields": "like_count,comments_count", "access_token": token})
    for source, target in (("like_count", "likes"), ("comments_count", "comments")):
        if source in node:
            stats[target] = int(node[source])

    try:
        insights = _graph(
            f"{media_id}/insights",
            {"metric": "views,reach,saved,shares", "access_token": token},
        )
        for entry in insights.get("data", []):
            values = entry.get("values") or [{}]
            stats[str(entry.get("name"))] = int(values[0].get("value", 0))
    except InstagramError as exc:
        # Insights need a permission the basic token may not carry. Likes and
        # comments are already in hand, so this is worth a note, not a failure.
        notes.append(f"Instagram görüntülenme verisi alınamadı ({exc})")

    return stats, notes


# -----------------------------------------------------------------------
# Report assembly
# -----------------------------------------------------------------------

def collect(root: Path | None = None, *, limit: int = 10) -> list[VideoReport]:
    """Gather figures for the most recent *limit* published shorts."""
    from .youtube_analytics import get_video_stats

    reports: list[VideoReport] = []
    published = _load_published(root)[-limit:]

    token = ""
    try:
        _, token = _credentials()
    except InstagramError as exc:
        logger.info("Instagram credentials unavailable: %s", exc)

    for record in reversed(published):  # newest first
        report = VideoReport(
            creature=str(record.get("creature", "?")),
            title=str(record.get("title", "")),
            published_at=str(record.get("published_at", "")),
            youtube_url=str(record.get("youtube_url", "")),
            instagram_url=str(record.get("instagram_url", "")),
        )

        video_id = str(record.get("youtube_video_id", ""))
        if video_id:
            try:
                stats = get_video_stats(video_id)
                report.youtube = {
                    "views": stats.get("views", 0),
                    "likes": stats.get("likes", 0),
                    "comments": stats.get("comments", 0),
                }
            except RuntimeError as exc:
                report.notes.append(f"YouTube verisi alınamadı ({exc})")

        media_id = str(record.get("instagram_media_id", ""))
        if token and not media_id and report.instagram_url:
            try:
                media_id = resolve_media_id(report.instagram_url, token)
            except InstagramError as exc:
                report.notes.append(f"Instagram gönderisi bulunamadı ({exc})")
        if token and media_id:
            try:
                report.instagram, notes = instagram_stats(media_id, token)
                report.notes.extend(notes)
            except InstagramError as exc:
                report.notes.append(f"Instagram verisi alınamadı ({exc})")

        reports.append(report)
    return reports


def _num(value: int | None) -> str:
    """Format a figure, distinguishing "no data" from zero."""
    return "—" if value is None else f"{value:,}".replace(",", ".")


def _replay_rate(instagram: dict[str, int]) -> str:
    """Views divided by reach — how many times the average viewer watched.

    This is the only figure so far that separated the Reel that travelled from
    the ones that stalled, so it is worth showing on its own rather than
    leaving the reader to divide two numbers in their head.
    """
    views, reach = instagram.get("views"), instagram.get("reach")
    if not views or not reach:  # a zero reach would divide by zero
        return ""
    return f"{views / reach:.2f}".replace(".", ",")


def format_telegram(reports: list[VideoReport]) -> str:
    """Render the report as the HTML Telegram accepts."""
    if not reports:
        return (
            "📊 <b>Rapor</b>\n\nHenüz yayınlanmış video yok. "
            "Bir video gönderdiğinde burada görünecek."
        )

    stamp = datetime.now(UTC).strftime("%d.%m.%Y %H:%M UTC")
    lines = [f"📊 <b>Unfoldables — Rapor</b>\n<i>{len(reports)} video · {stamp}</i>\n"]

    yt_total = ig_total = ig_shares = 0
    for report in reports:
        lines.append(f"<b>{_escape(report.title or report.creature)}</b>")

        yt = report.youtube
        if yt:
            yt_total += yt.get("views", 0)
            lines.append(
                f"  ▶️ {_num(yt.get('views'))} izlenme · "
                f"{_num(yt.get('likes'))} beğeni · {_num(yt.get('comments'))} yorum"
            )
        else:
            lines.append("  ▶️ —")

        ig = report.instagram
        if ig:
            ig_total += ig.get("views", 0)
            ig_shares += ig.get("shares", 0)
            # Split across two lines: how far it travelled, then how people
            # reacted. Shares lead the second line because they are what
            # actually drives Reels distribution.
            reach = [
                f"{_num(ig[key])} {label}"
                for key, label in (("views", "görüntülenme"), ("reach", "erişim"))
                if key in ig
            ]
            replays = _replay_rate(ig)
            if replays:
                reach.append(f"kişi başı {replays}")
            if reach:
                lines.append("  📸 " + " · ".join(reach))
            reaction = [
                f"{_num(ig[key])} {label}"
                for key, label in (
                    ("shares", "paylaşım"),
                    ("saved", "kayıt"),
                    ("likes", "beğeni"),
                    ("comments", "yorum"),
                )
                if key in ig
            ]
            if reaction:
                lines.append("     " + " · ".join(reaction))
        elif report.instagram_url:
            lines.append("  📸 —")

        for note in report.notes:
            lines.append(f"  <i>⚠️ {_escape(note)}</i>")
        lines.append("")

    total = f"<b>Toplam:</b> {_num(yt_total)} YouTube izlenme · {_num(ig_total)} Instagram görüntülenme"
    if ig_shares:
        total += f" · {_num(ig_shares)} paylaşım"
    lines.append(total)
    return "\n".join(lines)


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def send_report(root: Path | None = None, *, limit: int = 10) -> str:
    """Build the report and send it to Telegram. Returns the message text."""
    from . import notifications

    message = format_telegram(collect(root, limit=limit))
    notifications.send_message(message)
    return message
