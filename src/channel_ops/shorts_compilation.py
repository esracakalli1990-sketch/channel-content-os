"""Long-form compilations built from clips the channel has already published.

Why this exists: the channel is Shorts-only, and Shorts watch time does not
count toward the 4,000 valid public watch hours the Partner Programme asks for.
The other route — ten million Shorts views in ninety days — needs about 111,000
views a day against the 10,800 the channel makes, and closing a tenfold gap by
making better ten-second clips is not a plan. A single long video that holds a
hundred thousand viewers for three minutes clears the watch-hour threshold on
its own, and the channel has already put 132,813 views on one Short.

The raw material costs nothing: every clip was sent through Telegram, and
Telegram keeps file ids valid indefinitely. The ids live in this repository's
history (and, from now on, in the publish record), so a compilation is assembled
from videos that were already made and already paid for.
"""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from . import notifications, telegram_inbox, youtube_uploader
from .config_loader import find_project_root
from .youtube_auth import get_access_token

logger = logging.getLogger(__name__)

PUBLISHED_FILE = "data/shorts_published.json"
LONG_PUBLISHED_FILE = "data/long_published.json"

# 1080p landscape. The clips are 9:16, so they sit centred over a blurred copy
# of themselves rather than black bars: the same frame, scaled to cover and
# defocused, which reads as deliberate instead of like a phone video dropped
# into a wide frame.
CANVAS_W, CANVAS_H = 1920, 1080

# Long enough to be unmistakably long-form rather than a Short, short enough
# that the tail is still watched. Ten to twelve minutes is where this kind of
# ambient compilation sits.
TARGET_MINUTES = 11

# YouTube only renders a chapter that runs at least ten seconds, and the clips
# are nine to eleven. A chapter therefore covers one clip when that clip is
# long enough and two when it is not.
MIN_CHAPTER_SECONDS = 10

CHANNEL_HANDLE = "@unfoldableslab"

# The opening decides whether the rest is watched at all, so the best-performing
# clips go first; after that a strong one is dealt in regularly rather than
# letting the video decay from best to worst.
OPENERS = 3
STRONG_EVERY = 4


@dataclass
class Clip:
    creature: str
    file_id: str
    file_size: int
    views: int
    path: Path | None = None
    seconds: float = 0.0


class CompilationError(RuntimeError):
    """Raised when a compilation cannot be produced or published."""


def _ffmpeg() -> str:
    try:
        import imageio_ffmpeg
    except ImportError as exc:  # pragma: no cover - environment problem
        raise CompilationError("imageio-ffmpeg is not installed") from exc
    return imageio_ffmpeg.get_ffmpeg_exe()


def _read_json(path: Path, fallback):
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return fallback


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True
    )
    return result.stdout if result.returncode == 0 else ""


def recover_file_ids(root: Path) -> dict[str, tuple[str, int]]:
    """Every clip's Telegram id, keyed by creature.

    Clips published before the release queue existed were never recorded with
    an id and cannot be recovered; the rest are read from the publish record
    first and, for the ones written before the record carried an id, from the
    queue file's history. A shallow checkout has no history, so the workflow
    that runs this asks for the full one.
    """
    found: dict[str, tuple[str, int]] = {}

    for entry in _read_json(_path(PUBLISHED_FILE, root), []):
        if entry.get("file_id"):
            found[entry["creature"]] = (entry["file_id"], int(entry.get("file_size", 0)))

    commits = _git(root, "log", "--format=%H", "--", "data/shorts_queue.json").split()
    for commit in commits:
        raw = _git(root, "show", f"{commit}:data/shorts_queue.json")
        if not raw:
            continue
        try:
            queue = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for item in queue:
            creature = (item.get("concept") or {}).get("creature")
            if creature and item.get("file_id"):
                found.setdefault(creature, (item["file_id"], int(item.get("file_size", 0))))
    return found


def _path(name: str, root: Path) -> Path:
    return root / name


