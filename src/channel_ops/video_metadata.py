"""Read and repair the metadata of videos that are already published.

Two fields were never sent when these videos were uploaded and show up empty
in Studio on all 148 of them:

* the language of the title/description and of the audio, which Studio prints
  as "Seçin" and which YouTube uses for captions, translation and which
  language's audience sees the clip;
* ``containsSyntheticMedia``, the altered-or-synthetic content disclosure.
  YouTube has auto-applied the label to some of the videos and not to others,
  depending on whether the generating tool left a C2PA signal. The content is
  the same either way, so the ones it missed are an undisclosed gap rather
  than an advantage, and the policy-correct state is for all of them to carry
  the declaration.

THE DANGEROUS PART, stated once so nobody has to rediscover it: ``videos.update``
replaces every part it is given. A request carrying ``part=snippet`` with only
``defaultLanguage`` in it does not add a language -- it erases the title, the
description and the tags. Every writable field therefore has to be read first
and sent back unchanged alongside the new one, which is what :func:`planned_update`
does and what its tests are about.
"""
from __future__ import annotations

import json
import logging
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .youtube_auth import get_access_token

logger = logging.getLogger(__name__)

VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"

# The Data API takes fifty ids per read.
BATCH = 50

# Writable status fields. Deliberately a whitelist rather than "whatever came
# back": videos.list also returns read-only companions -- madeForKids beside
# selfDeclaredMadeForKids, uploadStatus, publishAt -- and echoing a read-only
# field back is rejected for the whole request. Omitting a writable one is
# worse: it is cleared.
_STATUS_FIELDS = ("privacyStatus", "license", "embeddable", "publicStatsViewable")


def fetch(video_ids: list[str]) -> dict[str, dict]:
    """Return ``{video_id: {"snippet": ..., "status": ...}}`` exactly as stored."""
    token = get_access_token()
    out: dict[str, dict] = {}
    for start in range(0, len(video_ids), BATCH):
        chunk = video_ids[start:start + BATCH]
        params = urlencode({"part": "snippet,status", "id": ",".join(chunk)})
        request = Request(f"{VIDEOS_URL}?{params}",
                          headers={"Authorization": f"Bearer {token}"})
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
        for item in payload.get("items", []):
            out[item["id"]] = {
                "snippet": item.get("snippet", {}),
                "status": item.get("status", {}),
            }
    return out


def load_declared(root) -> set[str]:
    """Video ids already told that they contain synthetic media.

    This file exists because ``containsSyntheticMedia`` is write-only: it is
    accepted by videos.update and never comes back from videos.list, so there
    is no way to ask YouTube whether a video has been declared. Without a
    local record every video looks undeclared forever, the repair list never
    shrinks, and a batched run rewrites its first forty videos every day
    while the rest are never reached. That is not hypothetical -- it is what
    the first two runs did.
    """
    path = root / "data" / "ai_declared.json"
    if not path.exists():
        return set()
    try:
        return set(json.loads(path.read_text()))
    except (json.JSONDecodeError, ValueError):
        logger.warning("%s is unreadable; treating every video as undeclared", path)
        return set()


def save_declared(root, declared: set[str]) -> None:
    path = root / "data" / "ai_declared.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(declared), indent=2) + "\n")


def needs_work(current: dict, *, language: str, declare_ai: bool) -> list[str]:
    """Which of the two repairs this video is missing, as human-readable names."""
    snippet, status = current.get("snippet", {}), current.get("status", {})
    missing = []
    if snippet.get("defaultLanguage") != language:
        missing.append("defaultLanguage")
    if snippet.get("defaultAudioLanguage") != language:
        missing.append("defaultAudioLanguage")
    if declare_ai and not status.get("containsSyntheticMedia"):
        missing.append("containsSyntheticMedia")
    return missing


def planned_update(video_id: str, current: dict, *, language: str,
                   declare_ai: bool) -> dict:
    """Build the full request body for one video.

    Everything writable is carried over from *current*; only the fields being
    repaired differ. The result is what would be sent, so a dry run can print
    it and a test can assert on it without touching the network.
    """
    snippet = dict(current.get("snippet", {}))
    status = current.get("status", {})

    # categoryId and title are required by the API; description and tags are
    # not, but leaving either out deletes it.
    new_snippet = {
        "title": snippet.get("title", ""),
        "categoryId": snippet.get("categoryId", ""),
        "description": snippet.get("description", ""),
        "tags": snippet.get("tags", []),
        "defaultLanguage": language,
        "defaultAudioLanguage": language,
    }

    new_status = {field: status[field] for field in _STATUS_FIELDS if field in status}
    # selfDeclaredMadeForKids is write-only, so it never comes back from a
    # read. Its read-only twin madeForKids does, and is the value to preserve.
    if "madeForKids" in status:
        new_status["selfDeclaredMadeForKids"] = bool(status["madeForKids"])
    if declare_ai:
        new_status["containsSyntheticMedia"] = True
    elif "containsSyntheticMedia" in status:
        new_status["containsSyntheticMedia"] = bool(status["containsSyntheticMedia"])

    return {"id": video_id, "snippet": new_snippet, "status": new_status}


def verify(sent: dict, returned: dict) -> list[str]:
    """Fields that did not come back the way they were sent.

    The whole risk of this operation is a request that silently drops a
    description or a tag list, and videos.update answers with the stored
    resource, so the check costs nothing and runs on every video. An empty
    list means the write landed exactly as intended.
    """
    sent_snippet = sent.get("snippet", {})
    back_snippet = returned.get("snippet", {})
    wrong = []
    for field in ("title", "description", "categoryId",
                  "defaultLanguage", "defaultAudioLanguage"):
        if sent_snippet.get(field, "") != back_snippet.get(field, ""):
            wrong.append(field)
    if sorted(sent_snippet.get("tags", [])) != sorted(back_snippet.get("tags", [])):
        wrong.append("tags")
    # containsSyntheticMedia is write-only on some videos and simply does not
    # come back, so a missing one is not evidence of anything. Only a value
    # that came back contradicting what was sent is.
    back_status = returned.get("status", {})
    if "containsSyntheticMedia" in back_status:
        if bool(back_status["containsSyntheticMedia"]) is not bool(
            sent.get("status", {}).get("containsSyntheticMedia", False)
        ):
            wrong.append("containsSyntheticMedia")
    if back_status.get("privacyStatus", "") != sent.get("status", {}).get("privacyStatus", ""):
        wrong.append("privacyStatus")
    return wrong


def apply(body: dict) -> dict:
    """Send one prepared body to videos.update."""
    token = get_access_token()
    url = f"{VIDEOS_URL}?{urlencode({'part': 'snippet,status'})}"
    request = Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        method="PUT",
    )
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
        except Exception:  # noqa: BLE001
            pass
        if exc.code == 403:
            raise RuntimeError(
                f"YouTube refused the update for {body.get('id')} (HTTP 403). The "
                "refresh token most likely carries only youtube.upload, which can "
                "create videos but not edit them. Minting a token with the "
                "https://www.googleapis.com/auth/youtube scope is what fixes it. "
                f"{detail}"
            ) from exc
        raise RuntimeError(
            f"YouTube refused the update for {body.get('id')} (HTTP {exc.code}): {detail}"
        ) from exc
