"""A git merge driver for the two state files two jobs write at once.

The idea job writes ``data/shorts_pending.json`` (new ideas, used ones pruned)
and ``data/shorts_history.json`` (creatures and shapes already used). The
Telegram watch loop writes the same pending file every time a clip arrives
(it stamps ``used_at`` on the idea the clip belongs to) and pushes every two
minutes. On 21 October the two pushed within a minute of each other: the
watch loop's ``used_at`` line and the idea job's new entries landed next to
each other at the end of the list, git saw a text conflict, the idea job's
rebase failed, and two ideas that had already gone out on Telegram were never
saved -- clips made from them would have matched the wrong idea.

The files are lists and dictionaries, not text, so they are merged as such.
Git hands a merge driver three versions -- the common ancestor (%O), ours
(%A, where the result must be written) and theirs (%B) -- and a non-zero exit
means "conflict, fall back to the usual failure". Anything unexpected exits
non-zero rather than guessing, so the worst case is the old behaviour.

Registered in .gitattributes and enabled per checkout with:

    git config merge.shorts-state.driver \\
        "python3 src/channel_ops/state_merge.py %O %A %B %P"

Standard library only, run as a plain script: the jobs that need it do not all
have the package on their path.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def _key(entry: dict) -> tuple[str, int]:
    """An idea is its batch plus its number in that batch, which is also how
    the operator refers to it."""
    return (str(entry["batch"]), int(entry["index"]))


def merge_pending(base: list, ours: list, theirs: list) -> list:
    """Three-way merge of the pending-ideas list.

    * an idea either side added is kept;
    * an idea either side removed (the idea job prunes used and expired ones)
      stays removed;
    * an idea both kept gets every field either side changed -- in practice
      the ``used_at`` stamp the watch loop adds.
    """
    base_by = {_key(e): e for e in base}
    ours_by = {_key(e): e for e in ours}
    theirs_by = {_key(e): e for e in theirs}

    removed = {k for k in base_by if k not in ours_by or k not in theirs_by}

    order: list[tuple[str, int]] = []
    for key in [_key(e) for e in theirs] + [_key(e) for e in ours]:
        if key not in order and key not in removed:
            order.append(key)

    merged = []
    for key in order:
        old = base_by.get(key, {})
        entry = dict(old)
        for side in (theirs_by.get(key), ours_by.get(key)):
            if side is None:
                continue
            for field, value in side.items():
                if field not in old or old[field] != value:
                    entry[field] = value
        merged.append(entry)
    return merged


def _union(first: list, second: list) -> list:
    """Every name in either list, newest last, no repeats (case-insensitive)."""
    out: list = []
    seen: set[str] = set()
    for value in first + second:
        lowered = str(value).lower()
        if lowered not in seen:
            seen.add(lowered)
            out.append(value)
    return out


# shorts_prompts.RECENT_MEMORY. Repeated, not imported: this file runs as a
# plain script without the package on its path. A test keeps the two equal.
HISTORY_LIMIT = 300


def merge_history(base: dict, ours: dict, theirs: dict) -> dict:
    """Both lists only ever grow at the end and are trimmed at the front to
    the same window, so the union of the two, cut back to that window, is
    what either side would have written had it seen the other."""
    merged = {}
    for name in sorted(set(ours) | set(theirs)):
        combined = _union(list(theirs.get(name, [])), list(ours.get(name, [])))
        merged[name] = combined[-HISTORY_LIMIT:]
    return merged


MERGERS = {
    "shorts_pending.json": (merge_pending, list),
    "shorts_history.json": (merge_history, dict),
}


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        return 2
    base_path, ours_path, theirs_path, name = argv
    merger = MERGERS.get(Path(name).name)
    if merger is None:
        return 1
    merge, kind = merger
    try:
        def load(path: str):
            text = Path(path).read_text(encoding="utf-8").strip()
            data = json.loads(text) if text else kind()
            if not isinstance(data, kind):
                raise ValueError(f"{path}: expected {kind.__name__}")
            return data

        result = merge(load(base_path), load(ours_path), load(theirs_path))
    except (OSError, ValueError, KeyError, TypeError):
        return 1
    Path(ours_path).write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
