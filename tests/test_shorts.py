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

from channel_ops import reporting, shorts_pipeline, shorts_prompts
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
    # Relative to now, never a fixed date: these entries were stamped
    # 2026-08-04 and quietly aged past the fourteen-day freshness window,
    # so the tests started failing on their own two weeks after being written.
    from datetime import UTC, datetime, timedelta

    return {
        "index": index,
        "created_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
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
    """A batch that goes unused must survive the next night's batch, so each
    run adds to the pending list rather than replacing it."""

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

    def test_each_batch_is_numbered_from_one(self):
        """The operator labels clips by position in tonight's batch, so a
        running counter would have made tonight's first idea #4."""
        self._send("moth")
        self._send("scorpion")
        pending = self._pending()
        self.assertEqual([entry["index"] for entry in pending], [1, 1])
        self.assertNotEqual(pending[0]["batch"], pending[1]["batch"])

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

    def test_a_days_videos_take_a_different_slot_each(self):
        """The count comes from the schedule: it was three a day and is two,
        and a test that writes the number down goes quietly meaningless the
        next time it changes."""
        slots = sorted(shorts_pipeline.PUBLISH_SLOTS_UTC)
        taken = []
        for _ in slots:
            taken.append(shorts_pipeline.next_slot(taken, self.morning))
        self.assertEqual(len(set(taken)), len(slots))
        self.assertEqual([slot.hour for slot in taken], slots)

    def test_one_more_than_the_days_slots_rolls_to_tomorrow(self):
        slots = sorted(shorts_pipeline.PUBLISH_SLOTS_UTC)
        taken = []
        for _ in range(len(slots) + 1):
            taken.append(shorts_pipeline.next_slot(taken, self.morning))
        self.assertEqual(taken[-1].hour, slots[0])
        self.assertEqual(taken[-1].day, taken[0].day + 1)

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
            (shorts_pipeline, "_burn_hook", lambda clip, hook, badge="": clip),
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


class PublishRetryTests(unittest.TestCase):
    """A refused upload must not cost the video. YouTube answered the great
    hornbill with HTTP 409 on 1 September, no video was created, and the clip
    was dropped from the queue on that single answer."""

    def setUp(self):
        self._workspace = TemporaryDirectory()
        self.root = Path(self._workspace.name)
        (self.root / "data").mkdir()
        self.addCleanup(self._workspace.cleanup)
        self.sent = []
        self.attempts = []
        self.fail_with = RuntimeError("YouTube upload failed (HTTP 409)")

        from channel_ops import shorts_metadata

        def fake_upload(path, title, description, **kw):
            self.attempts.append(title)
            if self.fail_with is not None:
                raise self.fail_with
            return {"id": "vid123", "status": {"privacyStatus": kw.get("privacy")}}

        patches = [
            (shorts_pipeline.notifications, "send_message", self.sent.append),
            (shorts_pipeline.telegram_inbox, "download_file",
             lambda file_id, size, destination: destination.write_bytes(b"video")),
            (shorts_pipeline.youtube_uploader, "upload_video", fake_upload),
            (shorts_pipeline, "generate_metadata",
             lambda provider, concept, *, recent_hooks=None:
                 shorts_metadata.fallback_metadata(concept)),
            (shorts_pipeline, "_burn_hook", lambda clip, hook, badge="": clip),
            (shorts_pipeline, "_publish_to_instagram", lambda p, c: ("", "")),
        ]
        for target, name, replacement in patches:
            original = getattr(target, name)
            setattr(target, name, replacement)
            self.addCleanup(setattr, target, name, original)

    def _queue(self, items):
        path = self.root / shorts_pipeline.QUEUE_FILE
        path.write_text(json.dumps(items), encoding="utf-8")

    def _read_queue(self):
        path = self.root / shorts_pipeline.QUEUE_FILE
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []

    def _due_item(self, **extra):
        from datetime import UTC, datetime, timedelta

        item = {
            "queued_at": "2026-09-01T02:48:00+00:00",
            "publish_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
            "file_id": "F1",
            "file_size": 3161016,
            "concept": _concept("great hornbill").__dict__ | {},
        }
        item.update(extra)
        return item

    def test_a_refused_upload_stays_in_the_queue(self):
        self._queue([self._due_item()])
        shorts_pipeline.publish_due(object(), self.root)
        queue = self._read_queue()
        self.assertEqual(len(queue), 1, "the clip must not be discarded")
        self.assertEqual(queue[0]["file_id"], "F1")
        self.assertEqual(queue[0]["attempts"], 1)

    def test_the_retry_waits_rather_than_firing_on_the_next_poll(self):
        from datetime import UTC, datetime

        self._queue([self._due_item()])
        shorts_pipeline.publish_due(object(), self.root)
        again = datetime.fromisoformat(self._read_queue()[0]["publish_at"])
        self.assertGreater(
            (again - datetime.now(UTC)).total_seconds(),
            shorts_pipeline.RETRY_BACKOFF_MINUTES * 60 - 120,
        )

    def test_the_original_slot_is_remembered(self):
        item = self._due_item()
        self._queue([item])
        shorts_pipeline.publish_due(object(), self.root)
        self.assertEqual(self._read_queue()[0]["first_publish_at"], item["publish_at"])

    def test_the_clip_is_given_up_on_after_the_last_attempt(self):
        self._queue([self._due_item(attempts=shorts_pipeline.MAX_PUBLISH_ATTEMPTS - 1)])
        shorts_pipeline.publish_due(object(), self.root)
        self.assertEqual(self._read_queue(), [])
        self.assertIn("kuyruktan çıkarıldı", self.sent[-1])

    def test_a_retry_that_works_publishes_the_clip(self):
        self._queue([self._due_item()])
        shorts_pipeline.publish_due(object(), self.root)

        self.fail_with = None
        queue = self._read_queue()
        queue[0]["publish_at"] = self._due_item()["publish_at"]
        self._queue(queue)

        published = shorts_pipeline.publish_due(object(), self.root)
        self.assertEqual(len(published), 1)
        self.assertEqual(len(self.attempts), 2)
        self.assertEqual(self._read_queue(), [])

    def test_the_failure_notice_names_the_clip_and_the_attempt(self):
        self._queue([self._due_item()])
        shorts_pipeline.publish_due(object(), self.root)
        notice = self.sent[-1]
        self.assertIn("great hornbill", notice)
        self.assertIn("409", notice)
        self.assertIn(f"1/{shorts_pipeline.MAX_PUBLISH_ATTEMPTS}", notice)

    def test_an_oversized_clip_is_not_retried(self):
        from channel_ops import telegram_inbox

        def too_large(file_id, size, destination):
            raise telegram_inbox.VideoTooLargeError("20 MB sınırı aşıldı")

        original = shorts_pipeline.telegram_inbox.download_file
        shorts_pipeline.telegram_inbox.download_file = too_large
        self.addCleanup(
            setattr, shorts_pipeline.telegram_inbox, "download_file", original
        )

        self._queue([self._due_item()])
        shorts_pipeline.publish_due(object(), self.root)
        self.assertEqual(self._read_queue(), [], "a too-large clip stays too large")


class LeadTimeTests(unittest.TestCase):
    """Prompts arrive around midnight Turkish time and the clips are made at
    once, so without a minimum lead the batch's first video would drop into a
    slot an hour away instead of opening the next day."""

    def test_an_imminent_slot_is_skipped(self):
        from datetime import UTC, datetime, timedelta

        # 22:30 UTC — half an hour before the 23:00 slot.
        night = datetime(2026, 8, 12, 22, 30, tzinfo=UTC)
        slot = shorts_pipeline.next_slot([], night)
        self.assertGreaterEqual(
            slot - night, timedelta(hours=shorts_pipeline.MIN_LEAD_HOURS)
        )

    def test_a_midnight_batch_opens_the_following_afternoon(self):
        from datetime import UTC, datetime

        night = datetime(2026, 8, 12, 22, 30, tzinfo=UTC)
        slots = sorted(shorts_pipeline.PUBLISH_SLOTS_UTC)
        taken = []
        for _ in slots:
            taken.append(shorts_pipeline.next_slot(taken, night))
        self.assertEqual([slot.hour for slot in taken], slots)
        # All on the 13th: none of the batch slips back onto the 12th, whose
        # remaining slot is inside the lead time.
        self.assertEqual({slot.day for slot in taken}, {13})

    def test_a_new_batch_does_not_take_a_slot_the_old_one_holds(self):
        from datetime import UTC, datetime

        still_queued = datetime(2026, 8, 13, 23, 0, tzinfo=UTC)
        night = datetime(2026, 8, 13, 21, 30, tzinfo=UTC)
        taken = [still_queued]
        for _ in range(3):
            taken.append(shorts_pipeline.next_slot(taken, night))
        self.assertEqual(len(set(taken)), 4)
        self.assertNotIn(still_queued, taken[1:])


class BatchNumberingTests(unittest.TestCase):
    """The operator writes 1, 2, 3 on the clips meaning "of tonight's batch".
    A running counter would have made tonight's first idea #11 while the
    caption still said 1, publishing it under a previous day's concept."""

    def _batch(self, days_ago):
        """A batch stamp counted back from today.

        These were fixed dates in August. Ideas expire after
        PENDING_EXPIRY_DAYS, so once the calendar moved past that every entry
        here became stale, match_pending returned None and all four tests
        failed on their own age rather than on anything in the code.
        """
        from datetime import UTC, datetime, timedelta

        return (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()

    def _entry(self, index, creature, batch):
        return {
            "index": index,
            "batch": batch,
            "created_at": batch,
            "concept": _concept(creature).__dict__ | {},
        }

    def test_a_number_picks_from_the_newest_batch(self):
        pending = [
            self._entry(1, "moth", self._batch(2)),
            self._entry(1, "scorpion", self._batch(1)),
        ]
        chosen = shorts_pipeline.match_pending("1", pending)
        self.assertEqual(chosen["concept"]["creature"], "scorpion")

    def test_an_older_idea_is_still_reachable_by_name(self):
        pending = [
            self._entry(1, "moth", self._batch(2)),
            self._entry(1, "scorpion", self._batch(1)),
        ]
        chosen = shorts_pipeline.match_pending("the moth one", pending)
        self.assertEqual(chosen["concept"]["creature"], "moth")

    def test_a_number_outside_the_newest_batch_falls_back(self):
        pending = [
            self._entry(7, "moth", self._batch(2)),
            self._entry(1, "scorpion", self._batch(1)),
        ]
        chosen = shorts_pipeline.match_pending("7", pending)
        self.assertEqual(chosen["concept"]["creature"], "moth")

    def test_entries_without_a_batch_never_outrank_a_real_one(self):
        """Ideas stored before batches existed must not win a bare number."""
        old = {"index": 1, "created_at": self._batch(3),
               "concept": _concept("moth").__dict__ | {}}
        pending = [old, self._entry(1, "scorpion", self._batch(1))]
        chosen = shorts_pipeline.match_pending("1", pending)
        self.assertEqual(chosen["concept"]["creature"], "scorpion")


class StatePersistenceTests(unittest.TestCase):
    """The watch loop resets its checkout every couple of minutes, so anything
    the pipeline writes and does not commit is destroyed. shorts_queue.json was
    added to the pipeline but not to the loop's commit list, and three queued
    videos were lost before anyone noticed."""

    def _loop_script(self):
        return Path("scripts/inbox_loop.sh").read_text(encoding="utf-8")

    def _state_files(self):
        """Every data file the pipeline persists, from its own constants."""
        return {
            value
            for name, value in vars(shorts_pipeline).items()
            if name.endswith("_FILE") and isinstance(value, str) and value.startswith("data/")
        }

    def test_the_loop_commits_every_state_file(self):
        script = self._loop_script()
        add_lines = [line for line in script.splitlines() if "git add" in line]
        self.assertTrue(add_lines, "the loop must stage its state files")
        staged = " ".join(add_lines)
        for path in self._state_files():
            covered = path in staged or "data/" in staged
            self.assertTrue(covered, f"{path} is never staged, so a refresh would destroy it")

    def test_the_queue_file_is_a_tracked_state_file(self):
        self.assertIn(shorts_pipeline.QUEUE_FILE, self._state_files())

    def test_the_loop_does_not_discard_local_commits(self):
        """A hard reset drops a commit whose push lost a race, taking any
        queued video with it."""
        script = self._loop_script()
        self.assertNotIn("reset -q --hard", script)
        self.assertIn("rebase", script)


class CreatureVarietyTests(unittest.TestCase):
    """Forty-two videos produced five beetles, three crabs, two nautiluses and
    two exact repeats. The exact-name list never stopped a second beetle, and
    the memory window was shorter than the channel's own history."""

    def setUp(self):
        from channel_ops import shorts_prompts
        self.sp = shorts_prompts

    def test_identifying_words_drop_plain_modifiers(self):
        self.assertEqual(self.sp.significant_words("giant brown pelican"), {"pelican"})
        self.assertEqual(self.sp.significant_words("stag beetle"), {"stag", "beetle"})

    def test_an_all_modifier_name_still_yields_words(self):
        """Nothing identifying left would otherwise match every creature."""
        self.assertTrue(self.sp.significant_words("great white"))

    def test_a_second_locomotive_is_caught(self):
        """The real miss: different last words, obviously the same video."""
        words = set(self.sp.family_words(["steam locomotive engine"]))
        self.assertTrue(self.sp._is_repeat("miniature steam locomotive", set(), words))

    def test_a_shared_first_word_is_caught(self):
        words = set(self.sp.family_words(["mantis shrimp"]))
        self.assertTrue(self.sp._is_repeat("mantis", set(), words))

    def test_a_shared_colour_does_not_block(self):
        """"brown pelican" must not rule out every brown animal."""
        words = set(self.sp.family_words(["brown pelican"]))
        self.assertFalse(self.sp._is_repeat("brown bear", set(), words))

    def test_an_unrelated_creature_passes(self):
        words = set(self.sp.family_words(["stag beetle", "hermit crab"]))
        self.assertFalse(self.sp._is_repeat("barn owl", set(), words))

    def test_memory_outlasts_the_published_history(self):
        """40 was smaller than the 42 videos already out, which is exactly how
        the pangolin came back."""
        published = json.loads(Path("data/shorts_published.json").read_text(encoding="utf-8"))
        self.assertGreater(self.sp.RECENT_MEMORY, len(published))

    def _provider(self, batches):
        from dataclasses import asdict

        def concept(creature):
            return asdict(_concept(creature))

        class StubProvider:
            def __init__(self):
                self.calls = 0

            def generate(self, prompt, system_prompt=""):
                batch = batches[min(self.calls, len(batches) - 1)]
                self.calls += 1
                return json.dumps([concept(name) for name in batch])

        return StubProvider()

    def test_a_repeated_family_is_rejected_and_replaced(self):
        provider = self._provider([["jewel beetle"], ["barn owl"]])
        concepts = self.sp.generate_concepts(provider, count=1, avoid=["stag beetle"])
        self.assertEqual([c.creature for c in concepts], ["barn owl"])
        self.assertEqual(provider.calls, 2, "the shortfall must be re-asked")

    def test_an_exact_repeat_is_rejected(self):
        provider = self._provider([["pangolin"], ["tree frog"]])
        concepts = self.sp.generate_concepts(provider, count=1, avoid=["pangolin"])
        self.assertEqual([c.creature for c in concepts], ["tree frog"])

    def test_two_of_the_same_family_in_one_batch_are_caught(self):
        provider = self._provider([["fiddler crab", "hermit crab"], ["barn owl"]])
        concepts = self.sp.generate_concepts(provider, count=2, avoid=[])
        words = [frozenset(self.sp.significant_words(c.creature)) for c in concepts]
        self.assertFalse(words[0] & words[1], "two of one family slipped through")

    def test_a_fresh_creature_is_kept_without_a_second_call(self):
        provider = self._provider([["barn owl"]])
        concepts = self.sp.generate_concepts(provider, count=1, avoid=["stag beetle"])
        self.assertEqual([c.creature for c in concepts], ["barn owl"])
        self.assertEqual(provider.calls, 1)

    def test_a_day_is_never_left_without_ideas(self):
        """If everything the model offers repeats, shipping a repeat still
        beats sending nothing."""
        provider = self._provider([["stag beetle"]])
        concepts = self.sp.generate_concepts(provider, count=1, avoid=["stag beetle"])
        self.assertEqual(len(concepts), 1)


class SubjectChoiceTests(unittest.TestCase):
    """The subject decides the video's fate, so the rule that picks it is
    bound here rather than left to survive on good intentions.

    Of the first fifty-five videos, eleven passed ten thousand views and those
    eleven carried seventy-six per cent of all views. What they share is one
    feature large enough to read from the silhouette — the tarsier's eyes, the
    fiddler crab's claw, the rhinoceros beetle's horn. The ones that went
    nowhere are creatures whose interest lives in fine detail."""

    def test_the_instructions_ask_for_one_readable_feature(self):
        text = shorts_prompts._CONCEPT_INSTRUCTIONS
        self.assertIn("silhouette", text)
        self.assertIn("one dominant, instantly readable feature", text)

    def test_the_rule_comes_before_the_writing_rules(self):
        """Placed after the grammar rules it reads as an afterthought, and the
        model treats it as one."""
        text = shorts_prompts._CONCEPT_INSTRUCTIONS
        self.assertLess(text.index("CHOOSING THE SUBJECT"), text.index("PLAUSIBLE FOLDING"))

    def test_the_evidence_travels_with_the_rule(self):
        """A bare instruction gets softened by the next person who edits it;
        the examples are what make it checkable."""
        text = shorts_prompts._CONCEPT_INSTRUCTIONS
        for creature in ("Tarsier", "Fiddler crab", "Rhinoceros beetle"):
            self.assertIn(creature, text)


class BadgeTests(unittest.TestCase):
    """The corner badge ran from video 43 to 62 and is off again.

    It never showed the benefit it was added for: over those twenty videos
    subscribers per thousand views went 0.36 -> 0.24 and likes per thousand
    4.13 -> 3.80. Neither reading is conclusive on its own, but nothing pointed
    the other way, so it is not drawn any more."""

    def setUp(self):
        self._workspace = TemporaryDirectory()
        self.root = Path(self._workspace.name)
        (self.root / "data").mkdir()
        self.addCleanup(self._workspace.cleanup)

    def test_no_badge_is_drawn_on_a_published_clip(self):
        """The overlay call must carry the hook alone; a badge argument left
        behind here would keep drawing it."""
        import inspect
        source = inspect.getsource(shorts_pipeline._publish_queued)
        self.assertIn("_burn_hook(destination, metadata.hook)", source)

    def test_the_overlay_can_still_draw_one(self):
        """Kept so the idea can be retested without rebuilding it."""
        import inspect
        self.assertIn(
            "badge", inspect.signature(shorts_pipeline.video_overlay.add_hook).parameters
        )

    def test_a_failed_overlay_still_publishes_the_clip(self):
        """A missing hook costs some reach; refusing to publish costs the
        whole video."""
        from channel_ops import video_overlay

        def explode(*args, **kwargs):
            raise video_overlay.OverlayUnavailable("no ffmpeg here")

        original = shorts_pipeline.video_overlay.add_hook
        shorts_pipeline.video_overlay.add_hook = explode
        self.addCleanup(setattr, shorts_pipeline.video_overlay, "add_hook", original)

        clip = self.root / "clip.mp4"
        clip.write_bytes(b"video")
        self.assertEqual(shorts_pipeline._burn_hook(clip, "Hook", "№1 · @x"), clip)

    def test_nothing_to_draw_leaves_the_clip_alone(self):
        clip = self.root / "clip.mp4"
        clip.write_bytes(b"video")
        self.assertEqual(shorts_pipeline._burn_hook(clip, "", ""), clip)

    def test_the_badge_field_survives_in_the_record(self):
        """The before/after split is only readable while every record carries
        the field — dropping it would erase which videos had one."""
        import inspect
        source = inspect.getsource(shorts_pipeline._publish_queued)
        self.assertIn('"badge": ""', source)


class CatchUpRunTests(unittest.TestCase):
    """The prompt job gets one shot a night. Twice now Gemini answered 503 to
    every retry and the night produced no ideas at all, so later runs repeat
    the attempt — and must stand down once a batch has landed."""

    def setUp(self):
        self._workspace = TemporaryDirectory()
        self.root = Path(self._workspace.name)
        (self.root / "data").mkdir()
        self.addCleanup(self._workspace.cleanup)

    def _write_batch(self, hours_ago):
        from datetime import UTC, datetime, timedelta
        stamp = (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat()
        (self.root / shorts_pipeline.PENDING_FILE).write_text(
            json.dumps([{"index": 1, "batch": stamp, "created_at": stamp,
                         "concept": _concept("moth").__dict__ | {}}]),
            encoding="utf-8",
        )

    def test_a_recent_batch_is_detected(self):
        self._write_batch(1)
        self.assertTrue(shorts_pipeline.has_recent_batch(6, self.root))

    def test_an_old_batch_does_not_count(self):
        self._write_batch(30)
        self.assertFalse(shorts_pipeline.has_recent_batch(6, self.root))

    def test_no_pending_file_means_nothing_recent(self):
        self.assertFalse(shorts_pipeline.has_recent_batch(6, self.root))

    def test_an_unreadable_stamp_is_ignored(self):
        (self.root / shorts_pipeline.PENDING_FILE).write_text(
            json.dumps([{"batch": "dün"}]), encoding="utf-8")
        self.assertFalse(shorts_pipeline.has_recent_batch(6, self.root))

    def test_the_catch_up_sends_nothing_when_ideas_arrived(self):
        self._write_batch(1)

        class StubProvider:
            def generate(self, prompt, system_prompt=""):
                raise AssertionError("the model must not be called again")

        pairs = shorts_pipeline.send_daily_prompts(
            StubProvider(), count=3, root=self.root, skip_if_recent_hours=6
        )
        self.assertEqual(pairs, [])

    def test_the_catch_up_runs_when_the_night_produced_nothing(self):
        sent = []

        def fake_pairs(provider, *, count, root=None):
            from channel_ops.shorts_prompts import PromptPair
            return [PromptPair(_concept("barn owl"), "t2i", "i2v") for _ in range(count)]

        original = shorts_pipeline.generate_prompt_pairs
        original_send = shorts_pipeline.notifications.send_message
        shorts_pipeline.generate_prompt_pairs = fake_pairs
        shorts_pipeline.notifications.send_message = sent.append
        self.addCleanup(setattr, shorts_pipeline, "generate_prompt_pairs", original)
        self.addCleanup(setattr, shorts_pipeline.notifications, "send_message", original_send)

        pairs = shorts_pipeline.send_daily_prompts(
            object(), count=2, root=self.root, skip_if_recent_hours=6
        )
        self.assertEqual(len(pairs), 2)


class ResendCommandTests(unittest.TestCase):
    """Unused ideas scroll out of reach in the chat, and the pool can hold
    leftovers from several nights — each numbered from one, so Telegram showed
    "1, 2, 1" and a caption of "1" was ambiguous."""

    def setUp(self):
        self._workspace = TemporaryDirectory()
        self.root = Path(self._workspace.name)
        (self.root / "data").mkdir()
        self.addCleanup(self._workspace.cleanup)
        self.sent = []

        from channel_ops import shorts_prompts

        original_send = shorts_pipeline.notifications.send_message
        original_render = shorts_pipeline.shorts_prompts.render
        shorts_pipeline.notifications.send_message = self.sent.append
        shorts_pipeline.shorts_prompts.render = lambda c: shorts_prompts.PromptPair(c, "t2i", "i2v")
        self.addCleanup(setattr, shorts_pipeline.notifications, "send_message", original_send)
        self.addCleanup(setattr, shorts_pipeline.shorts_prompts, "render", original_render)

    def _write(self, entries):
        (self.root / shorts_pipeline.PENDING_FILE).write_text(
            json.dumps(entries), encoding="utf-8")

    def _read(self):
        return json.loads((self.root / shorts_pipeline.PENDING_FILE).read_text(encoding="utf-8"))

    def _entry(self, index, creature, batch):
        return {"index": index, "batch": batch, "created_at": batch,
                "concept": _concept(creature).__dict__ | {}}

    def test_two_batches_are_renumbered_into_one_sequence(self):
        self._write([
            self._entry(1, "brown pelican", "2026-08-19T20:52:00+00:00"),
            self._entry(2, "periodical cicada", "2026-08-19T20:52:00+00:00"),
            self._entry(1, "raven", "2026-08-21T20:49:00+00:00"),
        ])
        shorts_pipeline.resend_pending(self.root)
        entries = self._read()
        self.assertEqual([e["index"] for e in entries], [1, 2, 3])
        self.assertEqual(len({e["batch"] for e in entries}), 1, "all one batch now")

    def test_a_number_matches_the_idea_that_was_listed(self):
        """The whole point: after resending, "2" is the second one shown."""
        self._write([
            self._entry(1, "brown pelican", "2026-08-19T20:52:00+00:00"),
            self._entry(2, "periodical cicada", "2026-08-19T20:52:00+00:00"),
            self._entry(1, "raven", "2026-08-21T20:49:00+00:00"),
        ])
        shorts_pipeline.resend_pending(self.root)
        chosen = shorts_pipeline.match_pending("2", self._read())
        self.assertEqual(chosen["concept"]["creature"], "periodical cicada")

    def test_used_ideas_are_not_resent(self):
        self._write([
            self._entry(1, "brown pelican", "2026-08-19T20:52:00+00:00"),
            dict(self._entry(2, "moth", "2026-08-19T20:52:00+00:00"),
                 used_at="2026-08-20T00:00:00+00:00"),
        ])
        pairs = shorts_pipeline.resend_pending(self.root)
        self.assertEqual([p.concept.creature for p in pairs], ["brown pelican"])

    def test_an_empty_pool_sends_nothing(self):
        self._write([])
        self.assertEqual(shorts_pipeline.resend_pending(self.root), [])
        self.assertEqual(self.sent, [])

    def test_the_command_tells_the_operator_when_the_pool_is_empty(self):
        self._write([])
        shorts_pipeline._resend_prompts(self.root)
        self.assertIn("kullanılmamış fikir yok", " ".join(self.sent))

    def test_a_failed_resend_does_not_raise(self):
        """A video waiting behind the command must still publish."""
        def explode(concept):
            raise RuntimeError("template missing")

        shorts_pipeline.shorts_prompts.render = explode
        self._write([self._entry(1, "brown pelican", "2026-08-19T20:52:00+00:00")])
        shorts_pipeline._resend_prompts(self.root)  # must not raise
        self.assertIn("gönderilemedi", " ".join(self.sent))

    def test_the_command_words_cover_the_obvious_turkish(self):
        for word in ("promptlar", "fikirler", "prompt"):
            self.assertIn(word, shorts_pipeline.PROMPT_COMMANDS)

    def test_report_and_prompt_commands_do_not_overlap(self):
        self.assertFalse(shorts_pipeline.PROMPT_COMMANDS & shorts_pipeline.REPORT_COMMANDS)


class QueueOrderTests(unittest.TestCase):
    """The number written on a clip matches it to its idea inside one batch.
    It is not a release order, and a leftover from an earlier day sits ahead
    of today's "1" — which read as the numbering having gone wrong when it had
    not. The real order is now both shown and askable.
    """

    def setUp(self):
        self._workspace = TemporaryDirectory()
        self.root = Path(self._workspace.name)
        (self.root / "data").mkdir()
        self.addCleanup(self._workspace.cleanup)
        self.sent = []
        original = shorts_pipeline.notifications.send_message
        shorts_pipeline.notifications.send_message = self.sent.append
        self.addCleanup(
            setattr, shorts_pipeline.notifications, "send_message", original
        )

    def _write(self, queue):
        (self.root / shorts_pipeline.QUEUE_FILE).write_text(
            json.dumps(queue), encoding="utf-8"
        )

    @staticmethod
    def _entry(creature, publish_at):
        return {"publish_at": publish_at, "concept": {"creature": creature}}

    def test_a_clip_behind_a_leftover_is_told_so(self):
        queue = [
            self._entry("fennec fox", "2026-08-26T13:00:00+00:00"),
            self._entry("swordfish", "2026-08-26T23:00:00+00:00"),
        ]
        line = shorts_pipeline._position_line(queue, queue[1])
        self.assertIn("2.", line)
        self.assertIn("fennec fox", line)

    def test_the_first_in_line_gets_no_position_line(self):
        """Nothing is ahead of it, so there is no order to explain."""
        queue = [self._entry("fennec fox", "2026-08-26T13:00:00+00:00")]
        self.assertEqual(shorts_pipeline._position_line(queue, queue[0]), "")

    def test_position_follows_the_slot_not_the_arrival_order(self):
        """The leftover was queued a day earlier but appended last."""
        queue = [
            self._entry("swordfish", "2026-08-26T23:00:00+00:00"),
            self._entry("fennec fox", "2026-08-26T13:00:00+00:00"),
        ]
        self.assertEqual(shorts_pipeline._position_line(queue, queue[1]), "")
        self.assertIn("2.", shorts_pipeline._position_line(queue, queue[0]))

    def test_the_queue_command_lists_releases_in_order(self):
        self._write([
            self._entry("flamingo", "2026-08-27T23:00:00+00:00"),
            self._entry("fennec fox", "2026-08-26T13:00:00+00:00"),
            self._entry("swordfish", "2026-08-26T23:00:00+00:00"),
        ])
        shorts_pipeline._send_queue(self.root)
        message = " ".join(self.sent)
        self.assertLess(message.index("fennec fox"), message.index("swordfish"))
        self.assertLess(message.index("swordfish"), message.index("flamingo"))

    def test_the_queue_command_says_so_when_nothing_waits(self):
        self._write([])
        shorts_pipeline._send_queue(self.root)
        self.assertIn("boş", " ".join(self.sent))

    def test_a_broken_queue_file_does_not_stop_a_publish(self):
        (self.root / shorts_pipeline.QUEUE_FILE).write_text("[{}]", encoding="utf-8")
        shorts_pipeline._send_queue(self.root)  # must not raise

    def test_both_clocks_are_shown(self):
        from datetime import UTC, datetime
        label = shorts_pipeline._slot_label(
            datetime(2026, 8, 26, 23, 0, tzinfo=UTC)
        )
        self.assertIn("26.08 23:00 UTC", label)
        self.assertIn("02:00 TRT", label)

    def test_the_command_words_cover_the_obvious_turkish(self):
        for word in ("kuyruk", "sıra", "queue"):
            self.assertIn(word, shorts_pipeline.QUEUE_COMMANDS)

    def test_the_commands_do_not_overlap_each_other(self):
        for other in (shorts_pipeline.REPORT_COMMANDS, shorts_pipeline.PROMPT_COMMANDS):
            self.assertFalse(shorts_pipeline.QUEUE_COMMANDS & other)


class IntakeIsolationTests(unittest.TestCase):
    """Reading the inbox and releasing a queued video are independent jobs.
    They were chained, so one failed Telegram fetch left a video whose slot
    had already arrived sitting in the queue."""

    def setUp(self):
        self._workspace = TemporaryDirectory()
        self.root = Path(self._workspace.name)
        (self.root / "data").mkdir()
        self.addCleanup(self._workspace.cleanup)
        self.released = []

        def fake_publish(provider, root=None):
            self.released.append(True)
            return [{"creature": "moth"}]

        def fake_intake(provider, root):
            raise RuntimeError("Telegram unreachable")

        for name, replacement in (("publish_due", fake_publish),
                                  ("_accept_incoming", fake_intake)):
            original = getattr(shorts_pipeline, name)
            setattr(shorts_pipeline, name, replacement)
            self.addCleanup(setattr, shorts_pipeline, name, original)

    def test_a_due_video_still_goes_out_when_the_inbox_fails(self):
        with self.assertRaises(RuntimeError):
            shorts_pipeline.process_inbox(object(), root=self.root)
        self.assertEqual(len(self.released), 1, "the release must not be skipped")

    def test_the_inbox_failure_is_still_reported(self):
        """Swallowing it would make a broken inbox look like a quiet one."""
        with self.assertRaises(RuntimeError) as caught:
            shorts_pipeline.process_inbox(object(), root=self.root)
        self.assertIn("Telegram unreachable", str(caught.exception))

    def test_a_healthy_poll_returns_the_published_records(self):
        shorts_pipeline._accept_incoming = lambda provider, root: []
        records = shorts_pipeline.process_inbox(object(), root=self.root)
        self.assertEqual(records, [{"creature": "moth"}])
