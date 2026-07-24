"""Local Ollama provider (free, offline, no internet required)."""
from __future__ import annotations

import json
import logging
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .base import AIProvider

logger = logging.getLogger(__name__)


class OllamaProvider(AIProvider):
    """Uses a local Ollama instance for inference.

    Requires Ollama running locally (default ``http://localhost:11434``).
    No API key needed — completely free.
    Default model: ``llama3.1:8b``.
    """

    def __init__(self) -> None:
        self._base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        self._model = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
        # Quick connectivity check
        try:
            urlopen(f"{self._base_url}/api/version", timeout=5)
        except Exception:
            raise RuntimeError(
                f"Ollama is not running at {self._base_url}. "
                "Install from https://ollama.com and run 'ollama serve'."
            )

    @property
    def name(self) -> str:
        return "ollama"

    def generate(self, prompt: str, *, system_prompt: str | None = None) -> str:
        body_dict: dict = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
        }
        if system_prompt:
            body_dict["system"] = system_prompt

        body = json.dumps(body_dict).encode("utf-8")
        request = Request(
            f"{self._base_url}/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urlopen(request, timeout=300) as response:
                payload = json.load(response)
        except HTTPError as exc:
            raise RuntimeError(f"Ollama returned HTTP {exc.code}") from exc
        except URLError as exc:
            raise RuntimeError(f"Could not reach Ollama: {exc.reason}") from exc

        text = payload.get("response", "")
        if not text.strip():
            raise RuntimeError("Ollama returned empty response.")
        return text
