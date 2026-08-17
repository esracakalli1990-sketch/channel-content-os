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

# The badge sits in the top-left for the whole clip. 219,000 views produced 83
# subscribers — about a tenth of what this format normally converts — because
# nothing on screen ever told a viewer there was a channel behind the video.
#
# It is a corner mark rather than a closing card on purpose: the clips settle
# into a still pose so they loop without a jolt, and a card over that last
# second would break the loop. The series number carries the "there are more
# of these" idea instead, at no cost to the loop.
BADGE_TOP_RATIO = 0.055
BADGE_LEFT_RATIO = 0.055
BADGE_WIDTH_RATIO = 1 / 26  # font size relative to frame width
BADGE_ALPHA = 205


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


def render_badge_layer(text: str, width: int, height: int, destination: Path) -> Path:
    """Draw the corner badge as a transparent PNG sized for the clip."""
    from PIL import Image, ImageDraw, ImageFont

    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    size = max(12, int(width * BADGE_WIDTH_RATIO))
    font = ImageFont.truetype(_font_path(), size)

    x, y = width * BADGE_LEFT_RATIO, height * BADGE_TOP_RATIO
    outline = max(2, size // 12)
    # Same solid outline as the hook: the badge sits over whatever the clip
    # happens to show in that corner, which is not always dark wood.
    for dx in range(-outline, outline + 1):
        for dy in range(-outline, outline + 1):
            if dx * dx + dy * dy <= outline * outline:
                draw.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0, 160))
    draw.text((x, y), text, font=font, fill=(255, 255, 255, BADGE_ALPHA))

    layer.save(destination)
    return destination


def add_hook(source: Path, destination: Path, text: str, badge: str = "") -> Path:
    """Return a copy of *source* with the hook and the channel badge drawn on.

    *text* covers the opening seconds; *badge* stays for the whole clip and may
    be empty. Both are composited in one encode — running ffmpeg twice would
    re-compress the clip a second time for nothing.

    Raises :class:`OverlayUnavailable` if the clip cannot be rendered; callers
    are expected to fall back to the untouched original.
    """
    text = " ".join(text.split())
    badge = " ".join(badge.split())
    if not text and not badge:
        raise OverlayUnavailable("Nothing to draw")

    width, height = video_size(source)
    inputs: list[str] = []
    layers: list[Path] = []
    steps: list[str] = []

    if text:
        hook_layer = destination.with_suffix(".hook.png")
        render_text_layer(text, width, height, hook_layer)
        layers.append(hook_layer)
        steps.append(f"overlay=0:0:enable='lt(t,{HOOK_SECONDS})'")
    if badge:
        badge_layer = destination.with_suffix(".badge.png")
        render_badge_layer(badge, width, height, badge_layer)
        layers.append(badge_layer)
        steps.append("overlay=0:0")

    # [0:v][1:v]overlay…[v1];[v1][2:v]overlay… — each layer feeds the next.
    chain: list[str] = []
    stream = "0:v"
    for index, step in enumerate(steps, start=1):
        label = f"v{index}" if index < len(steps) else ""
        suffix = f"[{label}]" if label else ""
        chain.append(f"[{stream}][{index}:v]{step}{suffix}")
        stream = label
    for layer in layers:
        inputs += ["-i", str(layer)]

    command = [
        _ffmpeg(), "-y", "-v", "error",
        "-i", str(source), *inputs,
        "-filter_complex", ";".join(chain),
        "-c:a", "copy",           # the mechanical audio is untouched
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-movflags", "+faststart",
        str(destination),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=600)
    for layer in layers:
        layer.unlink(missing_ok=True)

    if result.returncode != 0 or not destination.exists():
        raise OverlayUnavailable(f"ffmpeg failed: {result.stderr.strip()[:200]}")

    logger.info("Burned hook %r and badge %r onto %s", text, badge, destination.name)
    return destination
