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


class TitleTests(unittest.TestCase):
    """Titles are no longer forced into questions: measured against real data,
    label titles and question titles performed the same, and forcing one form
    would have removed the only remaining thing to learn from."""

    def _provider(self, title):
        class StubProvider:
            def generate(self, prompt, system_prompt=""):
                return json.dumps({
                    "title": title,
                    "hook": "Nine hidden gears",
                    "description": "A capsule opens.",
                    "caption": "A capsule opens.",
                    "hashtags": ["#automata"],
                })

        return StubProvider()

    def test_a_label_title_is_left_alone(self):
        metadata = generate_metadata(
            self._provider("Green aluminum oval puck"), _concept()
        )
        self.assertEqual(metadata.title, "Green aluminum oval puck")

    def test_a_question_title_is_left_alone(self):
        metadata = generate_metadata(
            self._provider("What hides inside this bronze puck?"), _concept()
        )
        self.assertEqual(metadata.title, "What hides inside this bronze puck?")


class MessageSplittingTests(unittest.TestCase):
    """Telegram answers HTTP 400 past 4096 characters. The weekly report hit
    that at fifteen videos and silently stopped arriving."""

    def test_a_short_report_stays_one_message(self):
        self.assertEqual(reporting.split_for_telegram("kısa"), ["kısa"])

    def test_a_long_report_is_split(self):
        text = "\n".join(f"satır {i} " + "x" * 80 for i in range(200))
        parts = reporting.split_for_telegram(text)
        self.assertGreater(len(parts), 1)
        for part in parts:
            self.assertLessEqual(len(part), reporting.TELEGRAM_MESSAGE_LIMIT)

    def test_splitting_loses_no_lines(self):
        text = "\n".join(f"satır {i} " + "y" * 80 for i in range(200))
        rejoined = "\n".join(reporting.split_for_telegram(text))
        self.assertEqual(
            [line.strip() for line in text.split("\n") if line.strip()],
            [line.strip() for line in rejoined.split("\n") if line.strip()],
        )

    def test_a_single_line_over_the_limit_is_still_emitted(self):
        parts = reporting.split_for_telegram("z" * 5000)
        self.assertEqual(len(parts), 1)


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


class ShapeVarietyTests(unittest.TestCase):
    """Creatures were kept distinct but nothing watched the objects, and five
    of thirteen videos came out as a "capsule"."""

    def setUp(self):
        self._workspace = TemporaryDirectory()
        self.root = Path(self._workspace.name)
        (self.root / "data").mkdir()
        self.addCleanup(self._workspace.cleanup)

    def test_shapes_are_remembered_alongside_creatures(self):
        from channel_ops import shorts_prompts

        shorts_prompts.remember_creatures(["moth"], self.root, shapes=["oval capsule"])
        self.assertEqual(shorts_prompts.load_used_shapes(self.root), ["oval capsule"])
        self.assertEqual(shorts_prompts.load_used_creatures(self.root), ["moth"])

    def test_a_history_without_shapes_is_not_an_error(self):
        """Histories written before shapes were tracked must still load."""
        from channel_ops import shorts_prompts

        (self.root / shorts_prompts.HISTORY_FILE).write_text(
            json.dumps({"creatures": ["moth"]}), encoding="utf-8"
        )
        self.assertEqual(shorts_prompts.load_used_shapes(self.root), [])
        self.assertEqual(shorts_prompts.load_used_creatures(self.root), ["moth"])

    def test_repeated_shapes_are_not_stored_twice(self):
        from channel_ops import shorts_prompts

        shorts_prompts.remember_creatures(["moth"], self.root, shapes=["oval capsule"])
        shorts_prompts.remember_creatures(["wasp"], self.root, shapes=["Oval Capsule"])
        self.assertEqual(len(shorts_prompts.load_used_shapes(self.root)), 1)

    def test_recent_shapes_reach_the_model(self):
        from channel_ops import shorts_prompts

        seen = {}

        class StubProvider:
            def generate(self, prompt, system_prompt=""):
                seen["instructions"] = system_prompt
                raise RuntimeError("stop after capturing the prompt")

        (self.root / shorts_prompts.HISTORY_FILE).write_text(
            json.dumps({"creatures": ["moth"], "shapes": ["oval capsule"]}), encoding="utf-8"
        )
        with self.assertRaises(RuntimeError):
            shorts_prompts.generate_prompt_pairs(StubProvider(), count=1, root=self.root)
        self.assertIn("oval capsule", seen["instructions"])
        self.assertIn("moth", seen["instructions"])


