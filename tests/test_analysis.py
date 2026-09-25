"""Tests for the analysis engine.

The point of most of these is not that a number comes out right, but that a
number does NOT come out when it should not. Four findings on this channel were
read off too few videos, acted on, and withdrawn; the expensive one cost
nineteen hitless videos. So the tests that matter most here are the ones that
assert the system stays silent.
"""
from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from channel_ops import channel_analysis as ca
from channel_ops.channel_data import parse_duration

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


def _dump(records, stats=None, live_rows=None, totals=None, long_form=None):
    return {
        "collected_at": NOW.isoformat(),
        "published": records,
        "long_form": long_form or [],
        "totals": totals or {"subscribers": 0, "views": 0, "videos": 0},
        "data_api": stats or {},
        "analytics": {"per_video": {
            "columns": ["video", "views", "estimatedMinutesWatched",
                        "averageViewDuration", "averageViewPercentage",
                        "subscribersGained", "likes", "shares", "comments"],
            "rows": live_rows or [],
        }},
        "errors": {},
    }


def _record(vid, creature="pangolin", hours_ago=100, template="be41f721", hour=None):
    published = NOW - timedelta(hours=hours_ago)
    if hour is not None:
        published = published.replace(hour=hour)
    return {
        "youtube_video_id": vid,
        "creature": creature,
        "published_at": published.isoformat(),
        "template_version": template,
        "idea_version": "730ac4e9c9e2",
    }


class MannWhitneyTests(unittest.TestCase):
    """The gate everything else stands on."""

    def test_identical_samples_are_not_a_difference(self):
        sample = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        self.assertGreater(ca.mann_whitney_p(sample, list(sample)), 0.9)

    def test_clearly_separated_samples_are(self):
        low = list(range(10))
        high = list(range(100, 110))
        self.assertLess(ca.mann_whitney_p(low, high), 0.01)

    def test_one_outlier_does_not_manufacture_a_finding(self):
        """The failure mode this test exists for: a single 594,000-view video
        sitting in a set whose median is 2,000. A test on means would call that
        a difference. A rank test must not."""
        ordinary = [1800, 2100, 1500, 2400, 1900, 2000, 1700, 2200]
        with_a_hit = [1850, 2050, 1550, 2350, 594000, 1950, 1750, 2150]
        self.assertGreater(ca.mann_whitney_p(ordinary, with_a_hit), ca.SIGNIFICANCE)

    def test_an_empty_side_is_not_significant(self):
        self.assertEqual(ca.mann_whitney_p([], [1, 2, 3]), 1.0)


class ComparisonTests(unittest.TestCase):
    def test_small_groups_refuse_to_answer(self):
        """Seven a side is not a finding however far apart the medians are."""
        result = ca.compare("a", [1] * 7, "b", [1000] * 7)
        self.assertIn("yeterli veri yok", result["verdict"])
        self.assertIsNone(result["p"])

    def test_a_real_difference_is_reported_with_its_p_value(self):
        result = ca.compare("a", list(range(10)), "b", list(range(100, 110)))
        self.assertIn("b daha iyi", result["verdict"])
        self.assertLess(result["p"], 0.05)

    def test_a_difference_in_medians_alone_is_not_enough(self):
        """Overlapping spreads with different medians: the honest answer is
        'fark yok', and this is exactly the call that was got wrong before."""
        a = [10, 20, 30, 40, 50, 60, 70, 900]
        b = [15, 25, 35, 45, 55, 65, 75, 85]
        result = ca.compare("a", a, "b", b)
        self.assertEqual(result["verdict"], "fark yok")


class MaturityTests(unittest.TestCase):
    def test_a_fresh_video_is_not_judged(self):
        rows = ca.videos(_dump([_record("v1", hours_ago=5)],
                               stats={"v1": {"views": 10}}), now=NOW)
        self.assertFalse(rows[0]["mature"])
        self.assertFalse(rows[0]["dead"], "5 saatlik video ölü sayılamaz")

    def test_a_two_day_old_video_is(self):
        rows = ca.videos(_dump([_record("v1", hours_ago=49)],
                               stats={"v1": {"views": 10}}), now=NOW)
        self.assertTrue(rows[0]["mature"])
        self.assertTrue(rows[0]["dead"])

    def test_fresh_videos_stay_out_of_comparisons(self):
        records = [_record(f"v{i}", hours_ago=100, template="addb5d8d") for i in range(10)]
        records += [_record(f"n{i}", hours_ago=1, template="be41f721") for i in range(10)]
        stats = {r["youtube_video_id"]: {"views": 5000} for r in records[:10]}
        stats |= {r["youtube_video_id"]: {"views": 3} for r in records[10:]}
        rows = ca.videos(_dump(records, stats=stats), now=NOW)
        for result in ca.cohorts(rows, "views"):
            self.assertNotIn("daha iyi", result["verdict"])


