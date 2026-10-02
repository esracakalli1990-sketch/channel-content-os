"""Tests for the figure the Partner Programme gate is counted in.

Kept in their own file rather than appended to test_analysis.py because a
second session is editing that file on another branch, and two appends to the
same spot conflict for no reason.

THE FAILURE THESE EXIST FOR. Since March 2025 a Shorts "view" counts every
play and replay, while the thresholds count engaged views. On this channel the
ratio is 44.8% -- 2,259,715 views against 1,012,547 engaged over 88 days.
Counting the gate in raw views reported it as PASSED when Studio's own
eligibility page showed 795K against the 3M target. That was told to the
operator as fact and had to be withdrawn.
"""
from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from channel_ops import channel_analysis as ca

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)

_PER_VIDEO_COLUMNS = [
    "video", "views", "estimatedMinutesWatched", "averageViewDuration",
    "averageViewPercentage", "subscribersGained", "likes", "shares", "comments",
]


def _dump(per_video_rows, engaged_rows=None, records=None):
    analytics = {"per_video": {"columns": _PER_VIDEO_COLUMNS,
                               "rows": per_video_rows}}
    if engaged_rows is not None:
        analytics["engaged_per_video"] = {
            "columns": ["video", "views", "engagedViews"], "rows": engaged_rows
        }
    published = records or [
        {"youtube_video_id": row[0], "creature": "x",
         "published_at": (NOW - timedelta(days=10)).isoformat()}
        for row in per_video_rows
    ]
    return {
        "collected_at": NOW.isoformat(),
        "published": published,
        "long_form": [],
        "totals": {"subscribers": 1720, "views": 2618818, "videos": 128},
        "data_api": {row[0]: {"views": row[1], "seconds": 9}
                     for row in per_video_rows},
        "analytics": analytics,
        "errors": {},
    }


class EngagedCountingTests(unittest.TestCase):
    def test_the_gate_counts_engaged_views_not_raw_views(self):
        """The whole correction. Raw 2,259,715 read as passed; engaged
        1,012,547 is a third of the way."""
        dump = _dump(
            [["v1", 2_259_715, 1000, 12, 130.0, 50, 500, 20, 10]],
            engaged_rows=[["v1", 2_259_715, 1_012_547]],
        )
        gates = ca.thresholds(dump, [])
        self.assertEqual(gates["early_shorts_views"]["value"], 1_012_547)
        self.assertLess(gates["early_shorts_views"]["percent"], 50)

    def test_the_name_says_which_measure_was_used(self):
        dump = _dump(
            [["v1", 1000, 100, 12, 130.0, 5, 50, 2, 1]],
            engaged_rows=[["v1", 1000, 450]],
        )
        gates = ca.thresholds(dump, [])
        self.assertIn("etkileşimli", gates["early_shorts_views"]["name"])
        self.assertIn("ETKİLEŞİMLİ", gates["shorts_floor_note"])

    def test_missing_engaged_data_is_flagged_loudly(self):
        """Before the engaged queries exist the report must say it cannot
        measure this, not print a confident bar next to a wrong number."""
        dump = _dump([["v1", 1000, 100, 12, 130.0, 5, 50, 2, 1]])
        gates = ca.thresholds(dump, [])
        self.assertFalse(gates["shorts_engaged"])
        self.assertIn("⚠️", gates["early_shorts_views"]["name"])
        self.assertIn("Güvenme", gates["shorts_floor_note"])

    def test_raw_views_are_never_mixed_into_an_engaged_total(self):
        """A video Analytics has not reported yet has no engaged figure. Adding
        its raw views would produce a number that is neither measure."""
        dump = _dump(
            [["reported", 1000, 100, 12, 130.0, 5, 50, 2, 1]],
            engaged_rows=[["reported", 1000, 450]],
        )
        dump["data_api"]["brandnew"] = {"views": 400_000, "seconds": 9}
        gates = ca.thresholds(dump, [])
        self.assertEqual(gates["early_shorts_views"]["value"], 450)

    def test_both_tiers_read_the_same_numerator(self):
        dump = _dump(
            [["v1", 1000, 100, 12, 130.0, 5, 50, 2, 1]],
            engaged_rows=[["v1", 1000, 450]],
        )
        gates = ca.thresholds(dump, [])
        self.assertEqual(gates["early_shorts_views"]["value"],
                         gates["shorts_views"]["value"])
        self.assertEqual(gates["early_shorts_views"]["goal"], 3_000_000)
        self.assertEqual(gates["shorts_views"]["goal"], 10_000_000)

    def test_the_studio_figure_reproduces(self):
        """Studio showed 795K eligible on 23 September. Fed the same split,
        the gate has to land there rather than near three million."""
        dump = _dump(
            [["v1", 1_776_000, 1000, 12, 130.0, 50, 500, 20, 10]],
            engaged_rows=[["v1", 1_776_000, 795_000]],
        )
        gates = ca.thresholds(dump, [])
        self.assertEqual(gates["early_shorts_views"]["value"], 795_000)
        self.assertAlmostEqual(gates["early_shorts_views"]["percent"], 26.5,
                               places=1)


