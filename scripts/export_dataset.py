"""Dump everything the APIs will give, as one file the brief can read.

Collection has to run where the credentials are (the Actions runner); the
document is typeset elsewhere. Splitting them through a committed file keeps
the two halves honest: the brief cannot quietly use a different number from
the one that was collected, because there is only one file and it carries the
timestamp it was collected at.

Writes data/expert_dataset.json.

Usage: export_dataset.py
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from channel_ops import channel_analysis as ca  # noqa: E402
from channel_ops.channel_data import collect  # noqa: E402
from channel_ops.config_loader import find_project_root  # noqa: E402

# Analytics tables worth carrying into an appendix verbatim. Named rather
# than "everything in the dump", so a new query added upstream does not
# silently change what an outside reviewer is handed.
TABLES = ("traffic", "countries", "devices", "subs_status", "demographics",
          "sharing", "search_terms", "external_sites", "daily")


def main() -> int:
    root = find_project_root()
    dump = collect(root)
    rows = ca.videos(dump)
    history = ca.load_history(root)
    gates = ca.thresholds(dump, history)

    published = {r.get("youtube_video_id"): r for r in dump.get("published") or []}
    for row in rows:
        record = published.get(row["id"], {})
        # The exact phrases, alongside the grouped "metal". An outside eye may
        # well see structure in the raw wording that the grouping hides.
        row["material_raw"] = record.get("material", "")
        row["shape_raw"] = record.get("shape", "")
        row["title"] = record.get("title", "")

    analytics = dump.get("analytics") or {}
    dataset = {
        "collected_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "totals": dump.get("totals", {}),
        "gates": gates,
        "videos": rows,
        "history": history,
        "tables": {name: analytics.get(name) for name in TABLES
                   if analytics.get(name)},
        # Carried so the reviewer sees which numbers were unavailable on the
        # day rather than assuming the set is complete.
        "errors": dump.get("errors", {}),
    }

    path = root / "data" / "expert_dataset.json"
    path.write_text(json.dumps(dataset, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8")

    mature = [r for r in rows if r["mature"]]
    print(f"{path}")
    removed = [r["id"] for r in rows if r.get("deleted")]
    hooked = sum(1 for r in rows if r.get("engaged_ratio") is not None)
    print(f"  video      : {len(rows)} ({len(mature)} olgun, silinmiş: {len(removed)})")
    print(f"  kanca%     : {hooked} videoda hesaplandı")
    print(f"  tablo      : {', '.join(sorted(dataset['tables']))}")
    print(f"  ölçüm      : {len(history)} anlık görüntü")
    if dataset["errors"]:
        print(f"  ⚠ eksik    : {', '.join(dataset['errors'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
