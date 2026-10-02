"""Tests for the two ways a comparison can look real without being real.

Both were raised by the analysis session on 2 October and both had already
shown up in live reports:

* Sixteen rank tests at p<0.05 produce a false positive about every other
  report. One did -- "hitler daha yüksek tutundurur" passed at p=0.049 on
  28 September and failed the week after.
* Template and instruction groups occupy separate stretches of the calendar.
  The channel went from six thousand views a day to four hundred thousand in
  three weeks, so the later group wins whatever it contains.
"""
from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from channel_ops import channel_analysis as ca

NOW = datetime(2026, 10, 2, 14, 0, tzinfo=UTC)


def _result(p, verdict="a daha iyi (p=?)"):
    return {"p": p, "verdict": verdict, "a": "a", "b": "b"}


class HolmTests(unittest.TestCase):
    def test_a_borderline_result_among_many_is_withdrawn(self):
        """p=0.049 is a finding when it is the only question asked and noise
        when it is the sixteenth."""
        results = [_result(0.049)] + [_result(0.6) for _ in range(15)]
        ca.apply_holm(results)
        self.assertIn("fark yok", results[0]["verdict"])
        self.assertIn("ham p=0.049", results[0]["verdict"])

    def test_a_strong_result_survives(self):
        results = [_result(0.0001)] + [_result(0.6) for _ in range(15)]
        ca.apply_holm(results)
        self.assertIn("daha iyi", results[0]["verdict"])
        self.assertLess(results[0]["p_adjusted"], ca.SIGNIFICANCE)

    def test_the_raw_p_value_is_kept(self):
        """The adjusted value says whether to believe it; the raw one says how
        close the call was. Dropping either hides something."""
        results = [_result(0.01), _result(0.02)]
        ca.apply_holm(results)
        self.assertEqual(results[0]["p"], 0.01)
        self.assertIn("p_adjusted", results[0])

    def test_holm_is_less_blunt_than_bonferroni(self):
        """Plain Bonferroni multiplies every p by the full count. Holm only
        does that for the smallest, which keeps power where it matters."""
        results = [_result(0.01), _result(0.02), _result(0.03), _result(0.04)]
        ca.apply_holm(results)
        by_p = sorted(results, key=lambda r: r["p"])
        self.assertAlmostEqual(by_p[0]["p_adjusted"], 0.04)   # 0.01 x 4
        self.assertAlmostEqual(by_p[-1]["p_adjusted"], 0.04)  # 0.04 x 1

    def test_untestable_rows_are_left_alone(self):
        """Groups too small to test carry no p and must not be counted into
        the family size -- that would tighten the threshold for nothing."""
        results = [_result(0.01), {"p": None, "verdict": "yeterli veri yok"}]
        ca.apply_holm(results)
        self.assertEqual(results[0]["tests_in_family"], 1)
        self.assertNotIn("p_adjusted", results[1])


class TimeSplitTests(unittest.TestCase):
    def _rows(self, spans):
        rows = []
        for label, start, count in spans:
            for day in range(count):
                rows.append({
                    "template": label,
                    "published_at": (NOW - timedelta(days=start - day)).isoformat(),
                })
        return rows

    def test_groups_in_separate_stretches_are_flagged(self):
        rows = self._rows([("eski", 40, 10), ("yeni", 10, 10)])
        self.assertTrue(ca._is_time_split(rows, "template", "eski", "yeni"))

    def test_groups_that_ran_side_by_side_are_not(self):
        rows = self._rows([("a", 30, 20), ("b", 25, 20)])
        self.assertFalse(ca._is_time_split(rows, "template", "a", "b"))

    def test_a_missing_group_is_not_flagged(self):
        rows = self._rows([("a", 30, 5)])
        self.assertFalse(ca._is_time_split(rows, "template", "a", "yok"))

    def test_the_warning_reaches_the_comparison(self):
        """A template is replaced rather than run alongside its predecessor,
        so this is the normal case for that dimension, not an edge case."""
        rows = []
        for label, start in (("eski", 40), ("yeni", 10)):
            for day in range(10):
                rows.append({
                    "template": label, "idea": "x", "hour": "17", "kind": "canli",
                    "metal": "bronze", "mature": True, "views": 1000 + day,
                    "retention": 120.0, "subs_per_1k": 0.4, "hit": False,
                    "published_at": (NOW - timedelta(days=start - day)).isoformat(),
                })
        results = ca.cohorts(rows, "views")
        template = next(r for r in results if r["dimension"] == "Şablon")
        self.assertIn("zamanda ayrık", template["confounded"])


if __name__ == "__main__":
    unittest.main()


class WeekComparisonTests(unittest.TestCase):
    """"Bu hafta ne değişti" compared against the previous snapshot.

    Two runs on the same afternoon reported thirteen hours of change under a
    weekly heading: on 2 October it read +90 subscribers for a week in which
    the real figure was nearly a thousand."""

    def setUp(self):
        import importlib.util
        from pathlib import Path
        spec = importlib.util.spec_from_file_location(
            "rep", Path(__file__).parent.parent / "scripts" / "channel_report.py")
        self.rep = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.rep)

    def _history(self, ages_and_values):
        now = datetime.now(UTC)
        return [
            {"at": (now - timedelta(days=days)).isoformat(), "subscribers": value}
            for days, value in ages_and_values
        ]

    def test_a_week_old_row_is_preferred_over_the_newest(self):
        history = self._history([(7.0, 1720), (0.5, 2610)])
        text = self.rep._delta(history, "subscribers", 2700)
        self.assertIn("980", text)   # 2700 - 1720, haftalik
        self.assertNotIn("90", text)  # 2700 - 2610, 13 saatlik

    def test_the_closest_to_a_week_wins_not_the_oldest(self):
        history = self._history([(30.0, 300), (7.0, 1720), (0.5, 2610)])
        self.assertIn("980", self.rep._delta(history, "subscribers", 2700))

    def test_a_short_record_names_its_own_window(self):
        """Better to say the window is two days than to call it a week."""
        history = self._history([(2.0, 2610)])
        text = self.rep._delta(history, "subscribers", 2700)
        self.assertIn("2.0 gün", text)

    def test_a_week_old_row_needs_no_disclaimer(self):
        history = self._history([(7.0, 1720)])
        self.assertNotIn("gün", self.rep._delta(history, "subscribers", 2700))

    def test_an_empty_record_says_nothing(self):
        self.assertEqual(self.rep._delta([], "subscribers", 2700), "")
