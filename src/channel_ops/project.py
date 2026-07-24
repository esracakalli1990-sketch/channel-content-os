from __future__ import annotations

import csv
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from .workflow import validate_transition

logger = logging.getLogger(__name__)


# Canonical production folder names — the single source of truth.
# Always import these instead of hard-coding the strings, otherwise the
# pipeline drifts into inventing parallel folders (see HANDOVER notes).
TOPIC_DIR = "01_topic"
MARKET_DIR = "02_market"
RESEARCH_DIR = "03_research"
SCRIPT_DIR = "04_script"
VISUALS_DIR = "05_visuals"    # stock footage + asset_manifest.csv
VOICE_DIR = "06_voice"        # narration.mp3 + subs.srt
EDIT_DIR = "07_edit"          # intermediate renders (raw, merged)
EXPORT_DIR = "08_export"      # the deliverable: final.mp4
YOUTUBE_DIR = "09_youtube"
ANALYTICS_DIR = "10_analytics"

VIDEO_FOLDERS = (
    TOPIC_DIR,
    MARKET_DIR,
    RESEARCH_DIR,
    SCRIPT_DIR,
    VISUALS_DIR,
    VOICE_DIR,
    EDIT_DIR,
    EXPORT_DIR,
    YOUTUBE_DIR,
    ANALYTICS_DIR,
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def video_path(root: Path, video_id: str) -> Path:
    return root / "videos" / video_id


def initialize_video(root: Path, video_id: str, topic: str) -> Path:
    path = video_path(root, video_id)
    if path.exists():
        raise FileExistsError(f"Video already exists: {video_id}")
    for folder in VIDEO_FOLDERS:
        (path / folder).mkdir(parents=True, exist_ok=True)
    template = (root / "templates" / "production_pack.md").read_text(encoding="utf-8")
    (path / "PRODUCTION_PACK.md").write_text(template.replace("{{VIDEO_ID}}", video_id), encoding="utf-8")
    manifest = {
        "video_id": video_id,
        "topic": topic,
        "status": "idea",
        "created_at": utc_now(),
        "history": [{"at": utc_now(), "from": None, "to": "idea", "note": "Video initialized"}],
    }
    (path / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    _create_csv_headers(path)
    return path


def _create_csv_headers(path: Path) -> None:
    files = {
        f"{RESEARCH_DIR}/source_ledger.csv": ["claim_id", "claim", "source_url", "publisher", "accessed_at", "primary_source", "verified", "notes"],
        f"{VISUALS_DIR}/asset_manifest.csv": ["asset_id", "description", "source_url", "license", "commercial_use_confirmed", "evidence_path", "status"],
    }
    for relative_path, headers in files.items():
        with (path / relative_path).open("w", newline="", encoding="utf-8") as file:
            csv.writer(file).writerow(headers)


def load_manifest(root: Path, video_id: str) -> tuple[Path, dict]:
    path = video_path(root, video_id)
    manifest_path = path / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Unknown video: {video_id}")
    return manifest_path, json.loads(manifest_path.read_text(encoding="utf-8"))


def transition_video(root: Path, video_id: str, target: str, approval_note: str | None = None) -> dict:
    manifest_path, manifest = load_manifest(root, video_id)
    result = validate_transition(manifest["status"], target, approval_note)
    if not result.allowed:
        raise ValueError(result.message)
    manifest["history"].append({"at": utc_now(), "from": manifest["status"], "to": target, "note": approval_note or "Automated workflow transition"})
    manifest["status"] = target
    manifest["updated_at"] = utc_now()
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def status_summary(root: Path) -> list[dict]:
    videos = root / "videos"
    if not videos.exists():
        return []
    result = []
    for manifest_path in sorted(videos.glob("*/manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        result.append({"video_id": manifest["video_id"], "status": manifest["status"], "topic": manifest["topic"]})
    return result

