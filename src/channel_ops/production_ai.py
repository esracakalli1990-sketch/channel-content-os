"""AI content production helpers.

Builds source-grounded prompts and talks to the configured AI provider.
Supports multiple providers through the ``providers`` subpackage.
"""
from __future__ import annotations

import csv
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Source ledger helpers
# ---------------------------------------------------------------------------

def verified_sources(video_directory: Path) -> list[dict[str, str]]:
    """Return rows from the source ledger where ``verified`` is truthy."""
    from .project import RESEARCH_DIR

    ledger = video_directory / RESEARCH_DIR / "source_ledger.csv"
    if not ledger.exists():
        raise FileNotFoundError("Source ledger does not exist.")
    with ledger.open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    return [row for row in rows if row.get("verified", "").strip().lower() in {"yes", "true", "1"}]


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def build_prompt(video_id: str, topic: str, stage: str, sources: list[dict[str, str]]) -> str:
    """Build a source-grounded prompt for the given production *stage*."""
    if not sources:
        raise ValueError("At least one verified source is required before an AI draft can be created.")

    source_block = "\n".join(
        f"- [{row['claim_id']}] {row['claim']} | {row['publisher']} | {row['source_url']}"
        for row in sources
    )

    stage_instructions = {
        "research": (
            "Create a concise research brief. State only claims supported by the "
            "supplied source ledger. Flag gaps and contradictions. Cite every "
            "factual statement with [claim_id]."
        ),
        "script": (
            "Write an 8-12 minute English documentary script. Use a hook, open loop, "
            "tension, decision, consequence, and closing loop. Do not add facts "
            "outside the supplied ledger. Cite every factual claim with [claim_id]."
        ),
        "storyboard": (
            "Create a scene-by-scene storyboard for the approved script concept. "
            "For every scene give narration purpose, visual type, suggested duration, "
            "and a licensable asset search query. Do not suggest copyrighted clips "
            "as a default."
        ),
    }
    if stage not in stage_instructions:
        raise ValueError(f"Unknown draft stage: {stage}. Must be one of: {', '.join(stage_instructions)}")

    return (
        f"You are the research and editorial assistant for an English business "
        f"documentary channel.\n\n"
        f"Video ID: {video_id}\n"
        f"Topic: {topic}\n"
        f"Task: {stage_instructions[stage]}\n\n"
        f"Editorial rules:\n"
        f"- Be accurate, specific, and neutral; do not manufacture numbers, dates, "
        f"quotes, or causal claims.\n"
        f"- Treat the source ledger as the only factual authority.\n"
        f"- Preserve uncertainty when the sources disagree.\n"
        f"- This is a draft for human review, not publication-ready truth.\n\n"
        f"Verified source ledger:\n{source_block}\n"
    )


# ---------------------------------------------------------------------------
# Provider dispatcher
# ---------------------------------------------------------------------------

def get_provider(name: str | None = None):
    """Return an AI provider instance by name.

    Falls back to *template* when no API key is available.
    """
    from .config_loader import load_config

    config = load_config()
    provider_name = name or config.ai.active_provider

    if provider_name == "template":
        return None  # template mode — no API call

    try:
        if provider_name == "gemini":
            from .providers.gemini_provider import GeminiProvider
            return GeminiProvider()
        elif provider_name == "openai":
            from .providers.openai_provider import OpenAIProvider
            return OpenAIProvider()
        elif provider_name == "ollama":
            from .providers.ollama_provider import OllamaProvider
            return OllamaProvider()
        else:
            logger.warning("Unknown provider '%s', falling back to template mode.", provider_name)
            return None
    except RuntimeError as exc:
        logger.warning("Provider '%s' unavailable (%s), falling back to template.", provider_name, exc)
        return None


def generate_draft(prompt: str, *, provider_name: str | None = None) -> tuple[str, str]:
    """Generate content using the configured AI provider.

    Returns
    -------
    tuple[str, str]
        (content, mode) where *mode* is the provider name or ``"template"``.
    """
    provider = get_provider(provider_name)
    if provider is None:
        return template_draft(prompt), "template"

    mode = provider_name or "ai"
    try:
        content = provider.generate(prompt)
        logger.info("AI draft generated using provider '%s'.", mode)
        return content, mode
    except Exception as exc:
        logger.error("AI generation failed: %s — falling back to template.", exc)
        return template_draft(prompt), "template"


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def save_draft(video_directory: Path, stage: str, prompt: str, content: str, mode: str) -> Path:
    """Save the generated draft and its prompt to the appropriate directory."""
    from .project import RESEARCH_DIR, SCRIPT_DIR, VISUALS_DIR

    destinations = {"research": RESEARCH_DIR, "script": SCRIPT_DIR, "storyboard": VISUALS_DIR}
    destination_dir = video_directory / destinations[stage]
    destination_dir.mkdir(exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    (destination_dir / f"{stage}_{timestamp}_prompt.md").write_text(prompt, encoding="utf-8")
    destination = destination_dir / f"{stage}_{timestamp}_{mode}.md"
    destination.write_text(content, encoding="utf-8")
    logger.info("Saved %s draft to %s", stage, destination)
    return destination


def template_draft(prompt: str) -> str:
    """Wrap the prompt as a template draft for manual/later AI processing."""
    return (
        "# Human/AI Draft Request\n\n"
        "The following prompt is ready for an approved AI provider.\n\n"
        + prompt
    )
