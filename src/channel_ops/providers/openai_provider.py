"""OpenAI Chat Completions provider (optional — requires paid API key)."""
from __future__ import annotations

import json
import logging
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .base import AIProvider

logger = logging.getLogger(__name__)


class OpenAIProvider(AIProvider):
    """Uses the OpenAI Chat Completions API (``/v1/chat/completions``).

    Requires ``OPENAI_API_KEY`` environment variable.
    Default model: ``gpt-4o-mini`` (cost-effective).
    """

    def __init__(self) -> None:
        self._api_key = os.getenv("OPENAI_API_KEY", "")
        self._model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        if not self._api_key:
            raise RuntimeError("OPENAI_API_KEY is not set.")

    @property
    def name(self) -> str:
        return "openai"

    def generate(self, prompt: str, *, system_prompt: str | None = None) -> str:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        body = json.dumps({
            "model": self._model,
            "messages": messages,
            "temperature": 0.7,
        }).encode("utf-8")

        request = Request(
            "https://api.openai.com/v1/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
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
            raise RuntimeError(f"OpenAI API returned HTTP {exc.code}: {error_body}") from exc
        except URLError as exc:
            raise RuntimeError(f"Could not reach OpenAI API: {exc.reason}") from exc

        try:
            return payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"Unexpected OpenAI response format: {exc}") from exc