class RateTests(unittest.TestCase):
    """Per-day growth, and the reason it is not measured over two days.

    Every case here pins the clock. Twice before, tests in this repo aged into
    failure because they compared a fixed date against a moving one; widening
    the tolerance hides that instead of removing it."""

    def test_two_measurements_are_needed(self):
        gate = ca._gate("Abone", 500, 1000, [], "subscribers", now=NOW)
        self.assertIsNone(gate["rate_per_day"])
        self.assertIn("en az iki ölçüm", gate["note"])

    def test_the_rate_uses_the_oldest_snapshot_in_the_window(self):
        """A viral spike makes a two-day rate meaningless: on 21 September the
        channel briefly ran at 140,000 views a day."""
        history = [
            {"at": (NOW - timedelta(days=10)).isoformat(), "subscribers": 300},
            {"at": (NOW - timedelta(days=1)).isoformat(), "subscribers": 520},
        ]
        rate = ca._rate_per_day(history, "subscribers", 541, now=NOW)
        self.assertAlmostEqual(rate, (541 - 300) / 10, places=6)

    def test_snapshots_older_than_the_window_are_ignored(self):
        history = [{"at": (NOW - timedelta(days=90)).isoformat(), "subscribers": 1}]
        self.assertIsNone(ca._rate_per_day(history, "subscribers", 541, now=NOW))

    def test_a_distant_projection_is_refused_not_printed(self):
        """A gate crawling toward 4,000 hours once produced "2122-07-17". That
        is arithmetic, not a forecast, and a date invites planning around it."""
        history = [{"at": (NOW - timedelta(days=7)).isoformat(), "watch_hours": 0.5}]
        gate = ca._gate("İzlenme saati", 0.8, 4000, history, "watch_hours", now=NOW)
        self.assertIsNone(gate["eta"])
        self.assertIn("ulaşılamaz", gate["note"])
        self.assertIn("yıl", gate["note"])

    def test_a_small_rate_is_not_rounded_away_to_zero(self):
        history = [{"at": (NOW - timedelta(days=10)).isoformat(), "watch_hours": 0.1}]
        gate = ca._gate("İzlenme saati", 0.5, 4000, history, "watch_hours", now=NOW)
        self.assertGreater(gate["rate_per_day"], 0)

    def test_a_flat_figure_reports_unreachable_rather_than_a_date(self):
        history = [{"at": (NOW - timedelta(days=7)).isoformat(), "watch_hours": 0.7}]
        gate = ca._gate("İzlenme saati", 0.7, 4000, history, "watch_hours", now=NOW)
        self.assertIsNone(gate["eta"])
        self.assertIn("ulaşılamaz", gate["note"])


