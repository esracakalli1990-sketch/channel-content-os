"""CLI entry point for Channel Content OS."""
from __future__ import annotations

import argparse
import csv
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def _ensure_env() -> None:
    """Load .env file if python-dotenv is available."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass


def project_root() -> Path:
    """Find the project root by walking up to pyproject.toml or .git."""
    from .config_loader import find_project_root
    return find_project_root(Path(__file__))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="channel-os",
        description="Channel Content OS — YouTube documentary production system",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- status ---
    subparsers.add_parser("status", help="List all video workflow states")

    # --- init-video ---
    init = subparsers.add_parser("init-video", help="Create a standard Production Pack")
    init.add_argument("video_id")
    init.add_argument("--topic", required=True)

    # --- transition ---
    transition = subparsers.add_parser("transition", help="Move a video to its next workflow state")
    transition.add_argument("video_id")
    transition.add_argument("target")
    transition.add_argument("--approval-note", help="Required for human approval states")

    # --- score-topic ---
    score = subparsers.add_parser("score-topic", help="Store a weighted 0-100 topic score")
    score.add_argument("video_id")
    score.add_argument("--score", action="append", required=True, help="criterion=value (0-10)")

    # --- youtube-search ---
    search = subparsers.add_parser("youtube-search", help="Collect public YouTube search results")
    search.add_argument("video_id")
    search.add_argument("query")
    search.add_argument("--region", default="US")
    search.add_argument("--max-results", type=int, default=10)

    # --- add-source ---
    source = subparsers.add_parser("add-source", help="Add a source-backed claim to the ledger")
    source.add_argument("video_id")
    source.add_argument("--claim-id", required=True)
    source.add_argument("--claim", required=True)
    source.add_argument("--url", required=True)
    source.add_argument("--publisher", required=True)
    source.add_argument("--primary", action="store_true")
    source.add_argument("--verified", action="store_true")
    source.add_argument("--notes", default="")

    # --- draft-pack ---
    draft = subparsers.add_parser("draft-pack", help="Generate a source-grounded draft")
    draft.add_argument("video_id")
    draft.add_argument("stage", choices=["research", "script", "storyboard"])
    draft.add_argument("--provider", choices=["template", "gemini", "openai", "ollama"], default=None,
                        help="AI provider (default: from config)")

    # --- dashboard ---
    subparsers.add_parser("dashboard", help="Show a rich overview of all videos")

    # --- Unfoldables shorts pipeline ---
    prompts = subparsers.add_parser(
        "shorts-prompts", help="Generate transformation prompts and send them to Telegram"
    )
    # Nine concepts for three videos a day: the same three-to-one choice the
    # single-video schedule had, so there is still something to reject.
    prompts.add_argument("--count", type=int, default=9, help="How many concepts to send")

    subparsers.add_parser(
        "shorts-inbox", help="Publish any video waiting in the Telegram chat"
    )

    subparsers.add_parser(
        "telegram-whoami", help="Print the chat ID of everyone who has messaged the bot"
    )

    report = subparsers.add_parser(
        "shorts-report", help="Send a YouTube and Instagram performance report to Telegram"
    )
    report.add_argument("--limit", type=int, default=10, help="How many recent videos to cover")

    return parser


def _shorts_provider():
    """Build the AI provider used by the shorts pipeline."""
    from .providers.gemini_provider import GeminiProvider
    return GeminiProvider()


def main() -> None:
    _ensure_env()

    from .config_loader import load_config
    from .log import setup_logging
    from .production_ai import build_prompt, generate_draft, save_draft, verified_sources
    from .project import initialize_video, load_manifest, status_summary, transition_video
    from .scoring import calculate, parse_score_arguments
    from .youtube_research import save_snapshot, search_public_videos

    args = build_parser().parse_args()
    root = project_root()

    # Setup logging and load config
    setup_logging(root)
    config = load_config(root)

    if args.command == "status":
        rows = status_summary(root)
        if not rows:
            print("No videos have been initialized.")
            return
        for row in rows:
            print(f"  {row['video_id']}: {row['status']} — {row['topic']}")

    elif args.command == "dashboard":
        rows = status_summary(root)
        try:
            from rich.console import Console
            from rich.table import Table
            
            console = Console()
            table = Table(title="Channel Content OS — Dashboard")
            table.add_column("Video ID", style="cyan", no_wrap=True)
            table.add_column("Topic", style="magenta")
            table.add_column("Status", style="green")
            
            for row in rows:
                table.add_row(row["video_id"], row["topic"], row["status"])
                
            console.print(table)
        except ImportError:
            # Fallback if rich is not installed
            print("Dashboard (run 'pip install rich' for pretty tables):")
            for row in rows:
                print(f"  {row['video_id']} | {row['status']} | {row['topic']}")

    elif args.command == "init-video":
        path = initialize_video(root, args.video_id, args.topic)
        print(f"Created {path}")

    elif args.command == "transition":
        manifest = transition_video(root, args.video_id, args.target, args.approval_note)
        print(f"{manifest['video_id']} → {manifest['status']}")

    elif args.command == "score-topic":
        video_directory = root / "videos" / args.video_id
        load_manifest(root, args.video_id)
        threshold = config.governance.minimum_topic_score
        result = calculate(parse_score_arguments(args.score), threshold=threshold)
        from .project import TOPIC_DIR
        destination = video_directory / TOPIC_DIR / "topic_score.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        tag = "QUALIFIED ✓" if result["qualified"] else "NOT QUALIFIED ✗"
        print(f"{args.video_id}: {result['total']}/100 (threshold {threshold}) — {tag}")

    elif args.command == "youtube-search":
        if not 1 <= args.max_results <= 50:
            raise ValueError("--max-results must be between 1 and 50.")
        video_directory = root / "videos" / args.video_id
        load_manifest(root, args.video_id)
        snapshot = search_public_videos(args.query, args.region, args.max_results)
        destination = save_snapshot(video_directory, snapshot)
        print(f"Saved {len(snapshot['items'])} results to {destination}")

    elif args.command == "add-source":
        video_directory = root / "videos" / args.video_id
        load_manifest(root, args.video_id)
        from .project import RESEARCH_DIR
        destination = video_directory / RESEARCH_DIR / "source_ledger.csv"
        with destination.open("a", newline="", encoding="utf-8") as file:
            csv.writer(file).writerow([
                args.claim_id, args.claim, args.url, args.publisher,
                datetime.now(UTC).isoformat(timespec="seconds"),
                "yes" if args.primary else "no",
                "yes" if args.verified else "no",
                args.notes,
            ])
        print(f"Added {args.claim_id} to {destination.name}")

    elif args.command == "draft-pack":
        video_directory = root / "videos" / args.video_id
        _, manifest = load_manifest(root, args.video_id)
        sources = verified_sources(video_directory)
        prompt = build_prompt(args.video_id, manifest["topic"], args.stage, sources)
        content, mode = generate_draft(prompt, provider_name=args.provider)
        destination = save_draft(video_directory, args.stage, prompt, content, mode)
        print(f"Saved {args.stage} ({mode}) draft -> {destination.name}")

    elif args.command == "shorts-prompts":
        from .shorts_pipeline import send_daily_prompts
        pairs = send_daily_prompts(_shorts_provider(), count=args.count, root=root)
        print(f"Sent {len(pairs)} prompt pair(s) to Telegram:")
        for index, pair in enumerate(pairs, start=1):
            print(f"  #{index} {pair.concept.creature} ({pair.concept.shape})")

    elif args.command == "shorts-inbox":
        from .shorts_pipeline import process_inbox
        records = process_inbox(_shorts_provider(), root=root)
        if not records:
            print("Nothing to publish.")
            return
        for record in records:
            print(f"Uploaded {record['creature']}: {record['youtube_url']}")

    elif args.command == "shorts-report":
        import re as _re
        from .reporting import send_report
        # Echo the report into the job log as well. The figures are already
        # public on the channels themselves, and having them in the run makes
        # the numbers reviewable without reaching for the phone.
        message = send_report(root, limit=args.limit)
        print(_re.sub(r"<[^>]+>", "", message))

    elif args.command == "telegram-whoami":
        from .telegram_inbox import describe_chats
        chats = describe_chats()
        if not chats:
            print(
                "No messages found. Send your bot a message first (any text will do), "
                "then run this again."
            )
            return
        print("Store this as the TELEGRAM_CHAT_ID secret:\n")
        for chat in chats:
            print(f"  chat_id={chat['chat_id']}  ({chat['type']}, {chat['name']})")


if __name__ == "__main__":
    main()