class DeletedVideoTests(unittest.TestCase):
    """A video removed from the channel by hand still has a record. Without a
    marker the report queries it, gets nothing back, and draws the same bare
    "—" it draws when the API is broken."""

    def test_a_deleted_video_is_named_as_deleted(self):
        report = reporting.VideoReport(
            creature="ladybug", title="T", published_at="",
            notes=["YouTube'dan silindi (Instagram'da duruyor)"],
            instagram={"views": 2423, "reach": 1919, "likes": 7},
        )
        rendered = reporting.format_telegram([report])
        self.assertIn("silindi", rendered)
        # The Instagram figures are real and stay in the totals.
        self.assertIn("2.423", rendered)


class DistributionWarningTests(unittest.TestCase):
    """YouTube declining to distribute a video looks identical to a healthy
    publish in the log. It happened twice and a human caught it both times,
    days later."""

    def _report(self, title, views, hours_old):
        from datetime import UTC, datetime, timedelta

        return reporting.VideoReport(
            creature="x", title=title,
            published_at=(datetime.now(UTC) - timedelta(hours=hours_old)).isoformat(),
            youtube={"views": views, "likes": 0, "comments": 0},
        )

    def _healthy(self, count=5, views=1500, hours_old=48):
        return [self._report(f"v{i}", views, hours_old) for i in range(count)]

    def test_a_starved_video_is_flagged(self):
        reports = [self._report("ölü", 10, 30), *self._healthy()]
        rendered = reporting.format_telegram(reports)
        self.assertIn("YouTube dağıtmadı", rendered)

    def test_a_normal_video_is_not_flagged(self):
        rendered = reporting.format_telegram(self._healthy())
        self.assertNotIn("YouTube dağıtmadı", rendered)

    def test_a_fresh_video_is_never_called_dead(self):
        """A clip published minutes ago has no views yet and that is normal."""
        reports = [self._report("yeni", 0, 1), *self._healthy()]
        rendered = reporting.format_telegram(reports)
        self.assertNotIn("YouTube dağıtmadı", rendered)

    def test_a_young_channel_produces_no_warnings(self):
        """Below the median floor, ordinary variation would trip the check."""
        reports = [self._report("a", 2, 48), self._report("b", 30, 48), self._report("c", 40, 48)]
        self.assertNotIn("YouTube dağıtmadı", reporting.format_telegram(reports))

    def test_the_median_ignores_videos_too_new_to_judge(self):
        reports = [*self._healthy(count=3), self._report("yeni", 0, 2)]
        self.assertEqual(reporting._median_views(reports), 1500)

    def test_an_unparseable_date_does_not_crash_the_report(self):
        broken = reporting.VideoReport(
            creature="x", title="bozuk tarih", published_at="dün",
            youtube={"views": 5, "likes": 0, "comments": 0},
        )
        rendered = reporting.format_telegram([broken, *self._healthy()])
        self.assertIn("bozuk tarih", rendered)


class ReleaseSlotTests(unittest.TestCase):
    """Videos are made in one morning batch and released across the day, so
    the queue has to hand out distinct future slots."""

    def setUp(self):
        from datetime import UTC, datetime
        self.morning = datetime(2026, 8, 10, 7, 0, tzinfo=UTC)

    def test_three_videos_take_three_different_slots(self):
        taken = []
        for _ in range(3):
            taken.append(shorts_pipeline.next_slot(taken, self.morning))
        self.assertEqual(len(set(taken)), 3)
        self.assertEqual([slot.hour for slot in taken], sorted(shorts_pipeline.PUBLISH_SLOTS_UTC))

    def test_slots_are_always_in_the_future(self):
        """Past the day's last slot, the next one belongs to tomorrow.

        The hour is derived from the schedule rather than written in, so
        retuning the slots does not silently turn this into a no-op.
        """
        from datetime import UTC, datetime, timedelta

        last = max(shorts_pipeline.PUBLISH_SLOTS_UTC)
        after_last = datetime(2026, 8, 10, last, 0, tzinfo=UTC) + timedelta(minutes=1)
        slot = shorts_pipeline.next_slot([], after_last)
        self.assertGreater(slot, after_last)
        self.assertEqual(slot.day, 11)
        self.assertEqual(slot.hour, min(shorts_pipeline.PUBLISH_SLOTS_UTC))

    def test_a_fourth_video_rolls_to_the_next_day(self):
        taken = []
        for _ in range(4):
            taken.append(shorts_pipeline.next_slot(taken, self.morning))
        self.assertEqual(taken[3].day, 11)

    def test_an_unreadable_queued_time_is_ignored(self):
        """A corrupt entry must not stop new videos being scheduled."""
        times = shorts_pipeline._queued_times([{"publish_at": "yarın"}, {}])
        self.assertEqual(times, [])


