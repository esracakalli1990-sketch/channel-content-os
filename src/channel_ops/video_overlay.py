"""Burn a short hook line over the opening seconds of a clip.

The videos carry no speech and no on-screen text, so a viewer scrolling past
sees a metal object in a palm and has no reason to wait. A few words in the
first seconds give them one.

Text is drawn with Pillow and composited by ffmpeg's ``overlay`` rather than
its ``drawtext`` filter: the runner has no system ffmpeg, and the portable
build that ships with ``imageio-ffmpeg`` is compiled without drawtext. Drawing
the layer ourselves also gives control over the outline, which matters because
the text sits over whatever wood grain happens to be behind it.

Every failure here returns the original clip. A missing hook costs a little
reach; a failed publish costs the whole day's video.
"""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# How long the hook stays on screen. Long enough to read, gone before the
# transformation — which is the part people came for — begins.
HOOK_SECONDS = 2.5

# Shorts overlays its own UI along the bottom and right edges; 13% down the
# frame keeps the text clear of both.
HOOK_TOP_RATIO = 0.13
FONT_PATH_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
)


class OverlayUnavailable(RuntimeError):
    """Raised when the clip cannot be rendered with a hook."""


def _ffmpeg() -> str:
    try:
        import imageio_ffmpeg
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise OverlayUnavailable("imageio-ffmpeg is not installed") from exc
    return imageio_ffmpeg.get_ffmpeg_exe()


def _font_path() -> str:
    for candidate in FONT_PATH_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    raise OverlayUnavailable("No DejaVu Sans Bold font found on this machine")


def video_size(path: Path) -> tuple[int, int]:
    """Return the clip's (width, height)."""
    result = subprocess.run(
        [_ffmpeg(), "-hide_banner", "-i", str(path)],
        capture_output=True, text=True, timeout=60,
    )
    # ffmpeg reports stream details on stderr and exits non-zero without an
    # output file, so the dimensions are parsed from that rather than trusting
    # the return code.
    for token in result.stderr.split():
        if "x" in token and token.rstrip(",").replace("x", "").isdigit():
            width, _, height = token.rstrip(",").partition("x")
            if int(width) > 100 and int(height) > 100:
                return int(width), int(height)
    raise OverlayUnavailable("Could not read the clip's dimensions")


def render_text_layer(text: str, width: int, height: int, destination: Path) -> Path:
    """Draw *text* as a transparent PNG sized for the clip."""
    from PIL import Image, ImageDraw, ImageFont

    font_path = _font_path()
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    # Shrink until the line fits with a margin, so a longer hook stays on one
    # line instead of running off the frame.
    size = int(width / 11.6)
    while size > 12:
        font = ImageFont.truetype(font_path, size)
        if draw.textlength(text, font=font) <= width * 0.86:
            break
        size -= 2

    text_width = draw.textlength(text, font=font)
    x, y = (width - text_width) / 2, height * HOOK_TOP_RATIO
    outline = max(3, size // 15)

    # A solid outline rather than a drop shadow: the background is unpredictable
    # wood grain and a one-sided shadow leaves parts of the letters unreadable.
    for dx in range(-outline, outline + 1):
        for dy in range(-outline, outline + 1):
            if dx * dx + dy * dy <= outline * outline:
                draw.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0, 220))
    draw.text((x, y), text, font=font, fill=(255, 255, 255, 255))

    layer.save(destination)
    return destination


def add_hook(source: Path, destination: Path, text: str) -> Path:
    """Return a copy of *source* with *text* over its opening seconds.

    Raises :class:`OverlayUnavailable` if the clip cannot be rendered; callers
    are expected to fall back to the untouched original.
    """
    text = " ".join(text.split())
    if not text:
        raise OverlayUnavailable("No hook text to draw")

    width, height = video_size(source)
    layer = destination.with_suffix(".hook.png")
    render_text_layer(text, width, height, layer)

    command = [
        _ffmpeg(), "-y", "-v", "error",
        "-i", str(source),
        "-i", str(layer),
        "-filter_complex", f"[0:v][1:v]overlay=0:0:enable='lt(t,{HOOK_SECONDS})'",
        "-c:a", "copy",           # the mechanical audio is untouched
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-movflags", "+faststart",
        str(destination),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=600)
    layer.unlink(missing_ok=True)

    if result.returncode != 0 or not destination.exists():
        raise OverlayUnavailable(f"ffmpeg failed: {result.stderr.strip()[:200]}")

    logger.info("Burned hook %r onto %s", text, destination.name)
    return destination
