"""Set the language and the AI disclosure on videos already published.

Prints what it would change and changes nothing unless --apply is given,
because videos.update rewrites whole parts and this touches every video on
the channel.

Usage:
  backfill_video_metadata.py                 # dry run, all videos
  backfill_video_metadata.py --limit 1       # dry run, one video
  backfill_video_metadata.py --limit 1 --apply
  backfill_video_metadata.py --apply         # the real thing
  backfill_video_metadata.py --no-ai --apply # language only
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from channel_ops import video_metadata  # noqa: E402
from channel_ops.channel_data import load_records  # noqa: E402
from channel_ops.config_loader import find_project_root  # noqa: E402
from channel_ops.youtube_uploader import DEFAULT_LANGUAGE  # noqa: E402


def _video_ids(root: Path) -> list[str]:
    published, long_form = load_records(root)
    seen, ordered = set(), []
    for record in published + long_form:
        video_id = record.get("youtube_video_id")
        if video_id and video_id not in seen:
            seen.add(video_id)
            ordered.append(video_id)
    return ordered


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="actually send the updates")
    parser.add_argument("--limit", type=int, default=0,
                        help="only read the first N videos (try 1 first)")
    parser.add_argument("--max", type=int, default=0, dest="max_writes",
                        help="write at most N videos this run; see QUOTA below")
    parser.add_argument("--language", default=DEFAULT_LANGUAGE)
    parser.add_argument("--no-ai", action="store_true",
                        help="skip the synthetic-content declaration")
    args = parser.parse_args()
    declare_ai = not args.no_ai

    root = find_project_root()
    video_ids = _video_ids(root)
    if args.limit:
        video_ids = video_ids[:args.limit]
    print(f"{len(video_ids)} video kaydı okunuyor…\n")

    declared = video_metadata.load_declared(root)
    current = video_metadata.fetch(video_ids)
    missing_from_api = [v for v in video_ids if v not in current]
    if missing_from_api:
        print(f"⚠️  {len(missing_from_api)} video API'den dönmedi "
              f"(silinmiş olabilir): {', '.join(missing_from_api[:5])}\n")

    todo = []
    for video_id in video_ids:
        if video_id not in current:
            continue
        # A video already in the local record is not asked about the
        # disclosure again: YouTube never reports it back, so the record is
        # the only thing that knows.
        wants_ai = declare_ai and video_id not in declared
        gaps = video_metadata.needs_work(
            current[video_id], language=args.language, declare_ai=wants_ai
        )
        if gaps:
            todo.append((video_id, gaps, wants_ai))

    already = len(current) - len(todo)
    print(f"Zaten doğru : {already}")
    print(f"Düzeltilecek: {len(todo)}")

    if not todo:
        print("\nYapacak bir şey yok.")
        return 0

    # The quota is the reason this runs in batches rather than in one go. A
    # videos.update costs 50 units against a 10,000/day budget that the
    # pipeline is already spending on uploads at 1,600 each. Repairing all of
    # them in an afternoon would leave nothing for the evening's publishing,
    # which is the kind of unrelated breakage this project keeps producing.
    remaining = todo
    if args.max_writes and len(todo) > args.max_writes:
        remaining = todo[:args.max_writes]
        print(f"Bu turda   : {len(remaining)}  (kota: ~{len(remaining) * 50} birim)")
        print(f"Kalan      : {len(todo) - len(remaining)}  (sonraki turlarda)")
    else:
        print(f"Kota       : ~{len(remaining) * 50} birim")
    print()
    todo = remaining

    for video_id, gaps, _ in todo[:10]:
        title = current[video_id]["snippet"].get("title", "")[:48]
        print(f"  {video_id}  {', '.join(gaps):<58}  {title}")
    if len(todo) > 10:
        print(f"  … ve {len(todo) - 10} tane daha")

    if not args.apply:
        print("\nDeneme çalışması. Göndermek için --apply ekle.")
        return 0

    print()
    failures = []
    for index, (video_id, _, wants_ai) in enumerate(todo, start=1):
        body = video_metadata.planned_update(
            video_id, current[video_id], language=args.language, declare_ai=wants_ai
        )
        try:
            returned = video_metadata.apply(body)
        except RuntimeError as exc:
            # One rejected video must not cost the other 147. A locked AI label
            # and a revoked scope both land here and both are worth seeing in
            # full rather than as a count.
            failures.append((video_id, str(exc)))
            print(f"  ✗ {index}/{len(todo)}  {video_id}")
        else:
            # videos.update returns the stored resource, so the one question
            # that matters -- did this request erase anything? -- can be
            # answered from the reply instead of from Studio afterwards.
            lost = video_metadata.verify(body, returned)
            if lost:
                failures.append((video_id, f"ALANLAR DEĞİŞTİ: {', '.join(lost)}"))
                print(f"  ⚠ {index}/{len(todo)}  {video_id}  {', '.join(lost)}")
                print("     Devam edilmiyor; kalanlara dokunulmadı.")
                break
            if wants_ai:
                declared.add(video_id)
            print(f"  ✓ {index}/{len(todo)}  {video_id}")

    # Written even on a partial run, and even after a break, so the next
    # run starts where this one stopped rather than from the top.
    video_metadata.save_declared(root, declared)

    print(f"\nGüncellendi: {len(todo) - len(failures)} / {len(todo)}")
    if failures:
        print(f"Başarısız  : {len(failures)}")
        for video_id, reason in failures[:5]:
            print(f"  {video_id}: {reason}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