def fetch_views(video_ids: list[str]) -> dict[str, int]:
    """View counts for the published videos, fifty ids per call."""
    token = get_access_token()
    counts: dict[str, int] = {}
    for start in range(0, len(video_ids), 50):
        params = urlencode({"part": "statistics", "id": ",".join(video_ids[start:start + 50])})
        request = Request(
            f"https://www.googleapis.com/youtube/v3/videos?{params}",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urlopen(request, timeout=60) as response:
            payload = json.load(response)
        for item in payload.get("items", []):
            counts[item["id"]] = int(item.get("statistics", {}).get("viewCount", 0))
    return counts


def choose_clips(
    root: Path, minutes: int = TARGET_MINUTES, reuse: bool = False
) -> list[Clip]:
    """The clips to use, in the order they should play.

    Clips already spent on an earlier compilation are held back. Picking purely
    by view count would otherwise make every compilation a near-copy of the last
    one, which is duplicate content and earns exactly the treatment it deserves.
    The catalogue grows by two clips a day, so a fresh compilation is possible
    roughly monthly; when there are not enough unused clips the job refuses
    rather than quietly shipping a re-run.
    """
    published = _read_json(_path(PUBLISHED_FILE, root), [])
    ids = recover_file_ids(root)

    wanted = [p for p in published if p.get("creature") in ids and p.get("youtube_video_id")]
    if not wanted:
        raise CompilationError("No published clip has a recoverable Telegram file id.")

    views = fetch_views([p["youtube_video_id"] for p in wanted])
    clips = [
        Clip(creature=p["creature"], file_id=ids[p["creature"]][0],
             file_size=ids[p["creature"]][1], views=views.get(p["youtube_video_id"], 0))
        for p in wanted
    ]
    clips.sort(key=lambda c: c.views, reverse=True)

    spent = already_used(root)
    fresh = [c for c in clips if c.creature not in spent]

    # Ten seconds a clip is close enough to plan with; the real durations are
    # measured after download.
    needed = max(1, round(minutes * 60 / 10))
    if len(fresh) >= needed:
        return _interleave(fresh[:needed])
    if not reuse:
        raise CompilationError(
            f"Only {len(fresh)} unused clips, {needed} wanted. Wait for the catalogue "
            f"to grow, shorten the compilation, or pass --reuse to repeat clips."
        )
    logger.warning("Reusing %d already-published clips to reach length", needed - len(fresh))
    topped_up = fresh + [c for c in clips if c.creature in spent][: needed - len(fresh)]
    return _interleave(topped_up)


def already_used(root: Path) -> set[str]:
    """Creatures that have appeared in a previous compilation."""
    spent: set[str] = set()
    for record in _read_json(_path(LONG_PUBLISHED_FILE, root), []):
        spent.update(record.get("creatures") or [])
    return spent


def _interleave(clips: list[Clip]) -> list[Clip]:
    """Best first, then a strong clip every few so the video does not decay."""
    if len(clips) <= OPENERS:
        return clips
    ordered = clips[:OPENERS]
    strong = clips[OPENERS:OPENERS + (len(clips) - OPENERS) // STRONG_EVERY]
    rest = clips[OPENERS + len(strong):]
    while strong or rest:
        for _ in range(STRONG_EVERY - 1):
            if rest:
                ordered.append(rest.pop(0))
        if strong:
            ordered.append(strong.pop(0))
    return ordered


def _duration(path: Path) -> float:
    result = subprocess.run(
        [_ffmpeg(), "-hide_banner", "-i", str(path)], capture_output=True, text=True
    )
    # ffmpeg writes stream details to stderr and exits non-zero with no output
    # file, which is expected here — the probe is the point.
    for line in result.stderr.splitlines():
        if "Duration:" in line:
            stamp = line.split("Duration:")[1].split(",")[0].strip()
            hours, minutes, seconds = stamp.split(":")
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    return 0.0


def _normalise(source: Path, destination: Path) -> None:
    """One clip, centred on a blurred copy of itself, at a uniform 1080p.

    Every clip is re-encoded to identical parameters so the join below can copy
    streams instead of encoding the whole compilation a second time.
    """
    blurred = (
        f"[0:v]split=2[bg][fg];"
        f"[bg]scale={CANVAS_W}:{CANVAS_H}:force_original_aspect_ratio=increase,"
        f"crop={CANVAS_W}:{CANVAS_H},boxblur=20:2[bgb];"
        f"[fg]scale=-2:{CANVAS_H}[fgs];"
        f"[bgb][fgs]overlay=(W-w)/2:0,fps=30,format=yuv420p[v]"
    )
    command = [
        _ffmpeg(), "-y", "-v", "error", "-i", str(source),
        "-filter_complex", blurred, "-map", "[v]",
        "-map", "0:a?", "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-video_track_timescale", "30000",
        str(destination),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0 or not destination.exists():
        raise CompilationError(f"ffmpeg failed on {source.name}: {result.stderr.strip()[:200]}")


def _join(parts: list[Path], destination: Path) -> None:
    listing = destination.parent / "parts.txt"
    listing.write_text(
        "".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8"
    )
    command = [
        _ffmpeg(), "-y", "-v", "error", "-f", "concat", "-safe", "0",
        "-i", str(listing), "-c", "copy", "-movflags", "+faststart", str(destination),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0 or not destination.exists():
        raise CompilationError(f"ffmpeg could not join the clips: {result.stderr.strip()[:200]}")


def build(clips: list[Clip], workspace: Path) -> tuple[Path, list[Clip]]:
    """Download, normalise and join. Returns the file and the clips that made it."""
    parts: list[Path] = []
    used: list[Clip] = []
    for index, clip in enumerate(clips):
        raw = workspace / f"raw{index:03d}.mp4"
        try:
            telegram_inbox.download_file(clip.file_id, clip.file_size, raw)
        except Exception as exc:  # noqa: BLE001 - one missing clip must not stop the build
            logger.warning("Skipping %s: %s", clip.creature, exc)
            continue
        part = workspace / f"part{index:03d}.mp4"
        try:
            _normalise(raw, part)
        except CompilationError as exc:
            logger.warning("Skipping %s: %s", clip.creature, exc)
            continue
        clip.path = part
        clip.seconds = _duration(part)
        parts.append(part)
        used.append(clip)

    if len(used) < 3:
        raise CompilationError(f"Only {len(used)} clips survived; not enough for a compilation.")

    destination = workspace / "compilation.mp4"
    _join(parts, destination)
    return destination, used


def chapters(clips: list[Clip]) -> list[tuple[int, str]]:
    """Chapter marks, merging clips too short to stand as their own chapter.

    A chapter shorter than ten seconds is not rendered at all, and the clips run
    nine to eleven, so a nine-second one is folded into its neighbour. Both
    creatures are then named: a chapter covering two clips but announcing one of
    them sends the viewer to the wrong place.
    """
    marks: list[tuple[int, list[str]]] = []
    elapsed = 0.0
    opened_at = -MIN_CHAPTER_SECONDS
    for clip in clips:
        if not marks or elapsed - opened_at >= MIN_CHAPTER_SECONDS:
            marks.append((int(elapsed), [clip.creature]))
            opened_at = elapsed
        else:
            marks[-1][1].append(clip.creature)
        elapsed += clip.seconds
    return [(at, " & ".join(c.title() for c in names)) for at, names in marks]


def describe(clips: list[Clip]) -> tuple[str, str]:
    """Title and description, including the chapter list."""
    count = len(clips)
    title = (
        f"{count} Impossible Mechanical Transformations "
        f"— Satisfying Unfolding Toys Compilation"
    )[:100]
    lines = [
        "Every one of these starts as a solid object resting in a palm. A button is "
        "pressed and it unfolds into a miniature mechanical creature — gears, linkages "
        "and all.",
        "",
    ]
    for seconds, label in chapters(clips):
        lines.append(f"{seconds // 60:02d}:{seconds % 60:02d} {label}")
    lines += [
        "",
        f"New transformations every day on {CHANNEL_HANDLE}.",
        "",
        "#mechanical #automata #satisfying #compilation #asmr",
    ]
    return title, "\n".join(lines)[:5000]


def publish(
    root: Path | None = None, minutes: int = TARGET_MINUTES, reuse: bool = False,
    privacy: str = "public",
) -> dict:
    """Build a compilation from the back catalogue and upload it as long-form."""
    import tempfile

    root = root or find_project_root()
    clips = choose_clips(root, minutes, reuse)
    logger.info("Chosen %d clips for the compilation", len(clips))

    with tempfile.TemporaryDirectory() as workspace:
        video, used = build(clips, Path(workspace))
        size_mb = video.stat().st_size / (1024 * 1024)
        total = sum(c.seconds for c in used)
        logger.info("Built %.1f minutes, %.0f MB from %d clips", total / 60, size_mb, len(used))

        title, description = describe(used)
        result = youtube_uploader.upload_video(
            video, title, description,
            tags=["mechanical", "automata", "satisfying", "compilation", "asmr",
                  "kinetic art", "transformation"],
            privacy=privacy,
            made_for_kids=False,
        )

    video_id = result.get("id", "")
    record = {
        "published_at": _now(),
        "youtube_video_id": video_id,
        "youtube_url": f"https://youtube.com/watch?v={video_id}",
        "title": title,
        "minutes": round(total / 60, 1),
        "clip_count": len(used),
        "size_mb": round(size_mb, 1),
        "privacy": privacy,
        "creatures": [c.creature for c in used],
    }
    _append(record, root)

    # Long-form lives or dies on the click. A failure here must not undo an
    # upload that already worked, so it is reported and left for the standalone
    # command to retry.
    try:
        set_thumbnail(root, video_id)
        record["thumbnail"] = True
    except Exception as exc:  # noqa: BLE001
        logger.exception("Thumbnail failed")
        notifications.send_message(f"⚠️ <b>Kapak konulamadı</b>\n{exc}")

    notifications.send_message(
        f"🎬 <b>Derleme yayınlandı</b>\n\n"
        f"<b>{title}</b>\n"
        f"{record['minutes']} dakika · {len(used)} klip · {privacy}\n"
        f"▶️ {record['youtube_url']}"
    )
    return record


def _now() -> str:
    from datetime import UTC, datetime
    return datetime.now(UTC).isoformat(timespec="seconds")


def _append(record: dict, root: Path) -> None:
    path = _path(LONG_PUBLISHED_FILE, root)
    history = _read_json(path, [])
    history.append(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Thumbnail
# ---------------------------------------------------------------------------
#
# Shorts need no thumbnail — the feed plays them. Long-form is the opposite:
# nothing is watched that is not first clicked, and YouTube's automatic pick is
# whatever frame it happens to like, which for a compilation of unfoldings is
# usually a blurred mid-transformation smear. The promise of this channel is
# "solid object becomes machine", so the thumbnail is that promise: the closed
# object on the left, what it became on the right.

THUMB_W, THUMB_H = 1280, 720
SEAM = 8

# Far enough in that the shell is still shut, and far enough along that the
# creature has finished unfolding. The clips run nine to eleven seconds.
BEFORE_AT = 0.3
AFTER_RATIO = 0.92


def _frame(video: Path, at: float, destination: Path) -> None:
    command = [
        _ffmpeg(), "-y", "-v", "error", "-ss", f"{at:.2f}", "-i", str(video),
        "-frames:v", "1", "-q:v", "2", str(destination),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0 or not destination.exists():
        raise CompilationError(f"Could not take a frame at {at:.1f}s: {result.stderr.strip()[:160]}")


def _fill(image, width: int, height: int):
    """Cover the box, cropping the overflow around the centre."""
    from PIL import Image

    scale = max(width / image.width, height / image.height)
    resized = image.resize(
        (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
        Image.LANCZOS,
    )
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    return resized.crop((left, top, left + width, top + height))


def build_thumbnail(clip: Path, count: int, destination: Path) -> Path:
    """A before-and-after thumbnail cut from the compilation's opening clip."""
    from PIL import Image, ImageDraw, ImageFont

    from .video_overlay import _font_path

    workspace = destination.parent
    before_png = workspace / "before.png"
    after_png = workspace / "after.png"
    length = _duration(clip) or 10.0
    _frame(clip, BEFORE_AT, before_png)
    _frame(clip, length * AFTER_RATIO, after_png)

    # The band is painted over the panels, so the panels stop short of it and
    # the seam does not run down through the lettering.
    band = 150
    panel_h = THUMB_H - band
    half = (THUMB_W - SEAM) // 2
    canvas = Image.new("RGB", (THUMB_W, THUMB_H), (255, 255, 255))
    canvas.paste(_fill(Image.open(before_png).convert("RGB"), half, panel_h), (0, 0))
    canvas.paste(
        _fill(Image.open(after_png).convert("RGB"), half, panel_h), (half + SEAM, 0)
    )

    draw = ImageDraw.Draw(canvas, "RGBA")

    # A caption band rather than text straight onto the frame: the clips are
    # shot on a pale wooden table and white letters vanish into it.
    #
    # Three words, not four. A YouTube thumbnail is about 170 pixels wide on a
    # phone, which is where nearly all of this channel's viewing happens, and
    # "66 MECHANICAL TRANSFORMATIONS" at that size is a grey smear. Fewer
    # characters buy bigger letters.
    draw.rectangle([(0, panel_h), (THUMB_W, THUMB_H)], fill=(12, 12, 14))
    font = ImageFont.truetype(_font_path(), 96)
    caption = f"{count} UNFOLDING MACHINES"
    while draw.textlength(caption, font=font) > THUMB_W * 0.90 and font.size > 40:
        font = ImageFont.truetype(_font_path(), font.size - 2)
    width = draw.textlength(caption, font=font)
    draw.text(
        ((THUMB_W - width) / 2, panel_h + (band - font.size) / 2 - 8),
        caption, font=font, fill=(255, 255, 255),
    )

    # The arrow sits on the seam and says which way to read the two halves.
    radius = 62
    centre = (THUMB_W // 2, panel_h // 2)
    draw.ellipse(
        [(centre[0] - radius, centre[1] - radius), (centre[0] + radius, centre[1] + radius)],
        fill=(255, 255, 255), outline=(0, 0, 0), width=5,
    )
    arrow = ImageFont.truetype(_font_path(), 86)
    glyph = "››"  # two single guillemets: present in DejaVu, reads as motion
    glyph_width = draw.textlength(glyph, font=arrow)
    draw.text(
        (centre[0] - glyph_width / 2, centre[1] - arrow.size * 0.62),
        glyph, font=arrow, fill=(15, 15, 15),
    )

    canvas.save(destination, "PNG", optimize=True)
    return destination


def preview_thumbnail(root: Path | None = None, video_id: str = "") -> str:
    """Build the thumbnail and return it base64-encoded, without attaching it.

    The image is made where the Telegram credentials are — inside the runner —
    so the only way to look at it before it goes on the channel is to carry it
    out through the job log. A JPEG at quality 88 keeps that log a few hundred
    kilobytes instead of a couple of megabytes; the thumbnail that actually
    gets uploaded is still the PNG.
    """
    import base64
    import tempfile

    root = root or find_project_root()
    record, opening, file_id, file_size = _compilation_clip(root, video_id)
    with tempfile.TemporaryDirectory() as workspace:
        clip = Path(workspace) / "opening.mp4"
        telegram_inbox.download_file(file_id, file_size, clip)
        image = build_thumbnail(clip, record["clip_count"], Path(workspace) / "thumb.png")

        from PIL import Image

        preview = Path(workspace) / "thumb.jpg"
        Image.open(image).convert("RGB").save(preview, "JPEG", quality=88, optimize=True)
        return base64.b64encode(preview.read_bytes()).decode("ascii")


def _compilation_clip(root: Path, video_id: str) -> tuple[dict, str, str, int]:
    """The record for a compilation and the Telegram id of its opening clip."""
    history = _read_json(_path(LONG_PUBLISHED_FILE, root), [])
    if not history:
        raise CompilationError("No compilation has been published yet.")
    record = next(
        (r for r in reversed(history) if r["youtube_video_id"] == video_id),
        history[-1] if not video_id else None,
    )
    if record is None:
        raise CompilationError(f"No compilation recorded for video {video_id}.")

    opening = (record.get("creatures") or [None])[0]
    ids = recover_file_ids(root)
    if opening not in ids:
        raise CompilationError(f"No Telegram file id for the opening clip ({opening}).")
    file_id, file_size = ids[opening]
    return record, opening, file_id, file_size


def set_thumbnail(root: Path | None = None, video_id: str = "") -> str:
    """Build and attach the thumbnail for a compilation.

    Defaults to the most recent one, so a compilation published before the
    thumbnail existed can be fixed without rebuilding the video.
    """
    import tempfile

    root = root or find_project_root()
    record, opening, file_id, file_size = _compilation_clip(root, video_id)
    with tempfile.TemporaryDirectory() as workspace:
        clip = Path(workspace) / "opening.mp4"
        telegram_inbox.download_file(file_id, file_size, clip)
        image = build_thumbnail(clip, record["clip_count"], Path(workspace) / "thumb.png")
        try:
            youtube_uploader.set_thumbnail(record["youtube_video_id"], image)
        except RuntimeError as exc:
            # A 403 in the youtube.thumbnail domain is not a bug to fix in code.
            # Custom thumbnails are switched off until the channel itself is
            # verified by phone, and no scope or retry gets past it — the upload
            # scope is clearly present, since the video went up with it.
            if "403" in str(exc):
                raise CompilationError(
                    "YouTube refused the thumbnail: this channel is not verified for "
                    "custom thumbnails yet. Verify it once at youtube.com/verify "
                    "(Studio > Settings > Channel > Feature eligibility), then run "
                    "shorts-thumbnail again. Nothing in the pipeline needs changing."
                ) from exc
            raise

    logger.info("Thumbnail set on %s from %s", record["youtube_video_id"], opening)
    return record["youtube_video_id"]