class CaptionOrderTests(unittest.TestCase):
    """The operator labels the batch 1, 2, 3; Telegram may deliver them in any
    order, and the labels are what decide which slot each one gets."""

    def test_numbered_captions_sort_numerically(self):
        captions = ["3", "1", "2"]
        self.assertEqual(sorted(captions, key=shorts_pipeline._caption_order), ["1", "2", "3"])

    def test_ten_sorts_after_two(self):
        captions = ["10", "2"]
        self.assertEqual(sorted(captions, key=shorts_pipeline._caption_order), ["2", "10"])

    def test_unlabelled_videos_go_last(self):
        captions = ["", "2"]
        self.assertEqual(sorted(captions, key=shorts_pipeline._caption_order), ["2", ""])


class QueueFlowTests(unittest.TestCase):
    """End to end: a clip arrives, waits for its slot, then publishes. The
    video itself is never stored — only the Telegram file id — so the clip is
    fetched again at release time."""

    def setUp(self):
        self._workspace = TemporaryDirectory()
        self.root = Path(self._workspace.name)
        (self.root / "data").mkdir()
        self.addCleanup(self._workspace.cleanup)
        self.sent = []
        self.uploaded = []

        import channel_ops.telegram_inbox as ti
        from channel_ops import shorts_metadata, youtube_uploader

        def fake_download(file_id, file_size, destination):
            destination.write_bytes(b"video")
            return destination

        def fake_upload(path, title, description, **kw):
            self.uploaded.append(title)
            return {"id": "vid123", "status": {"privacyStatus": kw.get("privacy")}}

        def fake_metadata(provider, concept, *, recent_hooks=None):
            return shorts_metadata.fallback_metadata(concept)

        patches = [
            (shorts_pipeline.notifications, "send_message", self.sent.append),
            (shorts_pipeline.telegram_inbox, "download_file", fake_download),
            (shorts_pipeline.youtube_uploader, "upload_video", fake_upload),
            (shorts_pipeline, "generate_metadata", fake_metadata),
            (shorts_pipeline, "_burn_hook", lambda clip, hook: clip),
            (shorts_pipeline, "_publish_to_instagram", lambda p, c: ("", "")),
        ]
        for target, name, replacement in patches:
            original = getattr(target, name)
            setattr(target, name, replacement)
            self.addCleanup(setattr, target, name, original)

    def _video(self, caption):
        from channel_ops.telegram_inbox import IncomingVideo
        return IncomingVideo(
            update_id=1, file_id="F1", file_size=1_800_000, caption=caption, sent_by="me"
        )

    def _queue(self):
        path = self.root / shorts_pipeline.QUEUE_FILE
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []

    def test_a_clip_is_queued_not_published(self):
        pending = [_pending(1, "moth")]
        shorts_pipeline.enqueue(self._video("1"), pending, self.root)
        self.assertEqual(len(self._queue()), 1)
        self.assertEqual(self.uploaded, [])
        self.assertTrue(pending[0]["used_at"], "concept must be retired on queueing")

    def test_the_queued_item_stores_the_file_id_not_the_video(self):
        shorts_pipeline.enqueue(self._video("1"), [_pending(1, "moth")], self.root)
        item = self._queue()[0]
        self.assertEqual(item["file_id"], "F1")
        self.assertNotIn("video", item)

    def test_nothing_publishes_before_the_slot(self):
        shorts_pipeline.enqueue(self._video("1"), [_pending(1, "moth")], self.root)
        published = shorts_pipeline.publish_due(object(), self.root)
        self.assertEqual(published, [])
        self.assertEqual(len(self._queue()), 1)

    def test_the_slot_arriving_publishes_and_clears_the_queue(self):
        from datetime import UTC, datetime, timedelta

        shorts_pipeline.enqueue(self._video("1"), [_pending(1, "moth")], self.root)
        queue = self._queue()
        queue[0]["publish_at"] = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        (self.root / shorts_pipeline.QUEUE_FILE).write_text(json.dumps(queue), encoding="utf-8")

        published = shorts_pipeline.publish_due(object(), self.root)
        self.assertEqual(len(published), 1)
        self.assertEqual(len(self.uploaded), 1)
        self.assertEqual(self._queue(), [])

    def test_three_clips_get_three_different_slots(self):
        pending = [_pending(1, "moth"), _pending(2, "scorpion"), _pending(3, "nautilus")]
        for caption in ("1", "2", "3"):
            shorts_pipeline.enqueue(self._video(caption), pending, self.root)
        slots = [item["publish_at"] for item in self._queue()]
        self.assertEqual(len(set(slots)), 3)
        creatures = [item["concept"]["creature"] for item in self._queue()]
        self.assertEqual(creatures, ["moth", "scorpion", "nautilus"])

    def test_the_operator_is_told_when_it_will_go_out(self):
        shorts_pipeline.enqueue(self._video("1"), [_pending(1, "moth")], self.root)
        self.assertIn("Sıraya alındı", self.sent[0])
        self.assertIn("Yayın saati", self.sent[0])
