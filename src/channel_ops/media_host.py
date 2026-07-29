"""Temporary public hosting for clips, backed by GitHub Releases.

Instagram will not accept an uploaded file — it fetches the video from a URL
itself, so the clip has to be publicly reachable for the length of the publish
call. Nothing else in this project needs a server, and adding one for a file
that matters for two minutes is not worth it.

Release assets on a public repository are served without authentication, and
uploading one needs no more than the ``GITHUB_TOKEN`` the workflow already
has. Assets live under a single reusable tag and are deleted as soon as
Instagram has taken the file, so the release stays empty between runs and git
history never sees the video.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
GITHUB_UPLOADS = "https://uploads.github.com"

# One release holds every staged clip; it is created once and reused.
STAGING_TAG = "media-staging"
STAGING_NAME = "Geçici medya alanı"
STAGING_BODY = (
    "Bu sürüm, Instagram'a gönderilen videoların geçici olarak barındırıldığı yerdir. "
    "Instagram videoyu bir adresten kendisi indirdiği için gereklidir. "
    "Dosyalar yükleme biter bitmez silinir — burası normalde boştur. Elle dosya eklemeyin."
)


class HostingError(RuntimeError):
    """Raised when a clip cannot be staged or removed."""


@dataclass(frozen=True)
class StagedFile:
    """A clip currently reachable at a public URL."""

    asset_id: int
    url: str


def _context() -> tuple[str, str]:
    """Return (token, ``owner/repo``) from the workflow environment."""
    token = os.getenv("GITHUB_TOKEN", "").strip()
    repository = os.getenv("GITHUB_REPOSITORY", "").strip()
    missing = [
        name
        for name, value in (("GITHUB_TOKEN", token), ("GITHUB_REPOSITORY", repository))
        if not value
    ]
    if missing:
        raise HostingError(
            f"Missing {', '.join(missing)}. These are provided automatically inside a "
            "GitHub Actions run; staging a clip only works from a workflow."
        )
    return token, repository


def _request(url: str, token: str, *, method: str = "GET", data: bytes | None = None,
             content_type: str | None = None, timeout: int = 300) -> dict | None:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if content_type:
        headers["Content-Type"] = content_type

    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read()
            return json.loads(body) if body else None
    except HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:  # noqa: BLE001 - the original status still matters
            pass
        raise HostingError(f"GitHub API {method} {url} failed (HTTP {exc.code}): {detail}") from exc
    except URLError as exc:
        raise HostingError(f"Could not reach GitHub: {exc.reason}") from exc


def _staging_release_id(token: str, repository: str) -> int:
    """Return the staging release, creating it the first time."""
    try:
        release = _request(f"{GITHUB_API}/repos/{repository}/releases/tags/{STAGING_TAG}", token)
        if release:
            return int(release["id"])
    except HostingError as exc:
        if "HTTP 404" not in str(exc):
            raise

    created = _request(
        f"{GITHUB_API}/repos/{repository}/releases",
        token,
        method="POST",
        data=json.dumps(
            {
                "tag_name": STAGING_TAG,
                "name": STAGING_NAME,
                "body": STAGING_BODY,
                "prerelease": True,
            }
        ).encode("utf-8"),
        content_type="application/json",
    )
    if not created:
        raise HostingError("GitHub did not return the staging release it created.")
    logger.info("Created the staging release")
    return int(created["id"])


def stage(video_path: Path, *, name: str | None = None) -> StagedFile:
    """Upload *video_path* and return a publicly reachable URL for it."""
    token, repository = _context()
    release_id = _staging_release_id(token, repository)

    asset_name = name or video_path.name
    mime = mimetypes.guess_type(asset_name)[0] or "application/octet-stream"
    params = urlencode({"name": asset_name})

    # An asset left behind by a failed run would collide on name.
    _remove_conflicting(token, repository, release_id, asset_name)

    uploaded = _request(
        f"{GITHUB_UPLOADS}/repos/{repository}/releases/{release_id}/assets?{params}",
        token,
        method="POST",
        data=video_path.read_bytes(),
        content_type=mime,
    )
    if not uploaded:
        raise HostingError("GitHub did not return the uploaded asset.")

    # browser_download_url needs no credentials on a public repository, which is
    # exactly what Instagram requires.
    url = uploaded.get("browser_download_url") or (
        f"https://github.com/{repository}/releases/download/{STAGING_TAG}/{quote(asset_name)}"
    )
    logger.info("Staged %s at %s", asset_name, url)
    return StagedFile(asset_id=int(uploaded["id"]), url=url)


def _remove_conflicting(token: str, repository: str, release_id: int, asset_name: str) -> None:
    assets = _request(f"{GITHUB_API}/repos/{repository}/releases/{release_id}/assets", token) or []
    for asset in assets:
        if asset.get("name") == asset_name:
            logger.info("Removing a leftover asset named %s", asset_name)
            unstage(StagedFile(asset_id=int(asset["id"]), url=""))


def unstage(staged: StagedFile) -> None:
    """Delete a staged clip. Never raises — the upload already succeeded."""
    try:
        token, repository = _context()
        _request(
            f"{GITHUB_API}/repos/{repository}/releases/assets/{staged.asset_id}",
            token,
            method="DELETE",
            timeout=60,
        )
        logger.info("Removed staged asset %d", staged.asset_id)
    except HostingError as exc:
        # A leftover asset costs a little storage and is cleaned up on the next
        # run; failing here would wrongly report a successful publish as failed.
        logger.warning("Could not remove staged asset %d: %s", staged.asset_id, exc)
