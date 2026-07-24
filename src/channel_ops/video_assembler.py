"""Video assembly pipeline using FFmpeg.

Combines narration audio, background visuals, subtitles, and music
into a final video file. Requires FFmpeg installed on the system.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


def _check_ffmpeg() -> str:
    """Verify FFmpeg is available and return its path."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError(
            "FFmpeg is not installed or not in PATH.\n"
            "Download from https://ffmpeg.org/download.html\n"
            "Windows: winget install FFmpeg"
        )
    return ffmpeg


def _run_ffmpeg(args: list[str], *, timeout: int = 600) -> subprocess.CompletedProcess:
    """Run an FFmpeg command with error handling."""
    ffmpeg = _check_ffmpeg()
    cmd = [ffmpeg, "-y"] + args  # -y = overwrite output
    logger.info("FFmpeg: %s", " ".join(cmd))
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        logger.error("FFmpeg stderr: %s", result.stderr[-500:] if result.stderr else "")
        raise RuntimeError(f"FFmpeg failed (exit {result.returncode}): {result.stderr[-200:]}")
    return result


def get_media_duration(file_path: Path) -> float:
    """Get the duration of an audio/video file in seconds."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe not found. Install FFmpeg.")
    result = subprocess.run(
        [ffprobe, "-v", "quiet", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(file_path)],
        capture_output=True, text=True, timeout=30,
    )
    return float(result.stdout.strip())


def create_slideshow(
    image_paths: list[Path],
    duration_per_image: float,
    output_path: Path,
    *,
    resolution: str = "1920x1080",
    fps: int = 30,
) -> Path:
    """Create a slideshow video from a list of images with Ken Burns effect.

    Each image is shown for *duration_per_image* seconds with a subtle
    zoom-in animation for visual interest.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Build a concat file for FFmpeg
    concat_file = output_path.parent / "_concat_list.txt"
    lines = []
    for img in image_paths:
        lines.append(f"file '{img.as_posix()}'")
        lines.append(f"duration {duration_per_image}")
    # Repeat last image to avoid cut
    if image_paths:
        lines.append(f"file '{image_paths[-1].as_posix()}'")
    concat_file.write_text("\n".join(lines), encoding="utf-8")

    w, h = resolution.split("x")
    _run_ffmpeg([
        "-f", "concat", "-safe", "0", "-i", str(concat_file),
        "-vf", (
            f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,"
            f"zoompan=z='min(zoom+0.0015,1.3)':d={int(fps * duration_per_image)}"
            f":s={resolution}:fps={fps}"
        ),
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-r", str(fps),
        str(output_path),
    ])
    concat_file.unlink(missing_ok=True)
    logger.info("Slideshow created: %s (%d images)", output_path.name, len(image_paths))
    return output_path


def concat_video_clips(
    clip_paths: list[Path],
    output_path: Path,
    *,
    resolution: str = "1280x720",
    fps: int = 30,
    seconds_per_clip: float = 6.0,
    preset: str = "ultrafast",
) -> Path:
    """Concatenate stock video clips into a single silent background reel.

    Source clips come from different providers with mixed resolutions, frame
    rates and codecs, so each one is first trimmed to *seconds_per_clip* and
    normalized (scaled + padded, audio stripped) into a temporary file. The
    normalized parts are then joined with FFmpeg's concat demuxer.

    Clips that fail to normalize are skipped; the reel is built from whatever
    succeeds. Raises RuntimeError only if no clip could be processed at all.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    work_dir = output_path.parent / "_stock_parts"
    work_dir.mkdir(parents=True, exist_ok=True)

    w, h = resolution.split("x")
    normalized: list[Path] = []

    for index, clip in enumerate(clip_paths):
        part = work_dir / f"part_{index:03d}.mp4"
        try:
            _run_ffmpeg([
                "-t", str(seconds_per_clip),
                "-i", str(clip),
                "-an",  # drop audio — narration is added later
                "-vf", (
                    f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                    f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,"
                    f"setsar=1,fps={fps}"
                ),
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-preset", preset,
                str(part),
            ])
        except RuntimeError as exc:
            logger.warning("Skipping unusable stock clip %s: %s", clip.name, exc)
            continue
        normalized.append(part)

    if not normalized:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise RuntimeError("No stock clips could be normalized for the background reel.")

    concat_file = work_dir / "_parts_list.txt"
    concat_file.write_text(
        "\n".join(f"file '{p.as_posix()}'" for p in normalized),
        encoding="utf-8",
    )

    # All parts share codec/resolution/fps now, so a stream copy is safe.
    _run_ffmpeg([
        "-f", "concat", "-safe", "0", "-i", str(concat_file),
        "-c", "copy",
        str(output_path),
    ])

    shutil.rmtree(work_dir, ignore_errors=True)
    logger.info("Background reel created: %s (%d clips)", output_path.name, len(normalized))
    return output_path


def build_timed_reel(
    segments: list[tuple[Path, float]],
    output_path: Path,
    *,
    resolution: str = "1280x720",
    fps: int = 30,
    preset: str = "ultrafast",
) -> Path:
    """Build a background reel where each clip fills an exact time slot.

    *segments* is a list of ``(clip_path, duration_seconds)``. Every clip is
    normalized and stretched to occupy precisely its slot: clips shorter than
    the slot are looped, longer ones are cut. The result therefore matches the
    narration timeline instead of being looped blindly underneath it.

    Segments whose clip fails to render are skipped; their time is absorbed by
    the remaining ones only in the sense that the reel becomes shorter, so the
    caller should still guard the final duration.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    work_dir = output_path.parent / "_reel_parts"
    work_dir.mkdir(parents=True, exist_ok=True)

    w, h = resolution.split("x")
    parts: list[Path] = []

    for index, (clip, duration) in enumerate(segments):
        if duration <= 0:
            continue
        part = work_dir / f"seg_{index:03d}.mp4"
        try:
            _run_ffmpeg([
                "-stream_loop", "-1",       # short clips repeat to fill the slot
                "-i", str(clip),
                "-t", f"{duration:.3f}",    # ...and are cut at exactly the slot end
                "-an",
                "-vf", (
                    f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                    f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,"
                    f"setsar=1,fps={fps}"
                ),
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-preset", preset,
                str(part),
            ])
        except RuntimeError as exc:
            logger.warning("Skipping segment %d (%s): %s", index, clip.name, exc)
            continue
        parts.append(part)

    if not parts:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise RuntimeError("No timed segments could be rendered for the reel.")

    concat_file = work_dir / "_segments.txt"
    concat_file.write_text(
        "\n".join(f"file '{p.as_posix()}'" for p in parts),
        encoding="utf-8",
    )
    _run_ffmpeg([
        "-f", "concat", "-safe", "0", "-i", str(concat_file),
        "-c", "copy",
        str(output_path),
    ])

    shutil.rmtree(work_dir, ignore_errors=True)
    logger.info("Timed reel created: %s (%d segments)", output_path.name, len(parts))
    return output_path


