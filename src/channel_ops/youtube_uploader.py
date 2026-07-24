"""YouTube Data API v3 integration for private upload and status management.

Handles OAuth 2.0 authentication, video upload (always private first),
metadata management, and publish status transitions.

Setup:
1. Create a project at https://console.cloud.google.com
2. Enable YouTube Data API v3
3. Create OAuth 2.0 credentials (Desktop app)
4. Download client_secret.json
5. Set YOUTUBE_CLIENT_SECRETS_PATH in .env
"""
from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

YOUTUBE_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
YOUTUBE_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"


def _load_token() -> str:
    """Load the OAuth access token from the token file."""
    token_path = os.getenv("YOUTUBE_TOKEN_PATH", "")
    if not token_path or not Path(token_path).exists():
        raise RuntimeError(
            "YouTube OAuth token not found. Run the auth flow first:\n"
            "  channel-os youtube-auth\n"
            "Set YOUTUBE_TOKEN_PATH in .env to point to the token file."
        )
    data = json.loads(Path(token_path).read_text(encoding="utf-8"))
    return data.get("access_token", "")


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
    """Upload a video to YouTube (always as private initially).

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
        Privacy status: ``"private"``, ``"unlisted"``, or ``"public"``.
        Forced to ``"private"`` for safety.

    Returns
    -------
    dict
        YouTube API response with video ID and status.
    """
    # Safety: always upload as private first
    if privacy == "public":
        logger.warning("Forcing private upload — use publish_video() after review.")
        privacy = "private"

    token = _load_token()

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
    token = _load_token()
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
    token = _load_token()
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
