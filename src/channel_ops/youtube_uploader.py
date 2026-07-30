"""YouTube Data API v3 integration for private upload and status management.

Handles video upload (always private first), metadata management, and publish
status transitions. Access tokens come from :mod:`channel_ops.youtube_auth`,
which exchanges the long-lived refresh token on demand.

Setup lives in SETUP_GUIDE.md sections 4 and 5.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .youtube_auth import get_access_token

logger = logging.getLogger(__name__)

YOUTUBE_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
YOUTUBE_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"

# YouTube treats a video as a Short when it is vertical and short enough; there
# is no API flag for it. These are the limits to stay inside.
SHORTS_MAX_SECONDS = 180
SHORTS_TAG = "#Shorts"

PRIVACY_STATUSES = frozenset({"private", "unlisted", "public"})


def upload_video(
    video_path: Path,
    title: str,
    description: str,
    *,
    tags: list[str] | None = None,
    category_id: str = "28",  # Science & Technology
    privacy: str = "private",
    made_for_kids: bool = False,
) -> dict:
    """Upload a video to YouTube.

    Parameters
    ----------
    video_path:
        Path to the video file (.mp4).
    title:
        Video title (max 100 chars).
    description:
        Video description (max 5000 chars).
    tags:
        List of keyword tags.
    category_id:
        YouTube category ID. 28 = Science & Technology.
    privacy:
        ``"private"``, ``"unlisted"`` or ``"public"``. Defaults to private, but
        the caller's choice is honoured: an earlier version quietly rewrote
        "public" to "private", which meant a caller asking to publish got no
        error and no published video.

    Returns
    -------
    dict
        YouTube API response with video ID and status.
    """
    if privacy not in PRIVACY_STATUSES:
        raise ValueError(
            f"Unknown privacy status {privacy!r}. Expected one of: {', '.join(sorted(PRIVACY_STATUSES))}"
        )

    token = get_access_token()

    metadata = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": tags or [],
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": made_for_kids,
        },
    }

    # Simple resumable upload
    params = urlencode({
        "uploadType": "multipart",
        "part": "snippet,status",
    })
    url = f"{YOUTUBE_UPLOAD_URL}?{params}"

    # Build multipart body
    boundary = "----ChannelOSUploadBoundary"
    video_data = video_path.read_bytes()
    metadata_json = json.dumps(metadata).encode("utf-8")

    body = (
        f"--{boundary}\r\n"
        f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
    ).encode("utf-8")
    body += metadata_json
    body += f"\r\n--{boundary}\r\n".encode("utf-8")
    body += f"Content-Type: video/mp4\r\n\r\n".encode("utf-8")
    body += video_data
    body += f"\r\n--{boundary}--".encode("utf-8")

    request = Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/related; boundary={boundary}",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=600) as response:
            result = json.load(response)
    except HTTPError as exc:
        error_body = ""
        try:
            error_body = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        raise RuntimeError(f"YouTube upload failed (HTTP {exc.code}): {error_body}") from exc

    video_id = result.get("id", "")
    logger.info("Video uploaded: https://youtube.com/watch?v=%s (private)", video_id)
    return result


def publish_video(youtube_video_id: str) -> dict:
    """Change a private video to public (requires human approval before calling)."""
    token = get_access_token()
    params = urlencode({"part": "status"})
    url = f"{YOUTUBE_VIDEOS_URL}?{params}"

    body = json.dumps({
        "id": youtube_video_id,
        "status": {"privacyStatus": "public"},
    }).encode("utf-8")

    request = Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="PUT",
    )

    try:
        with urlopen(request, timeout=30) as response:
            result = json.load(response)
    except HTTPError as exc:
        raise RuntimeError(f"YouTube publish failed (HTTP {exc.code})") from exc

    logger.info("Video published: https://youtube.com/watch?v=%s", youtube_video_id)
    return result


def set_thumbnail(youtube_video_id: str, thumbnail_path: Path) -> dict:
    """Upload a custom thumbnail for a video."""
    token = get_access_token()
    url = f"https://www.googleapis.com/upload/youtube/v3/thumbnails/set?videoId={youtube_video_id}"

    image_data = thumbnail_path.read_bytes()
    request = Request(
        url,
        data=image_data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "image/png",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as exc:
        raise RuntimeError(f"Thumbnail upload failed (HTTP {exc.code})") from exc


def save_upload_record(video_directory: Path, upload_result: dict) -> Path:
    """Save the YouTube upload response to the video's youtube directory."""
    from .project import YOUTUBE_DIR

    youtube_dir = video_directory / YOUTUBE_DIR
    youtube_dir.mkdir(parents=True, exist_ok=True)
    record_path = youtube_dir / "upload_record.json"
    record = {
        "youtube_video_id": upload_result.get("id", ""),
        "uploaded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "privacy": upload_result.get("status", {}).get("privacyStatus", ""),
        "url": f"https://youtube.com/watch?v={upload_result.get('id', '')}",
        "raw_response": upload_result,
    }
    record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Upload record saved: %s", record_path)
    return record_path
