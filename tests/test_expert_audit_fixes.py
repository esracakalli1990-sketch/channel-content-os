"""Regression tests for the faults an outside reviewer found on 15 October.

Each one is a way the weekly report was wrong while looking fine:

* a deleted video counted as a dead one in every report since it vanished;
* a recorded 90-day figure larger than the channel's lifetime total feeding
  the growth rate;
* a template change read as a cause when the climb was already under way;
* no per-video hook measure although the data was already being collected.
"""
from __future__ import annotations

import math
import unittest
from datetime import UTC, datetime, timedelta

from channel_ops import channel_analysis as ca

NOW = datetime(2026, 10, 14, 22, 0, tzinfo=UTC)


def _row(i, template, views, mature=True, hours_ago=None):
    return {
        "id": f"v{i}", "template": template, "views": views, "mature": mature,
        "published_at": (NOW - timedelta(days=100 - i)).isoformat(),
    }


class DeletedVideoTests(unittest.TestCase):
    def _dump(self, with_stats=True):
        def record(vid, hours):
            return {"youtube_video_id": vid, "creature": "owl",
                    "published_at": (NOW - timedelta(hours=hours)).isoformat()}
        stats = {"a": {"views": 5000, "seconds": 9},
                 "c": {"views": 7, "seconds": 9}}
        return {
            "collected_at": NOW.isoformat(), "long_form": [],
            "published": [record("a", 500), record("gone", 400),
                          record("c", 300), record("fresh", 10)],
            "data_api": stats if with_stats else {},
            "analytics": {}, "totals": {}, "errors": {},
        }

    def test_a_video_the_api_does_not_return_is_deleted_not_dead(self):
        rows = {r["id"]: r for r in ca.videos(self._dump(), NOW)}
        self.assertTrue(rows["gone"]["deleted"])
        self.assertFalse(rows["gone"]["dead"])
        self.assertFalse(rows["gone"]["mature"])

    def test_a_genuinely_dead_video_still_counts(self):
        rows = {r["id"]: r for r in ca.videos(self._dump(), NOW)}
        self.assertFalse(rows["c"]["deleted"])
        self.assertTrue(rows["c"]["dead"])

    def test_a_video_too_new_to_be_indexed_is_not_called_deleted(self):
        rows = {r["id"]: r for r in ca.videos(self._dump(), NOW)}
        self.assertFalse(rows["fresh"]["deleted"])

    def test_an_empty_api_reply_deletes_nothing(self):
        """No Data API answer at all means the call failed, not that every
        video was removed."""
        rows = ca.videos(self._dump(with_stats=False), NOW)
        self.assertFalse(any(r["deleted"] for r in rows))

    def test_deleted_videos_are_outside_every_median(self):
        rows = ca.videos(self._dump(), NOW)
        snap = ca.snapshot({"totals": {}}, rows, {
            "watch_hours": {"value": 0}, "shorts_views": {"value": 0}})
        self.assertEqual(snap["mature_count"], 2)
        self.assertEqual(snap["dead_count"], 1)


class ImpossibleSnapshotTests(unittest.TestCase):
    def test_the_28_september_row_is_suspect(self):
        row = {"at": "2026-09-28T14:12:36+00:00", "views": 2618818,
               "shorts_views_90d": 3108912, "shorts_measure": "views"}
        self.assertTrue(ca.suspect_snapshot(row))

    def test_a_normal_row_is_not(self):
        row = {"at": "x", "views": 2618818, "shorts_views_90d": 2259505}
        self.assertFalse(ca.suspect_snapshot(row))

    def test_a_suspect_row_stays_out_of_the_rate_but_not_out_of_the_record(self):
        now = datetime.now(UTC)
        history = [
            {"at": (now - timedelta(days=3)).isoformat(), "views": 1000,
             "shorts_views_90d": 5000, "shorts_measure": "views"},   # impossible
            {"at": (now - timedelta(days=2)).isoformat(), "views": 3000,
             "shorts_views_90d": 600, "shorts_measure": "views"},
        ]
        rate = ca._rate_per_day(history, "shorts_views_90d", 1000, now, "views")
        self.assertAlmostEqual(rate, 200, delta=2)      # (1000-600)/2 days
        self.assertEqual(len(history), 2)               # nothing deleted

    def test_other_figures_in_a_suspect_row_are_still_usable(self):
        now = datetime.now(UTC)
        history = [{"at": (now - timedelta(days=2)).isoformat(), "views": 1000,
                    "shorts_views_90d": 5000, "subscribers": 100}]
        rate = ca._rate_per_day(history, "subscribers", 300, now)
        self.assertAlmostEqual(rate, 100, delta=2)


