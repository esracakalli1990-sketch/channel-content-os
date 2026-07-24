"""Visual planning: derive stock-footage search queries from a narration script.

This module turns an approved narration script into a short list of concise
search queries suitable for the Pexels stock-video API. It does NOT invent any
factual content — it only produces *search terms* used to pick illustrative
b-roll, so it does not conflict with the project's zero-hallucination rule for
script generation.

Strategy:
  1. Ask the configured AI provider (Gemini by default) to extract short,
     visual, English search phrases as a JSON array.
  2. If no provider is available or the call fails, fall back to a lightweight
     deterministic keyword extractor over the script text.
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Common English + Markdown noise words we never want as a visual search query.
_STOPWORDS = frozenset("""
a an and are as at be been but by for from had has have he her his how i in into is
it its of on or our that the their them they this to was we were what when which who
will with you your not no yes do does did can could would should may might must about
over under more most some such than then there here their they're it's don't isn't
one two three also just very much many out up down off only own same so too s t re ve
""".split())

_SYSTEM_PROMPT = (
    "You are a video producer selecting stock b-roll footage. "
    "Given a narration script, return ONLY a JSON array of 4-8 short English "
    "search queries (2-4 words each) describing concrete, filmable visuals that "
    "match the narration's topics. Prefer tangible nouns and scenes over "
    "abstract concepts. Do not add commentary. Example: "
    '["city skyline aerial", "fast food kitchen", "stock market screen"].'
)


# ---------------------------------------------------------------------------
# Scene model — narration timing drives the visuals
# ---------------------------------------------------------------------------

@dataclass
class Scene:
    """A stretch of narration that one stock clip should illustrate."""

    start: float           # seconds
    end: float             # seconds
    text: str              # what is actually said during this window
    query: str = ""        # stock-footage search query for this window

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


_SRT_TIME = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*"
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{1,3})"
)


def _to_seconds(hours: str, minutes: str, seconds: str, millis: str) -> float:
    return (
        int(hours) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(millis.ljust(3, "0")) / 1000
    )


def parse_srt(subtitle_path: Path) -> list[tuple[float, float, str]]:
    """Parse an SRT file into ``(start, end, text)`` tuples.

    Deliberately dependency-free: the project does not otherwise need an SRT
    library, and the format we consume is the one we generate ourselves.
    Malformed blocks are skipped rather than raising.
    """
    if not subtitle_path.exists():
        return []

    cues: list[tuple[float, float, str]] = []
    blocks = re.split(r"\n\s*\n", subtitle_path.read_text(encoding="utf-8").strip())
    for block in blocks:
        match = _SRT_TIME.search(block)
        if not match:
            continue
        groups = match.groups()
        start = _to_seconds(*groups[:4])
        end = _to_seconds(*groups[4:])
        # Text is everything after the timing line.
        text = block[match.end():].strip().replace("\n", " ")
        if text:
            cues.append((start, end, text))

    logger.info("Parsed %d subtitle cues from %s", len(cues), subtitle_path.name)
    return cues


def build_scenes(
    cues: list[tuple[float, float, str]],
    *,
    target_seconds: float = 15.0,
    max_scenes: int = 40,
) -> list[Scene]:
    """Group subtitle cues into scenes of roughly *target_seconds* each.

    Cues are never split, so every scene boundary falls on a sentence break —
    the visual changes when the narration moves on, not mid-thought.
    """
    if not cues:
        return []

    scenes: list[Scene] = []
    start, end, parts = cues[0][0], cues[0][1], [cues[0][2]]

    for cue_start, cue_end, cue_text in cues[1:]:
        if cue_end - start >= target_seconds:
            scenes.append(Scene(start=start, end=end, text=" ".join(parts)))
            start, end, parts = cue_start, cue_end, [cue_text]
        else:
            end = cue_end
            parts.append(cue_text)

    scenes.append(Scene(start=start, end=end, text=" ".join(parts)))

    # Guard against pathological input producing hundreds of tiny scenes.
    if len(scenes) > max_scenes:
        logger.warning("Capping %d scenes to %d", len(scenes), max_scenes)
        scenes = _merge_to_limit(scenes, max_scenes)

    logger.info(
        "Built %d scenes (avg %.1fs) from %d cues",
        len(scenes), sum(s.duration for s in scenes) / len(scenes), len(cues),
    )
    return scenes


def _merge_to_limit(scenes: list[Scene], limit: int) -> list[Scene]:
    """Merge adjacent scenes until at most *limit* remain."""
    merged = list(scenes)
    while len(merged) > limit:
        # Merge the shortest scene into its neighbour.
        index = min(range(len(merged)), key=lambda i: merged[i].duration)
        partner = index + 1 if index + 1 < len(merged) else index - 1
        low, high = sorted((index, partner))
        combined = Scene(
            start=merged[low].start,
            end=merged[high].end,
            text=f"{merged[low].text} {merged[high].text}",
        )
        merged[low:high + 1] = [combined]
    return merged


def _tokens(text: str) -> set[str]:
    """Lowercase content words of *text*, used for similarity scoring."""
    words = re.findall(r"[a-z']{3,}", text.lower())
    return {w for w in words if w not in _STOPWORDS}


def _similarity(left: set[str], right: set[str]) -> float:
    """Jaccard overlap between two token sets (0.0 - 1.0)."""
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def assign_clips_to_scenes(
    scenes: list[Scene],
    clip_map: dict[str, Path],
    *,
    reuse_penalty: float = 0.15,
) -> list[tuple[Path, float]]:
    """Map every scene to a clip, returning ``(clip_path, duration)`` segments.

    A scene whose own query was downloaded uses that clip directly. The rest
    fall back to the closest clip we already have, scored on word overlap
    between the scene (its query plus keywords from what is actually said) and
    the query each clip was downloaded for. This beats cycling through clips in
    order, which pays no attention to what the narration is about.

    *reuse_penalty* discourages leaning on one clip: each previous use lowers a
    clip's score, so coverage stays spread out. The clip used by the previous
    scene is never repeated back-to-back.
    """
    if not scenes or not clip_map:
        return []

    candidates = [(query, path, _tokens(query)) for query, path in clip_map.items()]
    usage: dict[Path, int] = {path: 0 for path in clip_map.values()}

    segments: list[tuple[Path, float]] = []
    previous: Path | None = None

    for scene in scenes:
        chosen = clip_map.get(scene.query)

        if chosen is None or chosen == previous:
            scene_tokens = _tokens(scene.query) | set(_fallback_keywords(scene.text, 8))

            best_score = float("-inf")
            best_path = None
            for _, path, query_tokens in candidates:
                if path == previous and len(clip_map) > 1:
                    continue
                score = _similarity(scene_tokens, query_tokens)
                score -= usage[path] * reuse_penalty
                if score > best_score:
                    best_score, best_path = score, path

            chosen = best_path or next(iter(clip_map.values()))

        usage[chosen] = usage.get(chosen, 0) + 1
        segments.append((chosen, scene.duration))
        previous = chosen

    logger.info(
        "Assigned %d scenes across %d clips (max reuse: %d)",
        len(segments), len(clip_map), max(usage.values()) if usage else 0,
    )
    return segments


_SCENE_SYSTEM_PROMPT = (
    "You are a documentary video editor choosing stock b-roll for each scene. "
    "You will receive numbered scenes of narration. Return ONLY a JSON array of "
    "search queries — exactly one per scene, in the same order. Each query is "
    "2-4 English words naming a concrete, filmable visual that matches what is "
    "said in that scene. Prefer tangible subjects over abstractions. Never "
    "return fewer or more queries than there are scenes."
)


def plan_scene_queries(scenes: list[Scene], *, provider_name: str | None = None) -> list[Scene]:
    """Fill in ``scene.query`` for every scene, aligned to its narration.

    Uses a single AI call for the whole list so the editor sees the full arc.
    Falls back to per-scene keyword extraction when the AI is unavailable or
    returns a mismatched number of queries.
    """
    if not scenes:
        return scenes

    try:
        from .production_ai import get_provider

        provider = get_provider(provider_name)
        if provider is not None:
            numbered = "\n\n".join(
                f"Scene {i + 1}: {scene.text[:400]}" for i, scene in enumerate(scenes)
            )
            raw = provider.generate(
                f"{numbered}\n\nReturn exactly {len(scenes)} queries as a JSON array.",
                system_prompt=_SCENE_SYSTEM_PROMPT,
            )
            queries = _parse_json_array(raw)
            if len(queries) == len(scenes):
                for scene, query in zip(scenes, queries):
                    scene.query = query
                logger.info("AI planned %d scene queries", len(queries))
                return scenes
            logger.warning(
                "AI returned %d queries for %d scenes; falling back to keywords.",
                len(queries), len(scenes),
            )
    except Exception as exc:  # noqa: BLE001 — visuals are best-effort
        logger.warning("Scene query planning failed (%s), using keywords.", exc)

    for scene in scenes:
        keywords = _fallback_keywords(scene.text, 3)
        scene.query = " ".join(keywords) if keywords else "abstract background"
    return scenes


def _parse_json_array(text: str) -> list[str]:
    """Extract the first JSON array of strings from an LLM response."""
    match = re.search(r"\[.*?\]", text, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    return [str(q).strip() for q in data if str(q).strip()]


def _fallback_keywords(script_text: str, max_queries: int) -> list[str]:
    """Deterministic keyword extraction when no AI provider is available."""
    # Strip markdown, source refs, and punctuation.
    text = re.sub(r"\[C-\d+\]", " ", script_text)
    text = re.sub(r"[*_#>`\-\[\]()]", " ", text)
    words = re.findall(r"[A-Za-z']{3,}", text.lower())
    counts = Counter(w for w in words if w not in _STOPWORDS)
    common = [word for word, _ in counts.most_common(max_queries)]
    logger.info("Fallback visual keywords: %s", common)
    return common


def plan_visual_queries(
    script_text: str,
    *,
    max_queries: int = 6,
    provider_name: str | None = None,
) -> list[str]:
    """Return a list of stock-footage search queries derived from *script_text*.

    Falls back to deterministic keyword extraction if the AI provider is
    unavailable or returns nothing usable. Never raises — visuals are best
    effort and must not break the pipeline.
    """
    script_text = (script_text or "").strip()
    if not script_text:
        return []

    try:
        from .production_ai import get_provider

        provider = get_provider(provider_name)
        if provider is not None:
            prompt = (
                "Narration script:\n\n"
                f"{script_text[:6000]}\n\n"
                "Return the JSON array of search queries now."
            )
            raw = provider.generate(prompt, system_prompt=_SYSTEM_PROMPT)
            queries = _parse_json_array(raw)
            if queries:
                queries = queries[:max_queries]
                logger.info("AI visual queries (%s): %s", provider.name, queries)
                return queries
            logger.warning("AI returned no usable queries, using fallback.")
    except Exception as exc:  # noqa: BLE001 — visuals are best-effort
        logger.warning("Visual query planning via AI failed (%s), using fallback.", exc)

    return _fallback_keywords(script_text, max_queries)
