"""Abstract base for all AI providers."""
from __future__ import annotations

from abc import ABC, abstractmethod


class AIProvider(ABC):
    """Interface that every AI provider must implement."""

    @abstractmethod
    def generate(self, prompt: str, *, system_prompt: str | None = None) -> str:
        """Send *prompt* to the model and return the text response.

        Parameters
        ----------
        prompt:
            The user-facing prompt (research brief, script request, etc.)
        system_prompt:
            Optional system-level instruction for the model.

        Raises
        ------
        RuntimeError
            When the API call fails or no usable text is returned.
        """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier for this provider (e.g. ``"gemini"``)."""
