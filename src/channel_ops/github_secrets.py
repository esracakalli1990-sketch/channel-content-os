"""Write a value back into a GitHub Actions secret.

Used to keep the Instagram token alive without anyone touching the settings
page. Instagram's refresh endpoint hands back a *replacement* token rather than
extending the old one, so a refresh is only worth doing if the new value is
stored somewhere the next run will read it.

WHY A SEPARATE TOKEN IS UNAVOIDABLE. The ``GITHUB_TOKEN`` that Actions injects
into every run cannot write Actions secrets -- no permission setting grants it
that. Updating a secret needs a personal access token, which is why this asks
for ``GH_SECRET_TOKEN`` and says so plainly when it is missing.

WHY THE VALUE IS NEVER WRITTEN TO A FILE. This repository is public. A token in
a commit is a published credential, and a later deletion does not unpublish it.
The only destination here is the secrets API.

Secrets must arrive encrypted: GitHub publishes a per-repository public key and
expects a libsodium sealed box, which is what PyNaCl provides. Without PyNaCl
installed this raises rather than falling back to anything weaker.
"""
from __future__ import annotations

import json
import logging
import os
from base64 import b64encode
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

API = "https://api.github.com"


class SecretWriteError(RuntimeError):
    """Raised when the secret could not be updated."""


def _token() -> str:
    token = os.getenv("GH_SECRET_TOKEN", "").strip()
    if not token:
        raise SecretWriteError(
            "GH_SECRET_TOKEN is not set. The Actions GITHUB_TOKEN cannot write "
            "secrets, so a personal access token with permission to write "
            "repository secrets is required."
        )
    return token


def _call(path: str, *, method: str = "GET", body: dict | None = None) -> dict:
    request = Request(
        f"{API}{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {_token()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read()).get("message", "")
        except Exception:  # noqa: BLE001 - the status code is the real signal
            pass
        # 403 here is almost always the token's scopes rather than the
        # repository, and saying so saves an hour of looking in the wrong place.
        hint = (
            " The token is probably missing permission to write repository "
            "secrets." if exc.code in (403, 404) else ""
        )
        raise SecretWriteError(
            f"GitHub API {method} {path} failed (HTTP {exc.code}): {detail}{hint}"
        ) from exc
    except URLError as exc:
        raise SecretWriteError(f"Could not reach GitHub: {exc.reason}") from exc


def _seal(value: str, public_key: str) -> str:
    """Encrypt *value* to the repository's public key, base64 encoded."""
    try:
        from nacl import encoding, public
    except ImportError as exc:  # pragma: no cover - environment, not logic
        raise SecretWriteError(
            "PyNaCl is not installed. GitHub only accepts secrets sealed with "
            "libsodium; install it with: pip install PyNaCl"
        ) from exc

    sealed = public.SealedBox(
        public.PublicKey(public_key.encode(), encoding.Base64Encoder())
    ).encrypt(value.encode())
    return b64encode(sealed).decode()


def update_secret(repository: str, name: str, value: str) -> None:
    """Set the Actions secret *name* on *repository* (``owner/repo``) to *value*.

    Nothing about *value* is logged or returned -- it is a credential, and the
    usual habit of echoing what was written would put it in a workflow log that
    anyone can read.
    """
    if "/" not in repository:
        raise SecretWriteError(
            f"Repository must be given as 'owner/repo', got {repository!r}."
        )
    key = _call(f"/repos/{repository}/actions/secrets/public-key")
    key_id, public_key = key.get("key_id"), key.get("key")
    if not key_id or not public_key:
        raise SecretWriteError("GitHub did not return a usable public key.")

    _call(
        f"/repos/{repository}/actions/secrets/{name}",
        method="PUT",
        body={"encrypted_value": _seal(value, public_key), "key_id": key_id},
    )
    logger.info("Updated secret %s on %s", name, repository)
