"""Ask the API whether the channel is earning yet.

The monetisation switch is not exposed as a field anywhere: no Data API
property says "this channel is in the Partner Programme". But the Analytics
API will only answer a revenue query for a channel that is actually earning,
so the question can be asked sideways -- request ``estimatedRevenue`` and see
whether YouTube serves it.

This exists because the alternative was waiting for an email. On 13 October
the channel's inbox turned out to have had creator emails switched off for
months, which is exactly the way a single-path notification fails: silently,
and only noticed when the thing you were waiting for has already happened.
This path does not depend on anyone's notification settings.

IMPORTANT, and the reason :func:`probe` reports rather than decides: a 403
here has two very different causes that must not be confused.

* The refresh token lacks ``yt-analytics-monetary.readonly``. The probe can
  then never succeed, no matter what happens to the channel -- a check that
  cannot ever turn true is worse than no check, because it reads as "not yet"
  forever.
* The channel genuinely is not monetised yet. Expected, and quiet.

The exact wording YouTube returns for each has not been observed from this
account, so nothing here guesses. The first run records whatever comes back
verbatim; the classification below is refined once a real message is in hand.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, date, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .youtube_analytics import ANALYTICS_URL, _channel_selector
from .youtube_auth import get_access_token

logger = logging.getLogger(__name__)

# The monetary scope. Named here so the operator message can quote it exactly.
MONETARY_SCOPE = "https://www.googleapis.com/auth/yt-analytics-monetary.readonly"

# Google will list a token's granted scopes, which is the only way to tell
# the two causes of a 403 apart with certainty.
TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"

# How far back to ask. Short, because the question is "is it on", not "how
# much" -- and a narrow window keeps the call cheap.
LOOKBACK_DAYS = 7


def probe(channel_id: str | None = None, *, today: date | None = None) -> dict:
    """Ask for revenue and report what happened, without deciding too much.

    Returns a dict with:

    ``state``
        ``"earning"`` when YouTube served revenue figures, ``"not_yet"`` when
        it refused in a way consistent with an unmonetised channel,
        ``"scope_missing"`` when the refusal points at the token instead, and
        ``"error"`` for anything else.
    ``revenue``
        The figure when there is one, else ``None``. A served zero is still
        ``"earning"``: the query being answered at all is the signal, and a
        monetised channel can legitimately have earned nothing yet.
    ``detail``
        YouTube's own message, kept verbatim for the report.
    """
    end = today or date.today()
    start = end - timedelta(days=LOOKBACK_DAYS)
    params = {
        "ids": _channel_selector(channel_id),
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "metrics": "estimatedRevenue",
    }

    token = get_access_token()
    request = Request(
        f"{ANALYTICS_URL}?{urlencode(params)}",
        headers={"Authorization": f"Bearer {token}"},
    )

    try:
        with urlopen(request, timeout=20) as response:
            payload = json.load(response)
    except HTTPError as exc:
        return _classify(exc)
    except URLError as exc:
        return {"state": "error", "revenue": None,
                "detail": f"Analytics'e ulaşılamadı: {exc.reason}"}

    rows = payload.get("rows") or []
    revenue = float(rows[0][0]) if rows and rows[0] else 0.0
    logger.info("Monetisation probe answered: revenue=%s", revenue)
    return {"state": "earning", "revenue": revenue,
            "detail": "Analytics gelir sorgusunu cevapladı."}


def _classify(exc: HTTPError) -> dict:
    """Turn an HTTP failure into one of the three meanings it can carry."""
    detail = ""
    try:
        detail = json.loads(exc.read().decode("utf-8", "replace")).get(
            "error", {}).get("message", "")
    except (json.JSONDecodeError, ValueError):
        pass
    detail = detail or f"HTTP {exc.code}"

    if exc.code in (401, 403):
        # Observed on 13 October: YouTube answers "Insufficient permission to
        # access this report." Reading that string was the original plan and
        # it turned out to be useless -- the same sentence fits a token that
        # may not ask AND a channel with no revenue to report. Guessing would
        # have meant either a broken probe reporting "not yet" forever, or a
        # false alarm about scopes every week. So the token is asked directly
        # instead, which answers one half of the question with certainty.
        granted = _granted_scopes()
        if granted is None:
            return {
                "state": "error",
                "revenue": None,
                "detail": (f"{detail} (token kapsamları okunamadı, bu yüzden "
                           "sebebi ayırt edilemiyor)"),
            }
        if MONETARY_SCOPE not in granted:
            return {
                "state": "scope_missing",
                "revenue": None,
                "detail": (
                    f"{detail} Token'da {MONETARY_SCOPE} kapsamı YOK — "
                    "yoklama kanala ne olursa olsun olumlu cevap veremez. "
                    "YOUTUBE_REFRESH_TOKEN bu kapsamla yeniden üretilmeli."
                ),
            }
        # The scope is granted and YouTube still refuses: the only remaining
        # reading is that there is no revenue report to serve.
        return {"state": "not_yet", "revenue": None, "detail": detail}

    return {"state": "error", "revenue": None, "detail": detail}


def _granted_scopes() -> set[str] | None:
    """The scopes the current access token actually carries, or None.

    Google's tokeninfo endpoint answers this directly, which turns the
    ambiguous 403 above into a decidable question. None means the lookup
    itself failed, and that is reported rather than guessed around.
    """
    try:
        token = get_access_token()
        url = f"{TOKENINFO_URL}?{urlencode({'access_token': token})}"
        with urlopen(url, timeout=15) as response:
            payload = json.load(response)
    except (HTTPError, URLError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("Could not read token scopes: %s", exc)
        return None
    return set(str(payload.get("scope", "")).split())


def summary(result: dict) -> str:
    """One line for the report, phrased for the person reading it on a phone."""
    state = result.get("state")
    if state == "earning":
        revenue = result.get("revenue")
        # Turkish separators: "1,234.50" -> "1.234,50". Swapped through a
        # placeholder, because replacing one at a time turns every separator
        # into the same character.
        money = (f"{revenue:,.2f}".replace(",", "\x00").replace(".", ",")
                 .replace("\x00", ".")) if revenue is not None else "?"
        return (f"🎉 PARA KAZANMA AKTİF — Analytics gelir veriyor "
                f"(son {LOOKBACK_DAYS} gün: ${money})")
    if state == "scope_missing":
        return f"⚠️ Gelir yoklaması çalışamıyor: {result.get('detail', '')}"
    if state == "error":
        return f"ⓘ Gelir yoklaması yapılamadı: {result.get('detail', '')}"
    return f"Para kazanma henüz açılmamış ({result.get('detail', '')})"