def merge_audio_video(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
    *,
    bg_music_path: Path | None = None,
    music_volume: float = 0.08,
) -> Path:
    """Merge a video track with narration audio (and optional background music).

    The video is stretched/looped to match the audio duration.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    audio_duration = get_media_duration(audio_path)

    inputs = ["-stream_loop", "-1", "-i", str(video_path), "-i", str(audio_path)]
    filter_parts = []

    if bg_music_path and bg_music_path.exists():
        inputs.extend(["-stream_loop", "-1", "-i", str(bg_music_path)])
        # Mix narration + quiet background music
        filter_parts.append(
            f"[1:a]volume=1.0[narration];"
            f"[2:a]volume={music_volume}[music];"
            f"[narration][music]amix=inputs=2:duration=shortest[aout]"
        )
        audio_map = ["-map", "0:v", "-map", "[aout]"]
    else:
        audio_map = ["-map", "0:v", "-map", "1:a"]

    args = inputs
    if filter_parts:
        args.extend(["-filter_complex", ";".join(filter_parts)])
    args.extend(audio_map)
    args.extend([
        "-c:v", "libx264", "-c:a", "aac",
        "-t", str(audio_duration),
        "-shortest",
        str(output_path),
    ])

    _run_ffmpeg(args)
    logger.info("Merged video+audio: %s (%.1fs)", output_path.name, audio_duration)
    return output_path


def burn_subtitles(
    video_path: Path,
    subtitle_path: Path,
    output_path: Path,
    *,
    font_size: int = 24,
    font_color: str = "white",
    outline_color: str = "black",
    margin_v: int = 40,
) -> Path:
    """Burn subtitles (SRT/VTT/ASS) into the video."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Escape backslashes and colons for FFmpeg subtitle filter on Windows
    sub_str = str(subtitle_path).replace("\\", "/").replace(":", "\\:")

    _run_ffmpeg([
        "-i", str(video_path),
        "-vf", (
            f"subtitles='{sub_str}'"
            f":force_style='FontSize={font_size},"
            f"PrimaryColour=&H00FFFFFF,"
            f"OutlineColour=&H00000000,"
            f"Outline=2,MarginV={margin_v}'"
        ),
        "-c:a", "copy",
        str(output_path),
    ])
    logger.info("Subtitles burned: %s", output_path.name)
    return output_path


def create_vertical_crop(
    input_path: Path,
    output_path: Path,
    *,
    resolution: str = "1080x1920",
) -> Path:
    """Crop a landscape video to vertical (9:16) for YouTube Shorts."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    w, h = resolution.split("x")
    _run_ffmpeg([
        "-i", str(input_path),
        "-vf", f"crop=ih*9/16:ih,scale={w}:{h}",
        "-c:a", "copy",
        str(output_path),
    ])
    logger.info("Vertical crop: %s", output_path.name)
    return output_path


def extract_clip(
    input_path: Path,
    output_path: Path,
    start_seconds: float,
    duration_seconds: float,
) -> Path:
    """Extract a clip from a video (for Shorts/highlights)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg([
        "-ss", str(start_seconds),
        "-i", str(input_path),
        "-t", str(duration_seconds),
        "-c", "copy",
        str(output_path),
    ])
    logger.info("Clip extracted: %s (%.1fs-%.1fs)", output_path.name, start_seconds, start_seconds + duration_seconds)
    return output_path
