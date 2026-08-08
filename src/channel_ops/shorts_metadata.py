"""Titles, descriptions and hashtags for a finished transformation short.

The videos carry no speech and no on-screen text, so the caption is the only
thing the algorithm and a scrolling viewer can read. It is generated from the
concept that produced the video rather than from the file, which means it can
be prepared before the clip even arrives.

Each platform gets its own rendering of the same idea: YouTube needs a title
separate from the description, while Instagram and TikTok take a single
caption. Limits differ too, so every field is clipped to what the platform
actually accepts instead of being rejected at upload time.

If the model call fails, :func:`fallback_metadata` builds a serviceable caption
straight from the concept. A day's video going out with a plain title beats the
pipeline stopping.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field, replace

from .providers.base import AIProvider
from .shorts_prompts import Concept

logger = logging.getLogger(__name__)

# Platform limits.
YOUTUBE_TITLE_LIMIT = 100
YOUTUBE_DESCRIPTION_LIMIT = 5000
INSTAGRAM_CAPTION_LIMIT = 2200
TIKTOK_CAPTION_LIMIT = 2200

# Instagram rejects a post outright past this many hashtags.
INSTAGRAM_HASHTAG_LIMIT = 30

# Carried on every video so the series is findable as a body of work.
BASE_HASHTAGS = ("#satisfying", "#mechanical", "#automata", "#asmr", "#transformation")

_METADATA_INSTRUCTIONS = """\
You write captions for a series of silent short videos. Each video shows a
small mechanical toy that unfolds into a miniature mechanical creature when a
button is pressed. There is no narration and no on-screen text, so the caption
carries the whole hook.

Return ONLY a JSON object, no prose and no code fences, with these fields:
  - "title": under 60 characters. Name the closed object but never what it
    becomes, so the reveal still belongs to the video. A question usually
    reads better than a label, but vary the form — the titles are one of the
    few things left to learn from, and eighteen identical ones teach nothing.
  - "hook": 2 to 5 words, burned over the opening seconds of the clip. It has
    to make the viewer ask a question, not answer one, and it has to fit THIS
    object specifically — a hook that would suit every video in the series is
    a failed hook. Name the object's material or shape where it helps.
  - "description": two or three sentences for the YouTube description.
  - "caption": one or two sentences for Instagram and TikTok, more casual.
  - "hashtags": 8 to 12 lowercase hashtag strings, each starting with '#'.

Rules:
1. Describe what actually happens. Never promise anything the video does not
   show — no "you won't believe", no fake stakes.
2. The video is silent and global. Keep the language plain so a non-native
   reader gets it instantly. Avoid idioms and wordplay.
3. The title keeps the surprise. Name the object — "What is inside this bronze
   egg?" — but never the creature, because the whole appeal is not knowing.
   The description and caption may say what it becomes; the title may not.
   Vary the question across videos: ask about the weight, the seam, the
   material, the shape. Not every title should start with "What is inside".
4. At most one emoji in the title, none in the description, none in the hook.
5. Hashtags must be real, searchable terms — no invented tags, no brand names.
6. The hook must never announce the transformation. "Watch it unfold", "watch
   it open", "see it transform" and every variant of them are banned: they
   spend the viewer's curiosity instead of creating it.

The video shows: a {shape} made of {material} that unfolds into a mechanical
{creature}. Internally: {internal_detail}.
"""

# Appended when earlier videos are known, so the model is not free to land on
# the same hook every time. It writes each video in isolation and, left to
# itself, converged on one phrase for three videos running.
_RECENT_HOOKS_NOTE = """\

These hooks were burned onto recent videos in this series. Write a hook that is
clearly different from all of them — different opening word, different idea:
{recent}
"""


@dataclass(frozen=True)
class ShortMetadata:
    """One video's text, before platform-specific clipping."""

    title: str
    description: str
    caption: str
    hashtags: list[str] = field(default_factory=list)
    # Burned over the opening seconds of the clip, so it has to stay short
    # enough to read before the transformation starts.
    hook: str = ""

    def youtube(self) -> tuple[str, str]:
        """Return the (title, description) YouTube expects.

        ``#Shorts`` goes in the description rather than the title so it does
        not eat into the 100 characters a viewer actually reads.
        """
        title = _clip(self.title, YOUTUBE_TITLE_LIMIT)
        tags = " ".join(_merge_hashtags(self.hashtags))
        description = f"{self.description}\n\n#Shorts {tags}".strip()
        return title, _clip(description, YOUTUBE_DESCRIPTION_LIMIT)

    def instagram(self) -> str:
        """Return the Reels caption, within Instagram's hashtag ceiling."""
        tags = _merge_hashtags(self.hashtags)[:INSTAGRAM_HASHTAG_LIMIT]
        return _clip(f"{self.caption}\n\n{' '.join(tags)}".strip(), INSTAGRAM_CAPTION_LIMIT)

    def tiktok(self) -> str:
        """Return the TikTok caption.

        TikTok shows only the first line or so before truncating, so the
        hashtags trail the sentence rather than opening the caption.
        """
        tags = " ".join(_merge_hashtags(self.hashtags))
        return _clip(f"{self.caption} {tags}".strip(), TIKTOK_CAPTION_LIMIT)

    def youtube_tags(self) -> list[str]:
        """Return hashtags as bare keywords for the API's ``tags`` field."""
        return [tag.lstrip("#") for tag in _merge_hashtags(self.hashtags)]