class ThresholdTests(unittest.TestCase):
    def test_shorts_and_long_watch_time_are_counted_separately(self):
        """Shorts watch time does not count toward the 4,000 hours. Adding the
        two together makes the threshold look far closer than it is."""
        records = [_record("short1")]
        long_form = [{"youtube_video_id": "long1", "published_at": NOW.isoformat()}]
        stats = {"short1": {"views": 500000, "seconds": 9},
                 "long1": {"views": 107, "seconds": 584}}
        live = [["short1", 500000, 41000, 12, 141.0, 100, 1000, 5, 3],
                ["long1", 107, 45, 25, 4.4, 0, 0, 0, 0]]
        gates = ca.thresholds(
            _dump(records, stats=stats, live_rows=live, long_form=long_form,
                  totals={"subscribers": 541}), [])
        self.assertAlmostEqual(gates["watch_hours"]["value"], 0.8, delta=0.1)
        self.assertEqual(gates["shorts_views"]["value"], 500000)

    def test_a_long_video_is_detected_by_its_duration_too(self):
        """Not only by being in long_published.json — a video uploaded any
        other way still produces watch hours and must still be counted."""
        records = [_record("v1")]
        stats = {"v1": {"views": 100, "seconds": 600}}
        live = [["v1", 100, 300, 180, 30.0, 2, 5, 1, 0]]
        gates = ca.thresholds(_dump(records, stats=stats, live_rows=live), [])
        self.assertEqual(gates["watch_hours"]["value"], 5.0)
        self.assertEqual(gates["shorts_views"]["value"], 0)

    def test_both_gates_are_reported_not_just_the_easiest(self):
        gates = ca.thresholds(_dump([]), [])
        self.assertIn("subscribers", gates)
        self.assertIn("watch_hours", gates)
        self.assertIn("shorts_views", gates)

    def test_the_lower_tier_is_tracked_as_well(self):
        """Reporting only the upper tier showed 17% when the nearest real
        milestone stood at 58% -- the difference between "far off" and "more
        than halfway"."""
        gates = ca.thresholds(_dump([]), [])
        for key in ("early_subscribers", "early_watch_hours", "early_shorts_views"):
            self.assertIn(key, gates)
        self.assertEqual(gates["early_shorts_views"]["goal"], 3_000_000)
        self.assertEqual(gates["shorts_views"]["goal"], 10_000_000)

    def test_the_same_figure_is_measured_against_both_tiers(self):
        """One view count, two goals. If the two ever disagree about the
        numerator, one of them is lying."""
        records = [_record("v1")]
        stats = {"v1": {"views": 1_748_243, "seconds": 9}}
        live = [["v1", 1_748_243, 350000, 12, 130.0, 500, 3000, 20, 10]]
        gates = ca.thresholds(_dump(records, stats=stats, live_rows=live), [])
        self.assertEqual(gates["early_shorts_views"]["value"],
                         gates["shorts_views"]["value"])
        self.assertAlmostEqual(gates["early_shorts_views"]["percent"], 58.27, places=1)

    def test_a_lagging_shorts_figure_is_labelled_a_floor(self):
        """It read 1,748,243 on a day Studio showed 2,054,924. The number was
        right for its window; presenting it as the current total was not."""
        dump = _dump([])
        dump["analytics"]["daily"] = {
            "columns": ["day", "views", "estimatedMinutesWatched",
                        "subscribersGained", "subscribersLost"],
            "rows": [["2026-09-20", 16852, 1587, 18, 5]],
        }
        gates = ca.thresholds(dump, [])
        self.assertIn("gün geriden", gates["shorts_floor_note"])
        self.assertIn("≥", gates["shorts_views"]["name"])
        self.assertIn("≥", gates["early_shorts_views"]["name"])

    def test_uploads_inside_the_window_are_counted(self):
        records = [_record(f"v{i}", hours_ago=24 * i) for i in range(1, 5)]
        old_one = _record("old", hours_ago=24 * 120)
        gates = ca.thresholds(_dump(records + [old_one]), [])
        self.assertEqual(gates["uploads_90d"], 4)


class HitProfileTests(unittest.TestCase):
    def test_a_trait_common_to_everything_shows_no_lift(self):
        """'80% of hits are animals' is not a finding when 80% of everything
        is animals. That mistake is what the 24 August rule was built on."""
        records = [_record(f"v{i}", creature="pangolin") for i in range(20)]
        stats = {f"v{i}": {"views": 50000 if i < 4 else 1000} for i in range(20)}
        profile = ca.hit_profile(ca.videos(_dump(records, stats=stats), now=NOW))
        kinds = [t for t in profile["traits"] if t["dimension"] == "Konu türü"]
        self.assertTrue(kinds)
        for trait in kinds:
            self.assertAlmostEqual(trait["lift"], 1.0, delta=0.01)

    def test_the_warning_is_always_attached(self):
        records = [_record(f"v{i}") for i in range(20)]
        stats = {f"v{i}": {"views": 50000 if i < 4 else 1000} for i in range(20)}
        profile = ca.hit_profile(ca.videos(_dump(records, stats=stats), now=NOW))
        self.assertIn("24 Ağustos", profile["warning"])


class DurationTests(unittest.TestCase):
    def test_seconds_are_read_from_the_iso_duration(self):
        self.assertEqual(parse_duration("PT9S"), 9)
        self.assertEqual(parse_duration("PT9M44S"), 584)
        self.assertEqual(parse_duration("PT1H2M3S"), 3723)

    def test_an_unparseable_duration_is_zero_not_a_crash(self):
        self.assertEqual(parse_duration(""), 0)
        self.assertEqual(parse_duration("garbage"), 0)


