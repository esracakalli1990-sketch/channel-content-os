"""Pexels stock media search (semi-automatic: system searches, user selects).

Requires a free Pexels API key: https://www.pexels.com/api/
All Pexels content is free for commercial use (Pexels license).
"""
from __future__ import annotations

import csv
import json
import logging
import os
import ssl
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote_plus
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

PEXELS_PHOTO_URL = "https://api.pexels.com/v1/search"
PEXELS_VIDEO_URL = "https://api.pexels.com/videos/search"

# Pexels sits behind Cloudflare, which rejects urllib's default
# "Python-urllib/x.y" agent with HTTP 403 (Cloudflare error 1010).
# A conventional browser UA is required for both API and CDN requests.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


@lru_cache(maxsize=1)
def _ssl_context() -> ssl.SSLContext:
    """Build an SSL context backed by certifi's CA bundle.

    The Python installer on Windows does not always populate a usable trust
    store, which makes ``urlopen`` fail with CERTIFICATE_VERIFY_FAILED against
    perfectly valid hosts. Prefer certifi when it is installed and fall back to
    the system default otherwise.
    """
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        logger.debug("certifi not installed; using system trust store.")
        return ssl.create_default_context()


def _get_api_key() -> str:
    key = os.getenv("PEXELS_API_KEY", "")
    if not key:
        raise RuntimeError(
            "PEXELS_API_KEY is not set. Get a free key at https://www.pexels.com/api/"
        )
    return key