class EngagedLookupTests(unittest.TestCase):
    def test_absent_report_reads_as_empty_not_zero(self):
        self.assertEqual(ca._engaged_by_video({"analytics": {}}), {})

    def test_a_report_without_the_column_reads_as_empty(self):
        self.assertEqual(ca._engaged_by_video({"analytics": {
            "engaged_per_video": {"columns": ["video", "views"],
                                  "rows": [["v1", 5]]}}}), {})

    def test_rows_are_keyed_by_video(self):
        found = ca._engaged_by_video({"analytics": {"engaged_per_video": {
            "columns": ["video", "views", "engagedViews"],
            "rows": [["a", 100, 44], ["b", 200, 90]],
        }}})
        self.assertEqual(found, {"a": 44, "b": 90})


if __name__ == "__main__":
    unittest.main()


class MeasureChangeTests(unittest.TestCase):
    """A rate must not be computed across a change of ruler.

    On 28 September the Shorts gate moved from raw views to engaged views and
    the recorded figure dropped from 3,108,912 to 1,012,415 -- same channel,
    different measure. The rate calculation compared the two, concluded the
    threshold was going backwards, and printed "artmıyor -- bu hızla
    ulaşılamaz" on a figure that had risen 64% in four days."""

    def _history(self, rows):
        return [
            {"at": (NOW - timedelta(days=days)).isoformat(),
             "shorts_views_90d": value, **({"shorts_measure": measure}
                                           if measure else {})}
            for days, value, measure in rows
        ]

    def test_rows_counted_the_other_way_are_ignored(self):
        history = self._history([
            (6, 3_108_912, "views"),      # eski olcu, kullanilmamali
            (4, 1_012_415, "engaged"),
        ])
        rate = ca._rate_per_day(history, "shorts_views_90d", 1_656_383,
                                now=NOW, measure="engaged")
        self.assertAlmostEqual(rate, (1_656_383 - 1_012_415) / 4, delta=1)

    def test_unlabelled_rows_are_ignored_too(self):
        """Unlabelled means unknown, not "same as now". Every row written
        before the label existed is from the old measure."""
        history = self._history([(4, 2_259_505, None)])
        self.assertIsNone(
            ca._rate_per_day(history, "shorts_views_90d", 1_656_383,
                             now=NOW, measure="engaged")
        )

    def test_without_a_measure_every_row_still_counts(self):
        """Subscribers and watch hours never changed ruler, so they must not
        be filtered by a label they do not carry."""
        history = self._history([(4, 500, None)])
        rate = ca._rate_per_day(history, "shorts_views_90d", 900, now=NOW)
        self.assertAlmostEqual(rate, 100.0, delta=0.1)

    def test_the_snapshot_records_which_measure_it_used(self):
        dump = _dump(
            [["v1", 1000, 100, 12, 130.0, 5, 50, 2, 1]],
            engaged_rows=[["v1", 1000, 450]],
        )
        rows = ca.videos(dump, now=NOW)
        gates = ca.thresholds(dump, [])
        self.assertEqual(ca.snapshot(dump, rows, gates)["shorts_measure"],
                         "engaged")

    def test_a_dump_without_engaged_data_is_labelled_views(self):
        dump = _dump([["v1", 1000, 100, 12, 130.0, 5, 50, 2, 1]])
        rows = ca.videos(dump, now=NOW)
        gates = ca.thresholds(dump, [])
        self.assertEqual(ca.snapshot(dump, rows, gates)["shorts_measure"],
                         "views")