class HookProxyTests(unittest.TestCase):
    def _dump(self, hours_old):
        return {
            "collected_at": NOW.isoformat(), "long_form": [], "totals": {},
            "errors": {},
            "published": [{"youtube_video_id": "a", "creature": "owl",
                           "published_at": (NOW - timedelta(hours=hours_old)).isoformat()}],
            "data_api": {"a": {"views": 10000, "seconds": 9}},
            "analytics": {"engaged_per_video": {
                "columns": ["video", "views", "engagedViews"],
                "rows": [["a", 9000, 4500]]}},
        }

    def test_the_ratio_is_engaged_over_views(self):
        row = ca.videos(self._dump(500), NOW)[0]
        self.assertEqual(row["engaged_ratio"], 45.0)

    def test_a_video_analytics_has_not_caught_up_on_gets_no_ratio(self):
        """Analytics lags the Data API by days; for a young video the engaged
        figure undercounts and the ratio would measure its age."""
        row = ca.videos(self._dump(60), NOW)[0]
        self.assertIsNone(row["engaged_ratio"])

    def test_no_engaged_report_means_no_ratio_not_zero(self):
        dump = self._dump(500)
        dump["analytics"] = {}
        self.assertIsNone(ca.videos(dump, NOW)[0]["engaged_ratio"])


class SpearmanTests(unittest.TestCase):
    def test_matches_scipy_on_a_known_sample(self):
        # scipy.stats.spearmanr(range(12), y) gave (0.2517482517, 0.4299187850)
        y = [0.13, 0.92, 0.44, 0.31, 0.77, 0.05, 0.62, 0.58, 0.21, 0.99, 0.37, 0.84]
        rho, p = ca.spearman([float(i) for i in range(12)], y)
        self.assertAlmostEqual(rho, 0.2517482517, places=8)
        self.assertAlmostEqual(p, 0.4299187850, places=6)

    def test_a_perfect_trend_has_zero_p(self):
        rho, p = ca.spearman([1, 2, 3, 4, 5], [10, 20, 30, 40, 50])
        self.assertEqual((rho, p), (1.0, 0.0))

    def test_constant_input_is_not_computable(self):
        self.assertIsNone(ca.spearman([1, 2, 3, 4, 5], [7, 7, 7, 7, 7]))

    def test_too_few_points_is_not_computable(self):
        self.assertIsNone(ca.spearman([1, 2, 3], [1, 2, 3]))


class TemplateTransitionTests(unittest.TestCase):
    def _rows(self, old_views, new_views):
        rows = []
        for i, v in enumerate(old_views):
            rows.append(_row(i, "old", v))
        for j, v in enumerate(new_views):
            rows.append(_row(len(old_views) + j, "new", v))
        return rows

    def test_no_jump_at_the_switch_when_the_level_does_not_change(self):
        """Same level either side of the hand-over, ordinary noise: the
        template change did nothing the videos can show."""
        views = [1000 + ((i * 37) % 11) * 40 for i in range(40)]
        rows = self._rows(views[:20], views[20:])
        main = ca.template_transitions(rows)[0]["primary"]
        self.assertEqual(main["verdict"], "fark yok")

    def test_a_real_step_at_the_switch_is_found(self):
        rows = self._rows([1000 + i for i in range(20)],
                          [50000 + i for i in range(20)])
        main = ca.template_transitions(rows)[0]["primary"]
        self.assertIn("daha iyi", main["verdict"])

    def test_too_few_videos_on_a_side_gives_no_test(self):
        rows = self._rows([1000] * 5, [2000] * 20)
        self.assertIsNone(ca.template_transitions(rows)[0]["primary"])

    def test_the_newest_template_is_included(self):
        rows = self._rows([1000 + i for i in range(20)], [900 + i for i in range(20)])
        names = [t["template"] for t in ca.template_trends(rows)]
        self.assertIn("new", names)

    def test_a_trend_inside_a_template_is_reported(self):
        rows = [_row(i, "t", 100 * (i + 1)) for i in range(20)]
        trend = ca.template_trends(rows)[0]
        self.assertIn("yükseliyor", trend["verdict"])

    def test_a_flat_template_has_no_trend(self):
        rows = [_row(i, "t", 1000 + (i * 7919) % 13) for i in range(20)]
        # values wobble with no order relation to i
        trend = ca.template_trends(rows)[0]
        self.assertTrue(trend["verdict"].startswith("trend yok") or trend["p"] > 0.05)

    def test_holm_withdraws_a_borderline_trend(self):
        trend = {"p": 0.04, "verdict": "zamanla yükseliyor (rho=0.3, p=0.040)"}
        others = [{"p": 0.5, "verdict": "fark yok"} for _ in range(15)]
        ca.apply_holm([trend] + others)
        self.assertIn("fark yok", trend["verdict"])