def _pexels_request(url: str, params: dict) -> dict:
    """Make an authenticated GET request to the Pexels API."""
    api_key = _get_api_key()
    full_url = f"{url}?{urlencode(params)}"
    request = Request(
        full_url,
        headers={
            "Authorization": api_key,
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=30, context=_ssl_context()) as response:
            return json.load(response)
    except HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:  # noqa: BLE001 — diagnostics only
            pass
        raise RuntimeError(f"Pexels API returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach Pexels API: {exc.reason}") from exc


# -----------------------------------------------------------------------
# Photo search
# -----------------------------------------------------------------------

def search_photos(
    query: str,
    *,
    per_page: int = 15,
    page: int = 1,
    orientation: str = "landscape",
) -> list[dict]:
    """Search Pexels for photos.

    Returns a list of dicts with id, description, photographer, url, and
    download links for various sizes.
    """
    payload = _pexels_request(PEXELS_PHOTO_URL, {
        "query": query,
        "per_page": min(per_page, 80),
        "page": page,
        "orientation": orientation,
    })
    results = []
    for photo in payload.get("photos", []):
        results.append({
            "id": photo["id"],
            "type": "photo",
            "description": photo.get("alt", ""),
            "photographer": photo.get("photographer", ""),
            "url": photo.get("url", ""),
            "src_original": photo.get("src", {}).get("original", ""),
            "src_large": photo.get("src", {}).get("large2x", ""),
            "src_medium": photo.get("src", {}).get("medium", ""),
            "license": "Pexels License (free commercial use)",
        })
    logger.info("Pexels photo search '%s': %d results", query, len(results))
    return results


# -----------------------------------------------------------------------
# Video search
# -----------------------------------------------------------------------

def search_videos(
    query: str,
    *,
    per_page: int = 15,
    page: int = 1,
    orientation: str = "landscape",
    size: str = "medium",
) -> list[dict]:
    """Search Pexels for stock videos.

    Returns a list of dicts with id, user, url, and download links.
    """
    payload = _pexels_request(PEXELS_VIDEO_URL, {
        "query": query,
        "per_page": min(per_page, 80),
        "page": page,
        "orientation": orientation,
        "size": size,
    })
    results = []
    for video in payload.get("videos", []):
        # Pick the best quality video file
        files = video.get("video_files", [])
        best = max(files, key=lambda f: f.get("height", 0)) if files else {}
        results.append({
            "id": video["id"],
            "type": "video",
            "description": video.get("url", ""),
            "user": video.get("user", {}).get("name", ""),
            "duration_seconds": video.get("duration", 0),
            "url": video.get("url", ""),
            "download_url": best.get("link", ""),
            "width": best.get("width", 0),
            "height": best.get("height", 0),
            "license": "Pexels License (free commercial use)",
        })
    logger.info("Pexels video search '%s': %d results", query, len(results))
    return results


# -----------------------------------------------------------------------
# Download + asset registration
# -----------------------------------------------------------------------

def download_asset(url: str, destination: Path) -> Path:
    """Download a media file from a URL to the given path."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=60, context=_ssl_context()) as response:
        destination.write_bytes(response.read())
    logger.info("Downloaded asset → %s", destination.name)
    return destination


def register_asset(
    video_directory: Path,
    asset_id: str,
    description: str,
    source_url: str,
    license_text: str = "Pexels License (free commercial use)",
    local_filename: str = "",
) -> None:
    """Add an asset entry to the video's asset_manifest.csv."""
    from .project import VISUALS_DIR

    manifest_path = video_directory / VISUALS_DIR / "asset_manifest.csv"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([
            asset_id,
            description,
            source_url,
            license_text,
            "yes",  # commercial_use_confirmed
            local_filename,
            "downloaded",
        ])
    logger.info("Registered asset %s in manifest", asset_id)


# -----------------------------------------------------------------------
# Autonomous fetch (no user selection — used by auto_produce.py)
# -----------------------------------------------------------------------

def fetch_clips(
    queries: list[str],
    destination_dir: Path,
    *,
    per_query: int = 1,
    max_clips: int = 6,
    max_duration_seconds: int = 30,
    video_directory: Path | None = None,
) -> list[Path]:
    """Search and download stock video clips for *queries*, autonomously.

    For each query the best-resolution clip shorter than *max_duration_seconds*
    is downloaded into *destination_dir*. When *video_directory* is given, each
    downloaded clip is also registered in that video's ``asset_manifest.csv``
    so licensing stays auditable.

    This is best-effort: any failure (missing API key, network error, no
    results) is logged and skipped rather than raised, so the production
    pipeline can fall back to a static background.
    """
    destination_dir.mkdir(parents=True, exist_ok=True)
    downloaded: list[Path] = []
    seen_ids: set[int] = set()

    for query in queries:
        if len(downloaded) >= max_clips:
            break
        try:
            results = search_videos(query, per_page=10)
        except RuntimeError as exc:
            logger.warning("Stock search failed for '%s': %s", query, exc)
            continue

        # Prefer short, high-resolution clips; skip duplicates across queries.
        candidates = [
            r for r in results
            if r["download_url"]
            and r["id"] not in seen_ids
            and 0 < r.get("duration_seconds", 0) <= max_duration_seconds
        ]
        candidates.sort(key=lambda r: r.get("height", 0), reverse=True)

        taken = 0
        for item in candidates:
            if taken >= per_query or len(downloaded) >= max_clips:
                break
            slug = "".join(c if c.isalnum() else "_" for c in query)[:30]
            target = destination_dir / f"stock_{slug}_{item['id']}.mp4"
            try:
                download_asset(item["download_url"], target)
            except Exception as exc:  # noqa: BLE001 — best effort
                logger.warning("Download failed for clip %s: %s", item["id"], exc)
                continue

            seen_ids.add(item["id"])
            downloaded.append(target)
            taken += 1

            if video_directory is not None:
                try:
                    register_asset(
                        video_directory,
                        asset_id=f"pexels-video-{item['id']}",
                        description=f"stock footage for '{query}' by {item['user']}",
                        source_url=item["url"],
                        local_filename=target.name,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Asset registration failed for %s: %s", item["id"], exc)

    logger.info("Fetched %d stock clips for %d queries", len(downloaded), len(queries))
    return downloaded


def fetch_clips_map(
    queries: list[str],
    destination_dir: Path,
    *,
    max_downloads: int = 16,
    max_duration_seconds: int = 40,
    video_directory: Path | None = None,
) -> dict[str, Path]:
    """Download one clip per distinct query and return a ``query -> path`` map.

    Scene planning produces one query per scene, but many scenes repeat a
    subject and a long video would otherwise mean dozens of large downloads.
    Duplicate queries are collapsed and *max_downloads* caps the total, so the
    caller can reuse clips for the scenes left unmapped.

    Best-effort: queries that yield nothing are simply absent from the result.
    """
    destination_dir.mkdir(parents=True, exist_ok=True)

    unique: list[str] = []
    for query in queries:
        normalized = query.strip()
        if normalized and normalized.lower() not in {u.lower() for u in unique}:
            unique.append(normalized)

    # Queries arrive in narration order. Taking the first N would leave the
    # whole back half of the video without a matching clip, so sample evenly
    # across the timeline instead — matched scenes stay spread throughout.
    if len(unique) > max_downloads:
        step = len(unique) / max_downloads
        selected = [unique[int(i * step)] for i in range(max_downloads)]
    else:
        selected = unique

    mapping: dict[str, Path] = {}
    seen_ids: set[int] = set()

    for query in selected:
        try:
            results = search_videos(query, per_page=10)
        except RuntimeError as exc:
            logger.warning("Stock search failed for '%s': %s", query, exc)
            continue

        candidates = [
            r for r in results
            if r["download_url"]
            and r["id"] not in seen_ids
            and 0 < r.get("duration_seconds", 0) <= max_duration_seconds
        ]
        candidates.sort(key=lambda r: r.get("height", 0), reverse=True)
        if not candidates:
            logger.info("No usable clip for query '%s'", query)
            continue

        item = candidates[0]
        slug = "".join(c if c.isalnum() else "_" for c in query)[:30]
        target = destination_dir / f"stock_{slug}_{item['id']}.mp4"
        try:
            download_asset(item["download_url"], target)
        except Exception as exc:  # noqa: BLE001 — best effort
            logger.warning("Download failed for '%s': %s", query, exc)
            continue

        seen_ids.add(item["id"])
        mapping[query] = target

        if video_directory is not None:
            try:
                register_asset(
                    video_directory,
                    asset_id=f"pexels-video-{item['id']}",
                    description=f"stock footage for '{query}' by {item['user']}",
                    source_url=item["url"],
                    local_filename=target.name,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Asset registration failed: %s", exc)

    logger.info(
        "Downloaded %d clips for %d distinct queries (%d requested)",
        len(mapping), len(unique), len(selected),
    )
    return mapping


# -----------------------------------------------------------------------
# Display helper (for CLI user selection)
# -----------------------------------------------------------------------

def format_results_for_selection(results: list[dict]) -> str:
    """Format search results for terminal display so user can pick items."""
    lines = []
    for i, item in enumerate(results, 1):
        if item["type"] == "photo":
            lines.append(
                f"  [{i}] 📷  {item['description'][:60]}"
                f"  by {item['photographer']}"
                f"  → {item['url']}"
            )
        else:
            lines.append(
                f"  [{i}] 🎬  {item['duration_seconds']}s  {item['width']}x{item['height']}"
                f"  by {item['user']}"
                f"  → {item['url']}"
            )
    return "\n".join(lines)
