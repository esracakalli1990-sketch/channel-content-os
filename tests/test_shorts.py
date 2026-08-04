"""Tests for the Unfoldables shorts loop.

These cover the parts that fail silently in production: a concept being
published twice, a hook repeating across videos, and the report printing a
loop rate as if it were a percentage.
"""
import json
import unittest
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from channel_ops import reporting, shorts_pipeline
from channel_ops.shorts_metadata import (
    _default_hook,
    fallback_metadata,
    generate_metadata,
)
from channel_ops.shorts_prompts import Concept


def _concept(creature="pangolin", shape="a brushed steel capsule"):
    return Concept(
        shape=shape,
        material="brushed steel",
        internal_detail="brass gears",
        button="a recessed brass button",
        button_short="brass button",
        creature=creature,
        shell_mechanic="the shell splits along a seam",
        emerging_parts="jointed legs",
    )


def _pending(index, creature, **extra):
    return {
        "index": index,
        "created_at": "2026-08-04T06:00:00+00:00",
        "concept": _concept(creature).__dict__ | {},
        "text_to_image": "",
        "image_to_video": "",
        **extra,
    }


class MatchPendingTests(unittest.TestCase):
    def test_uncaptioned_video_takes_the_most_recent_concept(self):
        pending = [_pending(1, "moth"), _pending(2, "scorpion")]
        self.assertEqual(shorts_pipeline.match_pending("", pending)["index"], 2)

    def test_used_concepts_are_not_offered_again(self):
        """Three uncaptioned videos in a day must not share one concept."""
        pending = [_pending(1, "moth"), _pending(2, "scorpion"), _pending(3, "nautilus")]
        chosen = []
        for _ in range(3):
            entry = shorts_pipeline.match_pending("", pending)
            chosen.append(entry["index"])
            entry["used_at"] = "2026-08-04T12:00:00+00:00"
        self.assertEqual(sorted(chosen), [1, 2, 3])

    def test_returns_none_once_everything_is_used(self):
        pending = [_pending(1, "moth", used_at="2026-08-04T12:00:00+00:00")]
        self.assertIsNone(shorts_pipeline.match_pending("", pending))

    def test_an_explicit_number_still_wins(self):
        pending = [_pending(1, "moth"), _pending(2, "scorpion")]
        self.assertEqual(shorts_pipeline.match_pending("1 numara", pending)["index"], 1)

    def test_a_named_creature_still_wins(self):
        pending = [_pending(1, "moth"), _pending(2, "scorpion")]
        self.assertEqual(shorts_pipeline.match_pending("the moth one", pending)["index"], 1)


class RecentHookTests(unittest.TestCase):
    def test_reads_the_hooks_of_recent_videos(self):
        with TemporaryDirectory() as workspace:
            root = Path(workspace)
            (root / "data").mkdir()
            (root / shorts_pipeline.PUBLISHED_FILE).write_text(
                json.dumps([{"hook": "Inside this disc?"}, {"hook": ""}, {"hook": "Nine hidden gears"}]),
                encoding="utf-8",
            )
            self.assertEqual(
                shorts_pipeline._recent_hooks(root),
                ["Inside this disc?", "Nine hidden gears"],
            )

    def test_no_history_is_not_an_error(self):
        with TemporaryDirectory() as workspace:
            self.assertEqual(shorts_pipeline._recent_hooks(Path(workspace)), [])


