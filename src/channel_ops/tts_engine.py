"""Text-to-Speech engine using Edge-TTS (free, no API key needed).

Produces natural-sounding English narration for documentary videos.
Falls back to a simple status message when edge-tts is not installed.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_VOICE = "en-US-GuyNeural"  # Deep, documentary-style male voice

# Alternative voices:
# en-US-AriaNeural      — Female, warm
# en-US-ChristopherNeural — Male, authoritative
# en-GB-RyanNeural      — British male
# en-AU-WilliamNeural   — Australian male


def get_voice() -> str:
    """Return the configured TTS voice from env or default."""
    return os.getenv("EDGE_TTS_VOICE", DEFAULT_VOICE)


async def _generate_async(
    text: str,
    output_path: Path,
    voice: str,
    rate: str = "+0%",
    pitch: str = "+0Hz",
) -> Path:
    """Internal async TTS generation."""
    try:
        import edge_tts
    except ImportError:
        raise RuntimeError(
            "edge-tts is not installed. Run: pip install edge-tts\n"
            "Or install with extras: pip install -e '.[tts]'"
        )

    communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    await communicate.save(str(output_path))
    logger.info("TTS audio saved: %s (voice=%s, rate=%s)", output_path.name, voice, rate)
    return output_path


def generate_narration(
    text: str,
    output_path: Path,
    *,
    voice: str | None = None,
    rate: str = "+0%",
    pitch: str = "+0Hz",
) -> Path:
    """Generate narration audio from text.

    Parameters
    ----------
    text:
        The script/narration text to convert to speech.
    output_path:
        Where to save the .mp3 file.
    voice:
        Edge-TTS voice name. Defaults to env ``EDGE_TTS_VOICE`` or
        ``en-US-GuyNeural``.
    rate:
        Speed adjustment (e.g. ``"+10%"`` for faster, ``"-5%"`` for slower).
    pitch:
        Pitch adjustment (e.g. ``"+2Hz"``).

    Returns
    -------
    Path
        The path to the generated audio file.
    """
    voice = voice or get_voice()
    return asyncio.run(_generate_async(text, output_path, voice, rate, pitch))


def _split_long_cues(cues: list, max_chars: int = 90) -> list:
    """Split overly long subtitle cues into readable chunks.

    Edge-TTS emits one cue per sentence, and documentary sentences run long —
    half of them exceeded 120 characters in practice, producing five-line
    subtitle walls that cover the footage. Each oversized cue is broken at word
    boundaries into near-equal pieces, with its time span divided in proportion
    to the characters spoken.
    """
    if not cues:
        return cues

    subtitle_class = type(cues[0])
    result = []

    for cue in cues:
        text = cue.content.strip()
        if len(text) <= max_chars:
            result.append(cue)
            continue

        # Choose a chunk count that keeps every piece under the limit.
        pieces_wanted = math.ceil(len(text) / max_chars)
        target_size = math.ceil(len(text) / pieces_wanted)

        chunks: list[str] = []
        current = ""
        for word in text.split():
            candidate = f"{current} {word}".strip()
            if current and len(candidate) > target_size:
                chunks.append(current)
                current = word
            else:
                current = candidate
        if current:
            chunks.append(current)

        # Greedy packing can leave a runt at the end ("arches.") that would
        # flash by unreadably. Prefer merging it back; when the merge would
        # break the limit, shift words across the boundary instead so both
        # pieces end up a sensible length.
        if len(chunks) > 1 and len(chunks[-1]) < target_size // 2:
            if len(chunks[-2]) + len(chunks[-1]) + 1 <= max_chars:
                chunks[-2] = f"{chunks[-2]} {chunks[-1]}"
                chunks.pop()
            else:
                previous_words = chunks[-2].split()
                while (
                    len(previous_words) > 1
                    and len(chunks[-1]) < target_size // 2
                    and len(chunks[-1]) + len(previous_words[-1]) + 1 <= max_chars
                ):
                    moved = previous_words.pop()
                    chunks[-1] = f"{moved} {chunks[-1]}"
                chunks[-2] = " ".join(previous_words)

        # Divide the cue's time span proportionally to each chunk's length.
        total_chars = sum(len(c) for c in chunks) or 1
        span = cue.end - cue.start
        offset = cue.start
        for chunk in chunks:
            share = span * (len(chunk) / total_chars)
            result.append(
                subtitle_class(
                    index=len(result) + 1,
                    start=offset,
                    end=offset + share,
                    content=chunk,
                )
            )
            offset += share
        # Absorb rounding drift so the last chunk lands exactly on the cue end.
        result[-1].end = cue.end

    for position, cue in enumerate(result, start=1):
        cue.index = position

    return result


def _fix_overlapping_cues(cues: list) -> None:
    """Clamp each cue's end time so it never runs into the next cue.

    Edge-TTS sentence boundaries occasionally overlap by a few tens of
    milliseconds, which makes two subtitles flash on screen at once. Trimming
    the earlier cue is safe: the text was already fully spoken by then.
    """
    for current, following in zip(cues, cues[1:]):
        if current.end > following.start:
            current.end = following.start


async def _generate_with_subtitles_async(
    text: str,
    audio_path: Path,
    subtitle_path: Path,
    voice: str,
    rate: str,
) -> tuple[Path, Path]:
    """Generate audio and SRT subtitles simultaneously."""
    try:
        import edge_tts
    except ImportError:
        raise RuntimeError("edge-tts is not installed.")

    communicate = edge_tts.Communicate(text, voice, rate=rate)
    submaker = edge_tts.SubMaker()

    audio_path.parent.mkdir(parents=True, exist_ok=True)
    subtitle_path.parent.mkdir(parents=True, exist_ok=True)

    boundary_count = 0
    with open(audio_path, "wb") as audio_file:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_file.write(chunk["data"])
            elif chunk["type"] in ("WordBoundary", "SentenceBoundary"):
                # edge-tts defaults to SentenceBoundary and never emits
                # WordBoundary unless explicitly requested. Listening for only
                # one of them silently produced empty (0 byte) subtitles.
                submaker.feed(chunk)
                boundary_count += 1

    _fix_overlapping_cues(submaker.cues)
    submaker.cues = _split_long_cues(submaker.cues)

    srt_content = submaker.get_srt()
    if not srt_content.strip():
        logger.warning(
            "No subtitle cues produced for %s (%d boundary events received). "
            "Subtitle file will be empty.",
            audio_path.name, boundary_count,
        )
    subtitle_path.write_text(srt_content, encoding="utf-8")

    logger.info(
        "TTS + subtitles saved: %s, %s (%d cues)",
        audio_path.name, subtitle_path.name, len(submaker.cues),
    )
    return audio_path, subtitle_path


def generate_with_subtitles(
    text: str,
    audio_path: Path,
    subtitle_path: Path,
    *,
    voice: str | None = None,
    rate: str = "+0%",
) -> tuple[Path, Path]:
    """Generate narration audio and matching sentence-timed subtitles.

    Returns
    -------
    tuple[Path, Path]
        (audio_path, subtitle_path) — an .mp3 and an .srt file.
    """
    voice = voice or get_voice()
    return asyncio.run(
        _generate_with_subtitles_async(text, audio_path, subtitle_path, voice, rate)
    )


async def list_voices_async(language: str = "en") -> list[dict]:
    """List available Edge-TTS voices for a language."""
    try:
        import edge_tts
    except ImportError:
        raise RuntimeError("edge-tts is not installed.")

    voices = await edge_tts.list_voices()
    return [
        {"name": v["ShortName"], "gender": v["Gender"], "locale": v["Locale"]}
        for v in voices
        if v["Locale"].startswith(language)
    ]


def list_voices(language: str = "en") -> list[dict]:
    """List available voices for a language (sync wrapper)."""
    return asyncio.run(list_voices_async(language))