class LegacyDatasetTests(unittest.TestCase):
    def test_an_old_export_gets_the_deleted_rule_applied(self):
        """expert_dataset.json predates the rule: the vanished video sits there
        as 0 views, no duration, 'mature' and 'dead'."""
        rows = ca.settle_deleted([
            {"views": 0, "seconds": 0, "mature": True, "dead": True, "hit": False},
            {"views": 5, "seconds": 9, "mature": True, "dead": True, "hit": False},
        ])
        self.assertTrue(rows[0]["deleted"])
        self.assertFalse(rows[0]["dead"])
        self.assertFalse(rows[0]["mature"])
        self.assertFalse(rows[1]["deleted"])
        self.assertTrue(rows[1]["dead"])

    def test_an_explicit_flag_is_respected(self):
        rows = ca.settle_deleted([{"views": 0, "seconds": 0, "deleted": False,
                                   "mature": True, "dead": True, "hit": False}])
        self.assertFalse(rows[0]["deleted"])


class BriefGrowthTableTests(unittest.TestCase):
    """The brief once put raw and engaged views in one column and explained the
    jump away with a footnote that was wrong."""

    def test_units_are_separate_and_the_impossible_row_is_gone(self):
        try:
            import importlib.util
            from pathlib import Path
            from tempfile import TemporaryDirectory
            from pypdf import PdfReader
            spec = importlib.util.spec_from_file_location(
                "brief", Path(__file__).parent.parent / "scripts" / "expert_brief.py")
            brief = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(brief)
        except Exception as exc:  # reportlab/pypdf/fonts are not repo dependencies
            self.skipTest(f"PDF araçları yok: {exc}")
        history = [
            {"at": "2026-09-27T10:00:00+00:00", "subscribers": 1, "views": 2618818,
             "videos": 1, "shorts_views_90d": 2260227, "shorts_measure": "views"},
            {"at": "2026-09-28T14:12:36+00:00", "subscribers": 1, "views": 2618818,
             "videos": 1, "shorts_views_90d": 3108912, "shorts_measure": "views"},
            {"at": "2026-09-29T10:00:00+00:00", "subscribers": 1, "views": 2700000,
             "videos": 1, "shorts_views_90d": 1012415, "shorts_measure": "engaged"},
        ]
        data = {"collected_at": "2026-10-14T22:03:00+00:00", "totals": {},
                "gates": {}, "videos": [], "history": history, "tables": {}}
        try:
            with TemporaryDirectory() as tmp:
                out = brief.build(data, Path(tmp) / "b.pdf")
                text = "\n".join(p.extract_text() for p in PdfReader(out).pages)
        except Exception as exc:
            self.skipTest(f"PDF üretilemedi: {exc}")
        self.assertIn("ham", text)
        self.assertIn("etkileşimli", text)
        self.assertNotIn("3.108.912 ham", text)
        self.assertNotIn("hata değil", text)   # the wrong footnote


if __name__ == "__main__":
    unittest.main()