def _clip(text: str, limit: int) -> str:
    """Trim *text* to *limit*, breaking at whitespace where possible."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rstrip()
    spaced = cut.rsplit(" ", 1)[0]
    # Only prefer the word boundary when it does not throw away most of the text.
    return spaced if len(spaced) > limit * 0.7 else cut


def _merge_hashtags(hashtags: list[str]) -> list[str]:
    """Normalise the model's hashtags and append the series' own, no repeats."""
    merged: list[str] = []
    seen: set[str] = set()
    for raw in [*hashtags, *BASE_HASHTAGS]:
        tag = "#" + re.sub(r"[^0-9a-z]", "", str(raw).lower().lstrip("#"))
        if len(tag) > 1 and tag not in seen:
            seen.add(tag)
            merged.append(tag)
    return merged


def _strip_code_fence(text: str) -> str:
    fenced = re.match(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", text, re.DOTALL)
    return fenced.group(1) if fenced else text.strip()


def parse_metadata(raw: str) -> ShortMetadata:
    """Parse the model's JSON object into metadata."""
    try:
        data = json.loads(_strip_code_fence(raw))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Metadata response was not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"Expected a JSON object, got {type(data).__name__}.")

    missing = [key for key in ("title", "description", "caption") if not str(data.get(key, "")).strip()]
    if missing:
        raise RuntimeError(f"Metadata is missing: {', '.join(missing)}")

    hashtags = data.get("hashtags") or []
    if not isinstance(hashtags, list):
        hashtags = []

    return ShortMetadata(
        title=str(data["title"]).strip(),
        description=str(data["description"]).strip(),
        caption=str(data["caption"]).strip(),
        hashtags=[str(tag) for tag in hashtags],
        hook=str(data.get("hook", "")).strip(),
    )


def _object_noun(concept: Concept) -> str:
    """The bare noun for the closed object — "capsule", "disc", "puck"."""
    words = re.findall(r"[a-zA-Z]+", concept.shape)
    return words[-1].lower() if words else "object"


def _default_hook(concept: Concept) -> str:
    """A short hook built from the object itself, so it differs per video.

    The last word of the shape is the noun — "a sleek brushed steel capsule"
    gives "capsule" — which keeps the hook specific without repeating the
    whole description on screen.
    """
    return f"Inside this {_object_noun(concept)}?"


def fallback_metadata(concept: Concept) -> ShortMetadata:
    """Build usable metadata from the concept alone, without the model."""
    creature = concept.creature.strip()
    return ShortMetadata(
        # The fallback title withholds the creature too, so a failed model call
        # does not quietly reintroduce the spoiler the prompt is avoiding.
        title=f"What is inside this {concept.shape.strip()}?",
        hook=_default_hook(concept),
        description=(
            f"A {concept.material.strip()} {concept.shape.strip()} unfolds into "
            f"a mechanical {creature}. One button, one continuous take, no cuts."
        ),
        caption=f"One button and it becomes a mechanical {creature}.",
        hashtags=[f"#{re.sub(r'[^a-z]', '', creature.lower())}"],
    )


def generate_metadata(
    provider: AIProvider,
    concept: Concept,
    *,
    recent_hooks: list[str] | None = None,
) -> ShortMetadata:
    """Write metadata for *concept*, falling back if the model is unavailable.

    *recent_hooks* are the hooks already burned onto earlier videos. They are
    shown to the model as things not to repeat, and enforced afterwards — an
    instruction alone was what produced three identical hooks in a row.
    """
    instructions = _METADATA_INSTRUCTIONS.format(
        shape=concept.shape,
        material=concept.material,
        creature=concept.creature,
        internal_detail=concept.internal_detail,
    )
    used = [hook.strip() for hook in (recent_hooks or []) if hook.strip()]
    if used:
        instructions += _RECENT_HOOKS_NOTE.format(
            recent="\n".join(f"  - {hook}" for hook in used)
        )

    try:
        raw = provider.generate("Write the caption set for this video.", system_prompt=instructions)
        metadata = parse_metadata(raw)
    except RuntimeError as exc:
        logger.warning("Metadata generation failed (%s) — falling back to the concept", exc)
        return fallback_metadata(concept)

    if metadata.hook and metadata.hook.casefold() in {hook.casefold() for hook in used}:
        replacement = _default_hook(concept)
        logger.warning("Model repeated the hook %r — using %r", metadata.hook, replacement)
        metadata = replace(metadata, hook=replacement)

    logger.info("Generated metadata: %s (hook: %s)", metadata.title, metadata.hook)
    return metadata
