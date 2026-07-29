"""OAuth access tokens for the YouTube Data API.

Google's access tokens last an hour, which is shorter than the gap between
scheduled runs, so nothing durable can be stored. The refresh token is the
durable credential: it lives in the ``YOUTUBE_REFRESH_TOKEN`` secret and is
exchanged for a fresh access token whenever one is needed.

Both the uploader and the analytics module read tokens through here so the
exchange only has to be right in one place.

To mint the refresh token, see SETUP_GUIDE.md — the flow runs entirely in a
browser via Google's OAuth Playground, since this project is operated from a
phone with no terminal.
"""
from __future__ import annotations

import json
import logging
import os
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

# Refresh a little early so a token cannot expire mid-upload.
EXPIRY_MARGIN_SECONDS = 120

# (access_token, expires_at) reused across calls within one process.
_cached: tuple[str, float] | None = None


class AuthError(RuntimeError):
    """Raised when YouTube credentials are missing or no longer valid."""


def _credentials() -> tuple[str, str, str]:
    client_id = os.getenv("YOUTUBE_CLIENT_ID", "").strip()
    client_secret = os.getenv("YOUTUBE_CLIENT_SECRET", "").strip()
    refresh_token = os.getenv("YOUTUBE_REFRESH_TOKEN", "").strip()

    missing = [
        name
        for name, value in (
            ("YOUTUBE_CLIENT_ID", client_id),
            ("YOUTUBE_CLIENT_SECRET", client_secret),
            ("YOUTUBE_REFRESH_TOKEN", refresh_token),
        )
        if not value
    ]
    if missing:
        raise AuthError(
            f"Missing YouTube credentials: {', '.join(missing)}. "
            "Add them under Settings > Secrets and variables > Actions. "
            "See SETUP_GUIDE.md sections 4 and 5."
        )
    return client_id, client_secret, refresh_token


def get_access_token(*, force_refresh: bool = False) -> str:
    """Return a valid access token, exchanging the refresh token as needed."""
    global _cached

    if _cached and not force_refresh:
        token, expires_at = _cached
        if time.time() < expires_at:
            return token

    client_id, client_secret, refresh_token = _credentials()
    body = urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
    ).encode("utf-8")

    request = Request(
        GOOGLE_TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except HTTPError as exc:
        raise _describe_failure(exc) from exc
    except URLError as exc:
        raise AuthError(f"Could not reach Google's token endpoint: {exc.reason}") from exc

    token = payload.get("access_token", "")
    if not token:
        raise AuthError("Google's token response contained no access_token.")

    expires_in = int(payload.get("expires_in", 3600))
    _cached = (token, time.time() + expires_in - EXPIRY_MARGIN_SECONDS)
    logger.info("Obtained a YouTube access token (valid for %d seconds)", expires_in)
    return token


def _describe_failure(exc: HTTPError) -> AuthError:
    """Turn Google's terse OAuth errors into something actionable.

    ``invalid_grant`` is the one worth spelling out: the most common cause here
    is an OAuth consent screen still in Testing, which revokes refresh tokens
    after seven days. The system then stops with no other symptom.
    """
    detail = ""
    error_code = ""
    try:
        payload = json.loads(exc.read().decode("utf-8", errors="replace"))
        error_code = payload.get("error", "")
        detail = payload.get("error_description", "") or error_code
    except (json.JSONDecodeError, ValueError):
        detail = f"HTTP {exc.code}"

    if error_code == "invalid_grant":
        return AuthError(
            "YouTube refused the refresh token (invalid_grant). It has been revoked or "
            "has expired. The usual cause is the OAuth consent screen still being in "
            "'Testing', which expires refresh tokens after 7 days — publish the app to "
            "production, then mint a new refresh token and update YOUTUBE_REFRESH_TOKEN. "
            "See SETUP_GUIDE.md sections 4.4 and 5."
        )
    if error_code in {"invalid_client", "unauthorized_client"}:
        return AuthError(
            "YouTube rejected the client credentials. Check that YOUTUBE_CLIENT_ID and "
            f"YOUTUBE_CLIENT_SECRET match the OAuth client you created ({detail})."
        )
    return AuthError(f"Could not refresh the YouTube access token: {detail}")


def reset_cache() -> None:
    """Drop the in-process token cache (used by tests)."""
    global _cached
    _cached = None