class PendingAccumulationTests(unittest.TestCase):
    """The prompt job runs three times a day, so each run must add to the
    pending list rather than replace it."""

    def setUp(self):
        self._workspace = TemporaryDirectory()
        self.root = Path(self._workspace.name)
        (self.root / "data").mkdir()
        self.addCleanup(self._workspace.cleanup)

    def _send(self, creature):
        class StubProvider:
            def generate(self, prompt, system_prompt=""):
                raise AssertionError("the concept source is stubbed instead")

        def fake_pairs(provider, *, count, root=None):
            from channel_ops.shorts_prompts import PromptPair
            return [PromptPair(_concept(creature), "t2i", "i2v") for _ in range(count)]

        original = shorts_pipeline.generate_prompt_pairs
        sent = []
        shorts_pipeline.generate_prompt_pairs = fake_pairs
        original_send = shorts_pipeline.notifications.send_message
        shorts_pipeline.notifications.send_message = sent.append
        try:
            shorts_pipeline.send_daily_prompts(StubProvider(), count=1, root=self.root)
        finally:
            shorts_pipeline.generate_prompt_pairs = original
            shorts_pipeline.notifications.send_message = original_send
        return sent

    def _pending(self):
        return json.loads((self.root / shorts_pipeline.PENDING_FILE).read_text(encoding="utf-8"))

    def test_a_later_run_keeps_the_earlier_concept(self):
        self._send("moth")
        self._send("scorpion")
        creatures = [entry["concept"]["creature"] for entry in self._pending()]
        self.assertEqual(creatures, ["moth", "scorpion"])

    def test_indices_keep_counting_up(self):
        self._send("moth")
        self._send("scorpion")
        self.assertEqual([entry["index"] for entry in self._pending()], [1, 2])

    def test_used_concepts_are_dropped_on_the_next_run(self):
        self._send("moth")
        pending = self._pending()
        pending[0]["used_at"] = "2026-08-04T12:00:00+00:00"
        (self.root / shorts_pipeline.PENDING_FILE).write_text(json.dumps(pending), encoding="utf-8")
        self._send("scorpion")
        creatures = [entry["concept"]["creature"] for entry in self._pending()]
        self.assertEqual(creatures, ["scorpion"])

    def test_a_single_idea_is_not_announced_as_a_choice(self):
        messages = self._send("moth")
        self.assertNotIn("Beğendiğini", messages[0])


class HookTests(unittest.TestCase):
    def test_default_hook_names_the_object(self):
        self.assertEqual(_default_hook(_concept(shape="a sleek brushed steel capsule")), "Inside this capsule?")

    def test_default_hook_differs_between_objects(self):
        first = _default_hook(_concept(shape="a crimson enamel disc"))
        second = _default_hook(_concept(shape="a titanium teardrop"))
        self.assertNotEqual(first, second)

    def test_fallback_metadata_withholds_the_creature(self):
        metadata = fallback_metadata(_concept(creature="pangolin", shape="a bronze egg"))
        self.assertNotIn("pangolin", metadata.title.lower())

    def test_a_repeated_hook_is_replaced(self):
        """The model wrote the same hook three videos running; instructing it
        not to was not enough, so the result is checked."""

        @dataclass
        class StubProvider:
            def generate(self, prompt, system_prompt=""):
                return json.dumps({
                    "title": "What is inside this disc?",
                    "hook": "Watch it unfold",
                    "description": "A disc opens.",
                    "caption": "A disc opens.",
                    "hashtags": ["#automata"],
                })

        metadata = generate_metadata(
            StubProvider(),
            _concept(shape="a crimson enamel disc"),
            recent_hooks=["watch it unfold"],  # differing case must still match
        )
        self.assertEqual(metadata.hook, "Inside this disc?")

    def test_a_fresh_hook_is_kept(self):
        @dataclass
        class StubProvider:
            def generate(self, prompt, system_prompt=""):
                return json.dumps({
                    "title": "What is inside this disc?",
                    "hook": "Nine hidden gears",
                    "description": "A disc opens.",
                    "caption": "A disc opens.",
                    "hashtags": ["#automata"],
                })

        metadata = generate_metadata(
            StubProvider(), _concept(), recent_hooks=["Watch it unfold"]
        )
        self.assertEqual(metadata.hook, "Nine hidden gears")


class ReportFormattingTests(unittest.TestCase):
    def test_a_loop_rate_is_not_shown_as_a_percentage(self):
        """averageViewPercentage runs past 100% on Shorts because replays
        count; the real data reached 261%."""
        line = reporting._retention_line({"averageViewPercentage": 261.0})
        self.assertIn("2,61×", line)
        self.assertNotIn("%261", line)

    def test_a_partial_watch_stays_a_percentage(self):
        self.assertIn("%78", reporting._retention_line({"averageViewPercentage": 78.0}))

    def test_missing_analytics_produces_no_line(self):
        self.assertEqual(reporting._retention_line({}), "")

    def test_traffic_line_reports_shares_of_the_total(self):
        line = reporting._traffic_line({"SHORTS": 970, "YT_SEARCH": 30})
        self.assertIn("Shorts akışı %97", line)

    def test_traffic_line_drops_rounding_noise(self):
        line = reporting._traffic_line({"SHORTS": 995, "PLAYLIST": 5})
        self.assertNotIn("oynatma listesi", line)

    def test_no_traffic_produces_no_line(self):
        self.assertEqual(reporting._traffic_line({}), "")


if __name__ == "__main__":
    unittest.main()
