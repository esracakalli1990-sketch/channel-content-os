"""The idea job and the Telegram watch loop write the same pending-ideas file.

On 21 October they pushed within a minute of each other, git saw a text
conflict, and two ideas already sent on Telegram were never saved. These
tests replay that with a real repository, not only the merge function.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from channel_ops import state_merge

ROOT = Path(__file__).resolve().parent.parent


def _idea(batch, index, creature, **extra):
    return {"index": index, "batch": batch, "created_at": batch,
            "concept": {"creature": creature}, **extra}


OLD = "2026-10-15T23:09:53+00:00"
NIGHT = "2026-10-20T23:18:48+00:00"
MORNING = "2026-10-21T03:59:00+00:00"


class MergePendingTests(unittest.TestCase):
    def test_the_21_october_case(self):
        """Watch loop stamps the cricket as used; the idea job, from the same
        starting point, prunes nothing yet and appends two new ideas."""
        base = [_idea(OLD, 2, "siphonophore"), _idea(NIGHT, 1, "field cricket")]
        loop = [_idea(OLD, 2, "siphonophore"),
                _idea(NIGHT, 1, "field cricket", used_at="2026-10-21T03:59:42+00:00")]
        ideas = base + [_idea(MORNING, 1, "pipistrelle"), _idea(MORNING, 2, "snail")]
        merged = state_merge.merge_pending(base, ideas, loop)
        names = [e["concept"]["creature"] for e in merged]
        self.assertEqual(names, ["siphonophore", "field cricket", "pipistrelle", "snail"])
        cricket = merged[1]
        self.assertEqual(cricket["used_at"], "2026-10-21T03:59:42+00:00")

    def test_an_idea_pruned_by_one_side_stays_gone(self):
        """The idea job drops used ideas when it writes; the loop marking the
        same idea used must not bring it back."""
        base = [_idea(NIGHT, 1, "cricket")]
        ideas = [_idea(MORNING, 1, "snail")]             # pruned the cricket
        loop = [_idea(NIGHT, 1, "cricket", used_at="x")]  # marked it used
        merged = state_merge.merge_pending(base, ideas, loop)
        self.assertEqual([e["concept"]["creature"] for e in merged], ["snail"])

    def test_it_is_symmetric_about_which_side_is_ours(self):
        base = [_idea(NIGHT, 1, "cricket")]
        a = [_idea(NIGHT, 1, "cricket", used_at="t"), ]
        b = base + [_idea(MORNING, 1, "snail")]
        left = state_merge.merge_pending(base, a, b)
        right = state_merge.merge_pending(base, b, a)
        key = lambda es: sorted(json.dumps(e, sort_keys=True) for e in es)
        self.assertEqual(key(left), key(right))

    def test_nothing_new_on_either_side_changes_nothing(self):
        base = [_idea(NIGHT, 1, "cricket")]
        self.assertEqual(state_merge.merge_pending(base, base, base), base)


class MergeHistoryTests(unittest.TestCase):
    def test_both_sides_additions_survive(self):
        base = {"creatures": ["a", "b"], "shapes": ["x"]}
        ours = {"creatures": ["a", "b", "c"], "shapes": ["x", "y"]}
        theirs = {"creatures": ["a", "b", "d"], "shapes": ["x"]}
        merged = state_merge.merge_history(base, ours, theirs)
        self.assertEqual(set(merged["creatures"]), {"a", "b", "c", "d"})
        self.assertEqual(merged["shapes"], ["x", "y"])


    def test_the_window_matches_the_idea_memory(self):
        from channel_ops import shorts_prompts
        self.assertEqual(state_merge.HISTORY_LIMIT, shorts_prompts.RECENT_MEMORY)


class DriverEntryTests(unittest.TestCase):
    def test_an_unknown_file_is_left_to_git(self):
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / "f"
            p.write_text("[]")
            self.assertEqual(state_merge.main([str(p), str(p), str(p), "other.json"]), 1)

    def test_unreadable_json_is_a_conflict_not_a_guess(self):
        with TemporaryDirectory() as tmp:
            good, bad = Path(tmp) / "g", Path(tmp) / "b"
            good.write_text("[]")
            bad.write_text("{not json")
            self.assertEqual(
                state_merge.main([str(good), str(good), str(bad), "data/shorts_pending.json"]), 1)


@unittest.skipUnless(shutil.which("git"), "git yok")
class RealRebaseTests(unittest.TestCase):
    """The same race, through git itself, with the driver as configured in
    .gitattributes and the workflows."""

    def _git(self, cwd, *args):
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)

    def _write(self, repo, entries):
        path = Path(repo) / "data" / "shorts_pending.json"
        path.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def test_the_idea_job_rebases_cleanly_over_the_watch_loop(self):
        with TemporaryDirectory() as tmp:
            origin = Path(tmp) / "origin.git"
            self._git(tmp, "init", "-q", "--bare", "-b", "main", str(origin))
            seed = Path(tmp) / "seed"
            self._git(tmp, "clone", "-q", str(origin), str(seed))
            for repo_cfg in (("user.name", "t"), ("user.email", "t@t")):
                self._git(seed, "config", *repo_cfg)
            (seed / "data").mkdir()
            (seed / "src" / "channel_ops").mkdir(parents=True)
            shutil.copy(ROOT / "src" / "channel_ops" / "state_merge.py",
                        seed / "src" / "channel_ops" / "state_merge.py")
            shutil.copy(ROOT / ".gitattributes", seed / ".gitattributes")
            base = [_idea(OLD, 2, "siphonophore"), _idea(NIGHT, 1, "field cricket")]
            self._write(seed, base)
            self._git(seed, "add", "-A")
            self._git(seed, "commit", "-q", "-m", "base")
            self._git(seed, "push", "-q", "origin", "HEAD:main")

            clones = {}
            for name in ("loop", "ideas"):
                path = Path(tmp) / name
                self._git(tmp, "clone", "-q", str(origin), str(path))
                self._git(path, "config", "user.name", "t")
                self._git(path, "config", "user.email", "t@t")
                self._git(path, "config", "merge.shorts-state.driver",
                          "python3 src/channel_ops/state_merge.py %O %A %B %P")
                clones[name] = path

            # The watch loop marks the cricket used and pushes first.
            self._write(clones["loop"], [
                _idea(OLD, 2, "siphonophore"),
                _idea(NIGHT, 1, "field cricket", used_at="2026-10-21T03:59:42+00:00")])
            self._git(clones["loop"], "commit", "-q", "-am", "loop")
            self.assertEqual(self._git(clones["loop"], "push", "-q", "origin", "HEAD:main").returncode, 0)

            # The idea job, from the old starting point, appends two ideas.
            self._write(clones["ideas"], base + [
                _idea(MORNING, 1, "pipistrelle"), _idea(MORNING, 2, "snail")])
            self._git(clones["ideas"], "commit", "-q", "-am", "ideas")
            rebase = self._git(clones["ideas"], "pull", "--rebase", "-q", "origin", "main")
            self.assertEqual(rebase.returncode, 0, rebase.stderr)

            merged = json.loads((clones["ideas"] / "data" / "shorts_pending.json").read_text())
            names = [e["concept"]["creature"] for e in merged]
            self.assertEqual(names, ["siphonophore", "field cricket", "pipistrelle", "snail"])
            self.assertIn("used_at", merged[1])


if __name__ == "__main__":
    unittest.main()
