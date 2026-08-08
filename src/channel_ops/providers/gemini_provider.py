"""Google Gemini AI provider (default — free tier available)."""
from __future__ import annotations

import json
import logging
import os
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .base import AIProvider

logger = logging.getLogger(__name__)

# Gemini API endpoint — works with a simple API key.
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

# Statuses worth another attempt: rate limiting and the free tier's periodic
# "high demand" unavailability. Everything else is a real problem and retrying
# it only delays the error.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
# Four attempts spanning 28 seconds was not enough: on 6 August the free tier
# answered 503 to all of them and that slot's idea was never sent. Six attempts
# span about two minutes, which is the length these outages have actually run.
_MAX_ATTEMPTS = 6
_BACKOFF_SECONDS = 4
# Without a ceiling the last waits would double to 128s and push a scheduled
# job past the point where it is worth still waiting.
_MAX_BACKOFF_SECONDS = 45

# Model names that exist but cannot serve a text prompt.
_UNSUITABLE = ("embedding", "aqa", "imagen", "veo", "tts", "image-generation")


class _ModelUnavailable(RuntimeError):
    """The configured model name is not callable by this key."""


class _Transient(RuntimeError):
    """A failure that is likely to clear on its own."""


class GeminiProvider(AIProvider):
    """Uses the Google Gemini REST API (no SDK dependency).

    Requires the ``GEMINI_API_KEY`` environment variable.

    Google retires model names and closes older ones to new API keys, which
    takes the whole pipeline down with a 404 even though the key is valid. So
    the configured model is only a preference: if it is gone, the account is
    asked what it actually has and the closest flash model is used instead.
    """

    def __init__(self) -> None:
        self._api_key = os.getenv("GEMINI_API_KEY", "")
        self._model = os.getenv("GEMINI_MODEL", "").strip() or "gemini-flash-latest"
        if not self._api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Get a free key at https://aistudio.google.com/apikey"
            )

    @property
    def name(self) -> str:
        return "gemini"

    def available_models(self) -> list[str]:
        """Return the models this key may call for text generation."""
        url = f"{GEMINI_API_BASE}?key={self._api_key}&pageSize=200"
        try:
            with urlopen(Request(url), timeout=30) as response:
                payload = json.load(response)
        except (HTTPError, URLError) as exc:
            raise RuntimeError(f"Could not list Gemini models: {exc}") from exc

        models = []
        for entry in payload.get("models", []):
            name = str(entry.get("name", "")).removeprefix("models/")
            if not name or "generateContent" not in entry.get("supportedGenerationMethods", []):
                continue
            if any(word in name.lower() for word in _UNSUITABLE):
                continue
            models.append(name)
        return models

    @staticmethod
    def _rank(name: str) -> tuple:
        """Sort key preferring the newest general-purpose flash model.

        Flash models are what the free tier actually serves; "latest" aliases
        are preferred because they survive the next retirement. Experimental
        and preview builds go last — they disappear without warning.
        """
        lowered = name.lower()
        version = re.search(r"(\d+(?:\.\d+)?)", lowered)
        return (
            "flash" in lowered,
            "latest" in lowered,
            not any(word in lowered for word in ("exp", "preview", "thinking")),
            "lite" not in lowered,
            float(version.group(1)) if version else 0.0,
        )

    def _resolve_model(self) -> str:
        """Swap the configured model for a working one, once."""
        candidates = self.available_models()
        if not candidates:
            raise RuntimeError(
                "This Gemini key cannot call any text generation model. Check the key at "
                "https://aistudio.google.com/apikey"
            )
        chosen = max(candidates, key=self._rank)
        logger.warning(
            "Gemini model %r is unavailable; using %r instead. Set GEMINI_MODEL to pin a "
            "different one. Available: %s",
            self._model, chosen, ", ".join(sorted(candidates)[:10]),
        )
        self._model = chosen
        return chosen

    def generate(self, prompt: str, *, system_prompt: str | None = None) -> str:
        try:
            return self._generate_with_retry(prompt, system_prompt)
        except _ModelUnavailable:
            self._resolve_model()
            return self._generate_with_retry(prompt, system_prompt)

    def _generate_with_retry(self, prompt: str, system_prompt: str | None) -> str:
        """Call the model, riding out the free tier's transient failures.

        A scheduled job gets one chance a day, so giving up on a passing spike
        in demand would cost the whole day's prompts.
        """
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                return self._generate_once(prompt, system_prompt)
            except _Transient as exc:
                if attempt == _MAX_ATTEMPTS:
                    raise RuntimeError(
                        f"Gemini was still unavailable after {_MAX_ATTEMPTS} attempts: {exc}"
                    ) from exc
                delay = min(_BACKOFF_SECONDS * 2 ** (attempt - 1), _MAX_BACKOFF_SECONDS)
                logger.warning(
                    "Gemini call failed (%s); retrying in %ds (attempt %d/%d)",
                    exc, delay, attempt + 1, _MAX_ATTEMPTS,
                )
                time.sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover

    def _generate_once(self, prompt: str, system_prompt: str | None) -> str:
        url = f"{GEMINI_API_BASE}/{self._model}:generateContent?key={self._api_key}"

        contents = []
        if system_prompt:
            contents.append({"role": "user", "parts": [{"text": system_prompt}]})
            contents.append({"role": "model", "parts": [{"text": "Understood. I will follow these instructions."}]})
        contents.append({"role": "user", "parts": [{"text": prompt}]})

        body = json.dumps({"contents": contents}).encode("utf-8")
        request = Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urlopen(request, timeout=120) as response:
                payload = json.load(response)
        except HTTPError as exc:
            error_body = ""
            try:
                error_body = exc.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                pass
            # 404 here means the model name, not the endpoint — either retired or
            # closed to newer keys. Signal that so the caller can pick another.
            if exc.code == 404:
                raise _ModelUnavailable(error_body) from exc
            if exc.code in _RETRYABLE_STATUS:
                raise _Transient(f"HTTP {exc.code}") from exc
            raise RuntimeError(
                f"Gemini API returned HTTP {exc.code}: {error_body}"
            ) from exc
        except URLError as exc:
            # A dropped connection is as temporary as a 503.
            raise _Transient(f"connection failed ({exc.reason})") from exc

        # Parse response
        try:
            candidates = payload.get("candidates", [])
            if not candidates:
                raise RuntimeError("Gemini returned no candidates.")
            parts = candidates[0].get("content", {}).get("parts", [])
            text = "\n".join(part.get("text", "") for part in parts if part.get("text"))
            if not text.strip():
                raise RuntimeError("Gemini returned empty text.")
            return text
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"Unexpected Gemini response format: {exc}") from exc
