"""Publish Reels through the Instagram API with Instagram Login.

This is the ``graph.instagram.com`` flow, not the Facebook-login one on
``graph.facebook.com``: the account was connected with "Add account" in the
app's Instagram product, which issues an Instagram-scoped token and needs no
Facebook Page. The two flows use different hosts and are not interchangeable.

Publishing is three steps, not one. Instagram will not accept the file from us
— it fetches the video itself, so the clip must already be reachable at a
public URL. We hand over that URL, poll until Instagram has finished
transcoding, and only then publish the container.

Tokens last 60 days. :func:`refresh_token` trades the current one for a
replacement good for another sixty, which the weekly ``instagram-token`` job
writes straight back into the repository secret -- so in normal running the
deadline never arrives. Two cases still need a person: an already-expired token
cannot be refreshed, and neither can one under a day old.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.instagram.com"
API_VERSION = "v21.0"

# Instagram transcodes before it will publish; large clips take a while.
CONTAINER_POLL_SECONDS = 5
CONTAINER_TIMEOUT_SECONDS = 300

# Warn once the token has less than this left of its 60 days.
#
# Fourteen, not seven: the only job that checks runs weekly, and a seven-day
# window checked every seven days can fall entirely between two runs. That is
# not a hypothetical -- the token expired on 27 September without a single
# warning going out. Two weeks guarantees at least one, usually two.
TOKEN_WARNING_DAYS = 14


class InstagramError(RuntimeError):
    """Raised when Instagram rejects a request or credentials are missing."""


@dataclass(frozen=True)
class ReelResult:
    """A published Reel."""

    media_id: str
    permalink: str


def _credentials() -> tuple[str, str]:
    user_id = os.getenv("IG_USER_ID", "").strip()
    token = os.getenv("IG_ACCESS_TOKEN", "").strip()
    missing = [
        name
        for name, value in (("IG_USER_ID", user_id), ("IG_ACCESS_TOKEN", token))
        if not value
    ]
    if missing:
        raise InstagramError(
            f"Missing Instagram credentials: {', '.join(missing)}. "
            "See SETUP_GUIDE.md section 8."
        )
    return user_id, token


def _call(path: str, params: dict, *, method: str = "GET", timeout: int = 60) -> dict:
    """Call the Instagram Graph API and return the decoded payload."""
    url = f"{GRAPH_BASE}/{API_VERSION}/{path.lstrip('/')}"
    encoded = urlencode({k: v for k, v in params.items() if v is not None})

    if method == "POST":
        request = Request(
            url,
            data=encoded.encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
    else:
        request = Request(f"{url}?{encoded}")

    try:
        with urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except HTTPError as exc:
        raise InstagramError(_describe(exc)) from exc
    except URLError as exc:
        raise InstagramError(f"Could not reach Instagram: {exc.reason}") from exc


def _describe(exc: HTTPError) -> str:
    """Surface Instagram's own error message instead of a bare status code."""
    try:
        error = json.loads(exc.read().decode("utf-8", errors="replace")).get("error", {})
    except (json.JSONDecodeError, ValueError):
        return f"Instagram returned HTTP {exc.code}."

    message = error.get("message", "")
    code = error.get("code")

    if code == 190:
        return (
            f"Instagram rejected the access token ({message}). Long-lived tokens expire "
            "after 60 days — generate a new one in the app dashboard and update the "
            "IG_ACCESS_TOKEN secret."
        )
    return f"Instagram error {code or exc.code}: {message or 'no detail given'}"


# -----------------------------------------------------------------------
# Token lifetime
# -----------------------------------------------------------------------