class StalenessTests(unittest.TestCase):
    """Added after a report quoted an hour-old cached response as current.

    Subscribers, views, median and hit count were all bit-identical to the
    previous run while Studio showed roughly ten thousand views an hour
    arriving. A measurement system that cannot notice it has gone blind is
    worse than none, because it is trusted."""

    def _daily(self, rows):
        return {"columns": ["day", "views", "estimatedMinutesWatched",
                            "subscribersGained", "subscribersLost"], "rows": rows}

    def test_an_unchanged_counter_is_called_out(self):
        dump = _dump([], totals={"views": 2048124, "subscribers": 1070})
        history = [{"at": (NOW - timedelta(hours=1)).isoformat(), "views": 2048124}]
        result = ca.staleness(dump, history, now=NOW)
        self.assertTrue(result["frozen"])
        self.assertTrue(any("DONMUŞ" in w for w in result["warnings"]))

    def test_a_moving_counter_is_not(self):
        dump = _dump([], totals={"views": 2060000, "subscribers": 1070})
        history = [{"at": (NOW - timedelta(hours=1)).isoformat(), "views": 2048124}]
        self.assertFalse(ca.staleness(dump, history, now=NOW)["frozen"])

    def test_two_runs_minutes_apart_are_not_called_frozen(self):
        """Back-to-back runs legitimately see the same number; the check must
        not cry wolf or it will be ignored when it matters."""
        dump = _dump([], totals={"views": 2048124})
        history = [{"at": (NOW - timedelta(minutes=5)).isoformat(), "views": 2048124}]
        self.assertFalse(ca.staleness(dump, history, now=NOW)["frozen"])

    def test_the_first_ever_run_has_nothing_to_compare(self):
        self.assertFalse(ca.staleness(_dump([]), [], now=NOW)["frozen"])

    def test_trailing_unreported_days_are_not_the_edge(self):
        """A reported zero and a not-yet-reported day look identical here, and
        a run of them was once read as a collapse in views."""
        dump = _dump([])
        dump["analytics"]["daily"] = self._daily([
            ["2026-09-20", 16852, 1587, 18, 5],
            ["2026-09-21", 20873, 1900, 8, 8],
            ["2026-09-22", 0, 0, 0, 0],
            ["2026-09-23", 0, 0, 0, 0],
        ])
        result = ca.staleness(dump, [], now=NOW)
        self.assertEqual(result["analytics_last_day"], "2026-09-21")
        self.assertEqual(result["analytics_lag_days"], 4)
        self.assertTrue(any("geriden" in w for w in result["warnings"]))

    def test_current_analytics_raises_no_warning(self):
        dump = _dump([])
        dump["analytics"]["daily"] = self._daily([
            ["2026-09-24", 16852, 1587, 18, 5],
            ["2026-09-25", 20873, 1900, 8, 8],
        ])
        result = ca.staleness(dump, [], now=NOW)
        self.assertEqual(result["analytics_lag_days"], 0)
        self.assertEqual(result["warnings"], [])

    def test_a_missing_daily_table_is_not_a_crash(self):
        self.assertIsNone(ca.staleness(_dump([]), [], now=NOW)["analytics_last_day"])


class HistoryTests(unittest.TestCase):
    def test_a_snapshot_survives_a_round_trip(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir()
            ca.save_snapshot({"at": NOW.isoformat(), "subscribers": 541}, root)
            ca.save_snapshot({"at": NOW.isoformat(), "subscribers": 600}, root)
            history = ca.load_history(root)
            self.assertEqual([s["subscribers"] for s in history], [541, 600])

    def test_a_corrupt_file_reads_as_empty_rather_than_failing(self):
        """The report must still come out; losing the history is not a reason
        to lose the measurement as well."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir()
            (root / ca.METRICS_FILE).write_text("{not json", encoding="utf-8")
            self.assertEqual(ca.load_history(root), [])

    def test_missing_files_read_as_empty(self):
        with TemporaryDirectory() as tmp:
            self.assertEqual(ca.load_history(Path(tmp)), [])
            self.assertEqual(ca.load_studio(Path(tmp)), [])


class RatesTests(unittest.TestCase):
    def test_engagement_is_per_thousand_views_not_raw(self):
        """A hit collects more likes by being a hit. The question is whether
        the people who watched it liked it more."""
        records = [_record("v1")]
        stats = {"v1": {"views": 100000, "likes": 200, "comments": 10}}
        row = ca.videos(_dump(records, stats=stats), now=NOW)[0]
        self.assertEqual(row["likes_per_1k"], 2.0)

    def test_a_video_with_no_views_does_not_divide_by_zero(self):
        records = [_record("v1")]
        stats = {"v1": {"views": 0, "likes": 0}}
        row = ca.videos(_dump(records, stats=stats), now=NOW)[0]
        self.assertIsNone(row["likes_per_1k"])


if __name__ == "__main__":
    unittest.main()
