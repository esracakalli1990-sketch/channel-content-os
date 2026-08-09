"""Performance reports for the shorts published to YouTube and Instagram.

Everything reported here comes from ``data/shorts_published.json``, so the
report covers exactly what this system published and nothing else.

Two deliberate limits shape what can be shown:

* YouTube retention, subscribers gained and the traffic mix come from the
  YouTube *Analytics* API, which needs the ``yt-analytics.readonly`` scope. A
  refresh token minted without it still uploads fine, so the report shows those
  lines when the scope is there and says so once when it is not. Views, likes
  and comments come from the Data API and always work.
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
    # Retention and traffic mix, from the Analytics API. Empty when the token
    # has no yt-analytics.readonly scope, which is not the same as zero.
    retention: dict[str, float] = field(default_factory=dict)
    traffic: dict[str, int] = field(default_factory=dict)
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
    from .youtube_analytics import (
        AnalyticsScopeMissing,
        get_traffic_sources,
        get_video_retention,
        get_video_stats,
    )

    reports: list[VideoReport] = []
    published = _load_published(root)[-limit:]

    # One missing scope would otherwise produce the same warning on every
    # video. It is reported once and then not retried.
    analytics_available = True
    scope_note = ""

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
        if record.get("youtube_deleted"):
            # Deleted from the channel by hand. Querying it returns nothing,
            # which the report would otherwise draw as a bare "—" — the same
            # thing it draws when the API fails, so the reader could not tell
            # a removed video from a broken one.
            report.notes.append("YouTube'dan silindi (Instagram'da duruyor)")
        elif video_id:
            try:
                stats = get_video_stats(video_id)
                report.youtube = {
                    "views": stats.get("views", 0),
                    "likes": stats.get("likes", 0),
                    "comments": stats.get("comments", 0),
                }
            except RuntimeError as exc:
                report.notes.append(f"YouTube verisi alınamadı ({exc})")

            if analytics_available:
                try:
                    report.retention = get_video_retention(video_id)
                    report.traffic = get_traffic_sources(video_id)
                except AnalyticsScopeMissing as exc:
                    analytics_available = False
                    scope_note = str(exc)
                except RuntimeError as exc:
                    report.notes.append(f"Tutulma verisi alınamadı ({exc})")

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

    if scope_note and reports:
        reports[0].notes.append(scope_note)
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


# The API's traffic source codes, in the words the report is read in. Anything
# not listed is shown as its raw code rather than dropped — an unexpected
# source is exactly the thing worth noticing.
TRAFFIC_LABELS = {
    "SHORTS": "Shorts akışı",
    "SUBSCRIBER": "abonelik",
    "NO_LINK_OTHER": "doğrudan",
    "NO_LINK_EMBEDDED": "gömülü",
    "YT_SEARCH": "arama",
    "RELATED_VIDEO": "önerilen",
    "YT_CHANNEL": "kanal sayfası",
    "PLAYLIST": "oynatma listesi",
    "NOTIFICATION": "bildirim",
    "EXT_URL": "dış bağlantı",
}


def _duration(seconds: float | None) -> str:
    """Seconds as m:ss — the form YouTube Studio shows them in."""
    if not seconds:
        return ""
    total = int(round(seconds))
    return f"{total // 60}:{total % 60:02d}"


def _retention_line(retention: dict[str, float]) -> str:
    """How much of the clip was watched, and what it earned.

    On Shorts ``averageViewPercentage`` counts replays, so it routinely exceeds
    100% — the real figures here run to 261%. Reading that as "261% retention"
    is nonsense, so anything over a full watch is shown as a loop multiplier
    instead, which is what it actually measures.
    """
    parts: list[str] = []
    percentage = retention.get("averageViewPercentage")
    if percentage and percentage >= 100:
        parts.append(f"{percentage / 100:.2f}× izlendi".replace(".", ","))
    elif percentage:
        parts.append(f"%{percentage:.0f} izlendi")
    watched = _duration(retention.get("averageViewDuration"))
    if watched:
        parts.append(f"{watched} ort. izlenme")
    gained = int(retention.get("subscribersGained", 0))
    if gained:
        parts.append(f"+{gained} abone")
    shares = int(retention.get("shares", 0))
    if shares:
        parts.append(f"{_num(shares)} paylaşım")
    return "  🎯 " + " · ".join(parts) if parts else ""


def _traffic_line(traffic: dict[str, int]) -> str:
    """Where the views came from, as a share of the total.

    Only the top three are shown; the tail is noise on a Short whose views are
    almost always dominated by one source.
    """
    total = sum(traffic.values())
    if not total:
        return ""
    top = sorted(traffic.items(), key=lambda item: item[1], reverse=True)[:3]
    parts = [
        f"{TRAFFIC_LABELS.get(source, source)} %{views / total * 100:.0f}"
        for source, views in top
        if views / total >= 0.03  # below this it is rounding, not a source
    ]
    return "  🔀 " + " · ".join(parts) if parts else ""


# A video YouTube declines to distribute looks exactly like a healthy one in
# the publish log — it is only visible next to its neighbours. This has now
# happened twice and both times a human spotted it days later, so the report
# does the comparison itself.
STARVED_SHARE = 0.15
STARVED_AFTER_HOURS = 24
# Below this the channel is too small for "far under the median" to mean
# anything, and the warning would fire on ordinary variation.
STARVED_MIN_MEDIAN = 200


def _hours_since(stamp: str) -> float | None:
    try:
        return (datetime.now(UTC) - datetime.fromisoformat(stamp)).total_seconds() / 3600
    except ValueError:
        return None


def _median_views(reports: list[VideoReport]) -> float:
    """Median YouTube views among videos old enough to have been distributed."""
    counts = sorted(
        report.youtube["views"]
        for report in reports
        if report.youtube.get("views") is not None
        and (_hours_since(report.published_at) or 0) >= STARVED_AFTER_HOURS
    )
    if not counts:
        return 0.0
    middle = len(counts) // 2
    if len(counts) % 2:
        return float(counts[middle])
    return (counts[middle - 1] + counts[middle]) / 2


def _distribution_warning(report: VideoReport, median: float) -> str:
    """Flag a video the Shorts feed appears to have skipped.

    Only fires once the video has had a day to be picked up, so a clip
    published minutes ago is never called dead.
    """
    if median < STARVED_MIN_MEDIAN:
        return ""
    age = _hours_since(report.published_at)
    views = report.youtube.get("views")
    if age is None or age < STARVED_AFTER_HOURS or views is None:
        return ""
    if views >= median * STARVED_SHARE:
        return ""
    return (
        f"  ⚠️ <b>YouTube dağıtmadı</b> — {_num(views)} izlenme, "
        f"benzerlerinin ortancası {_num(int(median))}"
    )


def format_telegram(reports: list[VideoReport]) -> str:
    """Render the report as the HTML Telegram accepts."""
    if not reports:
        return (
            "📊 <b>Rapor</b>\n\nHenüz yayınlanmış video yok. "
            "Bir video gönderdiğinde burada görünecek."
        )

    stamp = datetime.now(UTC).strftime("%d.%m.%Y %H:%M UTC")
    lines = [f"📊 <b>Unfoldables — Rapor</b>\n<i>{len(reports)} video · {stamp}</i>\n"]

    median = _median_views(reports)
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

        for extra in (
            _distribution_warning(report, median),
            _retention_line(report.retention),
            _traffic_line(report.traffic),
        ):
            if extra:
                lines.append(extra)

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
            # Instagram has reported shares as exactly 0 on every video so far,
            # including one with 31k views and 100 likes, while YouTube reports
            # real shares for the same clips. The metric is almost certainly
            # not being populated, so a zero is hidden rather than printed as
            # though it were measured.
            reaction = [
                f"{_num(ig[key])} {label}"
                for key, label in (
                    ("shares", "paylaşım"),
                    ("saved", "kayıt"),
                    ("likes", "beğeni"),
                    ("comments", "yorum"),
                )
                if key in ig and not (key == "shares" and not ig[key])
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


# Telegram rejects a sendMessage body over 4096 characters with HTTP 400 and
# no explanation. The report crossed it at 15 videos and the weekly report
# simply stopped arriving. Split a little under the limit to leave room for
# the entity indices Telegram counts differently.
TELEGRAM_MESSAGE_LIMIT = 3900


def split_for_telegram(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Break *text* into messages Telegram will accept.

    Splits between lines only. Every line in the report closes its own HTML
    tags, so no chunk can end mid-tag — which Telegram would also reject.
    """
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for line in text.split("\n"):
        # +1 for the newline that will rejoin it.
        if current and length + len(line) + 1 > limit:
            chunks.append("\n".join(current).strip())
            current, length = [], 0
        current.append(line)
        length += len(line) + 1
    if current:
        chunks.append("\n".join(current).strip())
    return [chunk for chunk in chunks if chunk]


def send_report(root: Path | None = None, *, limit: int = 10) -> str:
    """Build the report and send it to Telegram. Returns the full message text."""
    from . import notifications

    message = format_telegram(collect(root, limit=limit))
    parts = split_for_telegram(message)
    for number, part in enumerate(parts, start=1):
        suffix = f"\n\n<i>({number}/{len(parts)})</i>" if len(parts) > 1 else ""
        notifications.send_message(part + suffix)
    return message