def refresh_token() -> tuple[str, int]:
    """Refresh the access token and return ``(new_token, days_valid)``.

    The endpoint both reports the remaining lifetime and issues a replacement
    token good for another sixty days. Whoever calls this must store the new
    value: the old one is not extended, so a refresh whose result is thrown
    away buys nothing.

    Two things Instagram requires and neither is recoverable here: the token
    must be at least twenty-four hours old, and it must not have expired yet.
    Once it is dead no amount of refreshing brings it back and a human has to
    issue a new one from the app dashboard.
    """
    _, token = _credentials()
    url = f"{GRAPH_BASE}/refresh_access_token?" + urlencode(
        {"grant_type": "ig_refresh_token", "access_token": token}
    )
    try:
        with urlopen(url, timeout=30) as response:
            payload = json.load(response)
    except HTTPError as exc:
        raise InstagramError(_describe(exc)) from exc
    except URLError as exc:
        raise InstagramError(f"Could not reach Instagram: {exc.reason}") from exc

    new_token = str(payload.get("access_token", "")).strip()
    days = int(payload.get("expires_in", 0)) // 86400
    if not new_token:
        raise InstagramError(
            f"Instagram reported {days} day(s) left but returned no new token."
        )
    return new_token, days


def token_days_remaining() -> int:
    """Return the days left on the access token."""
    return refresh_token()[1]


def token_warning() -> str | None:
    """Return a message when the token is close to expiring, else ``None``."""
    try:
        days = token_days_remaining()
    except InstagramError as exc:
        return f"⚠️ Instagram token kontrol edilemedi: {exc}"

    if days <= TOKEN_WARNING_DAYS:
        return (
            f"⚠️ <b>Instagram token'ı {days} gün sonra doluyor.</b>\n"
            f"Meta panelinden yeni token üret ve <code>IG_ACCESS_TOKEN</code> "
            f"secret'ını güncelle, yoksa Instagram paylaşımları durur."
        )
    logger.info("Instagram token has %d day(s) left", days)
    return None


# -----------------------------------------------------------------------
# Publishing
# -----------------------------------------------------------------------

def create_reel_container(video_url: str, caption: str) -> str:
    """Ask Instagram to fetch *video_url*, and return the container id."""
    user_id, token = _credentials()
    payload = _call(
        f"{user_id}/media",
        {
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "access_token": token,
        },
        method="POST",
    )
    container_id = payload.get("id", "")
    if not container_id:
        raise InstagramError("Instagram accepted the request but returned no container id.")
    logger.info("Created Reel container %s", container_id)
    return container_id


def wait_for_container(container_id: str, *, timeout: int = CONTAINER_TIMEOUT_SECONDS) -> None:
    """Block until Instagram has finished transcoding, or fail with the reason."""
    _, token = _credentials()
    deadline = time.time() + timeout

    while time.time() < deadline:
        payload = _call(container_id, {"fields": "status_code,status", "access_token": token})
        status = payload.get("status_code", "")

        if status == "FINISHED":
            logger.info("Container %s is ready", container_id)
            return
        if status in {"ERROR", "EXPIRED"}:
            raise InstagramError(
                f"Instagram could not process the video ({status}): "
                f"{payload.get('status', 'no detail given')}"
            )
        time.sleep(CONTAINER_POLL_SECONDS)

    raise InstagramError(
        f"Instagram was still processing the video after {timeout}s. The clip may be "
        "too large or too long for a Reel."
    )


def publish_reel(video_url: str, caption: str) -> ReelResult:
    """Publish a Reel from a publicly reachable *video_url*.

    Instagram downloads the file itself, so the URL has to be reachable
    without authentication for the duration of the call.
    """
    user_id, token = _credentials()

    container_id = create_reel_container(video_url, caption)
    wait_for_container(container_id)

    published = _call(
        f"{user_id}/media_publish",
        {"creation_id": container_id, "access_token": token},
        method="POST",
    )
    media_id = published.get("id", "")
    if not media_id:
        raise InstagramError("Instagram published the container but returned no media id.")

    permalink = ""
    try:
        permalink = _call(media_id, {"fields": "permalink", "access_token": token}).get(
            "permalink", ""
        )
    except InstagramError as exc:
        # The Reel is live; only the link lookup failed, which is not worth
        # failing the whole publish over.
        logger.warning("Published %s but could not fetch its permalink: %s", media_id, exc)

    logger.info("Published Reel %s", media_id)
    return ReelResult(media_id=media_id, permalink=permalink)
