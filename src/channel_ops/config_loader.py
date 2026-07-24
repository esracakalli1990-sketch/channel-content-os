"""Configuration loader for Channel Content OS.

Loads channel.json and ai.json from the config directory,
making governance rules, AI settings, and channel identity
available to all modules. Replaces hardcoded values.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_cached_config: AppConfig | None = None


@dataclass(frozen=True)
class ChannelConfig:
    """Channel identity and branding."""
    working_name: str = "Decoded"
    promise: str = ""
    language: str = "en"
    target_market: tuple[str, ...] = ("US", "UK")
    video_format: str = "Business and technology documentary"
    target_duration_min: int = 8
    target_duration_max: int = 12


@dataclass(frozen=True)
class GovernanceConfig:
    """Quality gates and editorial rules."""
    public_publish_requires_human_approval: bool = True
    claim_verification_required: bool = True
    asset_license_record_required: bool = True
    minimum_topic_score: float = 75.0
    minimum_quality_score: float = 80.0
    max_script_revisions: int = 2


@dataclass(frozen=True)
class AIConfig:
    """AI provider settings."""
    default_mode: str = "template"
    active_provider: str = "gemini"
    supported_providers: tuple[str, ...] = ("template", "gemini", "openai", "ollama")
    use_only_verified_sources: bool = True
    cite_claim_ids: bool = True
    human_approval_before_script: bool = True


@dataclass(frozen=True)
class AppConfig:
    """Top-level application configuration."""
    channel: ChannelConfig = field(default_factory=ChannelConfig)
    governance: GovernanceConfig = field(default_factory=GovernanceConfig)
    ai: AIConfig = field(default_factory=AIConfig)


def _parse_channel(data: dict[str, Any]) -> ChannelConfig:
    ch = data.get("channel", {})
    dur = ch.get("target_duration_minutes", [8, 12])
    return ChannelConfig(
        working_name=ch.get("working_name", "Decoded"),
        promise=ch.get("promise", ""),
        language=ch.get("language", "en"),
        target_market=tuple(ch.get("target_market", ["US", "UK"])),
        video_format=ch.get("format", "Business and technology documentary"),
        target_duration_min=dur[0] if len(dur) >= 1 else 8,
        target_duration_max=dur[1] if len(dur) >= 2 else 12,
    )


def _parse_governance(data: dict[str, Any]) -> GovernanceConfig:
    gov = data.get("governance", {})
    return GovernanceConfig(
        public_publish_requires_human_approval=gov.get("public_publish_requires_human_approval", True),
        claim_verification_required=gov.get("claim_verification_required", True),
        asset_license_record_required=gov.get("asset_license_record_required", True),
        minimum_topic_score=float(gov.get("minimum_topic_score", 75)),
        minimum_quality_score=float(gov.get("minimum_quality_score", 80)),
        max_script_revisions=int(gov.get("max_script_revisions", 2)),
    )


def _parse_ai(data: dict[str, Any]) -> AIConfig:
    return AIConfig(
        default_mode=data.get("default_mode", "template"),
        active_provider=data.get("active_provider", "gemini"),
        supported_providers=tuple(data.get("supported_providers", ["template", "gemini", "openai", "ollama"])),
        use_only_verified_sources=data.get("rules", {}).get("use_only_verified_source_ledger_claims", True),
        cite_claim_ids=data.get("rules", {}).get("cite_claim_ids_in_drafts", True),
        human_approval_before_script=data.get("rules", {}).get("human_approval_required_before_script_approved", True),
    )


def find_project_root(start: Path | None = None) -> Path:
    """Walk up from *start* until we find pyproject.toml or .git."""
    current = (start or Path(__file__)).resolve()
    for parent in (current, *current.parents):
        if (parent / "pyproject.toml").exists() or (parent / ".git").exists():
            return parent
    raise FileNotFoundError(
        "Could not locate project root (no pyproject.toml or .git found). "
        "Are you running from inside the channel-content-os directory?"
    )


def load_config(root: Path | None = None, *, force_reload: bool = False) -> AppConfig:
    """Load and cache the application configuration.

    Reads ``config/channel.json`` and ``config/ai.json`` relative to *root*.
    Missing files are tolerated — defaults are used instead.
    """
    global _cached_config
    if _cached_config is not None and not force_reload:
        return _cached_config

    if root is None:
        root = find_project_root()

    config_dir = root / "config"

    # --- channel.json ---
    channel_path = config_dir / "channel.json"
    channel_data: dict[str, Any] = {}
    if channel_path.exists():
        try:
            channel_data = json.loads(channel_path.read_text(encoding="utf-8"))
            logger.info("Loaded %s", channel_path)
        except json.JSONDecodeError as exc:
            logger.warning("Invalid JSON in %s: %s — using defaults", channel_path, exc)
    else:
        logger.info("No channel.json found at %s — using defaults", channel_path)

    # --- ai.json ---
    ai_path = config_dir / "ai.json"
    ai_data: dict[str, Any] = {}
    if ai_path.exists():
        try:
            ai_data = json.loads(ai_path.read_text(encoding="utf-8"))
            logger.info("Loaded %s", ai_path)
        except json.JSONDecodeError as exc:
            logger.warning("Invalid JSON in %s: %s — using defaults", ai_path, exc)
    else:
        logger.info("No ai.json found at %s — using defaults", ai_path)

    _cached_config = AppConfig(
        channel=_parse_channel(channel_data),
        governance=_parse_governance(channel_data),
        ai=_parse_ai(ai_data),
    )
    return _cached_config
