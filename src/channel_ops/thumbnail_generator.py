"""Automated thumbnail generator using Pillow.

Creates YouTube thumbnails by overlaying large, high-contrast text
onto a background image. Supports basic word wrapping and centering.
"""
from __future__ import annotations

import logging
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

logger = logging.getLogger(__name__)

# Standard YouTube thumbnail size
THUMBNAIL_SIZE = (1280, 720)


def generate_thumbnail(
    background_path: Path,
    output_path: Path,
    text: str,
    *,
    font_path: str | None = None,
    font_size: int = 100,
    text_color: str = "white",
    stroke_color: str = "black",
    stroke_width: int = 4,
) -> Path:
    """Generate a thumbnail with text overlay.

    Parameters
    ----------
    background_path:
        Path to the base image (will be resized/cropped to 1280x720).
    output_path:
        Where to save the generated thumbnail (.jpg or .png).
    text:
        The text to display on the thumbnail.
    font_path:
        Path to a .ttf or .otf font. Falls back to default if None.
    font_size:
        Size of the font.
    text_color:
        Primary color of the text.
    stroke_color:
        Color of the outline around the text for readability.
    stroke_width:
        Thickness of the text outline.
    """
    if not PIL_AVAILABLE:
        raise RuntimeError("Pillow is not installed. Run: pip install Pillow")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(background_path) as img:
        # Resize and crop to 1280x720 (center crop)
        img_ratio = img.width / img.height
        target_ratio = THUMBNAIL_SIZE[0] / THUMBNAIL_SIZE[1]

        if img_ratio > target_ratio:
            # Image is wider than target
            new_height = THUMBNAIL_SIZE[1]
            new_width = int(new_height * img_ratio)
        else:
            # Image is taller than target
            new_width = THUMBNAIL_SIZE[0]
            new_height = int(new_width / img_ratio)

        img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        
        left = (new_width - THUMBNAIL_SIZE[0]) // 2
        top = (new_height - THUMBNAIL_SIZE[1]) // 2
        right = left + THUMBNAIL_SIZE[0]
        bottom = top + THUMBNAIL_SIZE[1]
        
        img = img.crop((left, top, right, bottom))
        
        # Draw text
        draw = ImageDraw.Draw(img)
        try:
            if font_path and Path(font_path).exists():
                font = ImageFont.truetype(str(font_path), font_size)
            else:
                # Attempt to use a basic bold font if on Windows, else default
                font = ImageFont.truetype("arialbd.ttf", font_size)
        except Exception:
            font = ImageFont.load_default()

        # Wrap text if needed (basic heuristic)
        words = text.split()
        lines = []
        current_line = []
        
        for word in words:
            current_line.append(word)
            # Estimate width
            w = draw.textlength(" ".join(current_line), font=font)
            if w > THUMBNAIL_SIZE[0] * 0.9:  # Keep 10% margin
                current_line.pop()
                lines.append(" ".join(current_line))
                current_line = [word]
        if current_line:
            lines.append(" ".join(current_line))
            
        wrapped_text = "\n".join(lines)
        
        # Calculate bounding box to center text
        bbox = draw.multiline_textbbox((0, 0), wrapped_text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        
        x = (THUMBNAIL_SIZE[0] - text_width) // 2
        y = (THUMBNAIL_SIZE[1] - text_height) // 2
        
        # Draw stroke and text
        draw.multiline_text(
            (x, y),
            wrapped_text,
            font=font,
            fill=text_color,
            stroke_width=stroke_width,
            stroke_fill=stroke_color,
            align="center"
        )
        
        img.save(output_path, quality=90)
    
    logger.info("Generated thumbnail: %s", output_path.name)
    return output_path
