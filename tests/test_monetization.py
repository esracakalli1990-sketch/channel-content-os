"""Tests for the monetisation probe.

The probe asks the Analytics API for revenue and reads the answer as a yes/no
on whether the channel is in the Partner Programme, because no API field
states that directly.

The failure worth guarding against is subtle: a 403 for a missing OAuth scope
and a 403 for an unmonetised channel look alike from a distance. If the first
is read as the second, the probe reports "not yet" every week forever — a
check that can never turn true, which is worse than no check, and exactly the
kind of thing this project keeps building by accident.
"""
from __future__ import annotations

import json
import unittest
from datetime import date
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from channel_ops import monetization


def _ok(rows):
    class _Ctx:
        def __enter__(self):
            return BytesIO(json.dumps({"rows": rows}).encode())

        def __exit__(self, *exc):
            return False

    return _Ctx()


def _http(code, message):
    body = json.dumps({"error": {"message": message}}).encode()
    return HTTPError("url", code, "err", {}, BytesIO(body))


def _probe(side_effect=None, return_value=None):
    with patch.object(monetization, "get_access_token", return_value="tok"), \
         patch.object(monetization, "_channel_selector", return_value="channel==MINE"), \
         patch.object(monetization, "urlopen",
                      side_effect=side_effect, return_value=return_value):
        return monetization.probe(today=date(2026, 10, 13))


class EarningTests(unittest.TestCase):
    def test_served_revenue_means_monetised(self):
        result = _probe(return_value=_ok([[12.34]]))
        self.assertEqual(result["state"], "earning")
        self.assertAlmostEqual(result["revenue"], 12.34)

    def test_a_served_zero_still_means_monetised(self):
        """The signal is that the question was answered at all. A channel can
        be in the programme and have earned nothing yet, and reading that as
        'not monetised' would miss the day it switches on."""
        result = _probe(return_value=_ok([[0.0]]))
        self.assertEqual(result["state"], "earning")

    def test_an_empty_row_set_still_means_monetised(self):
        result = _probe(return_value=_ok([]))
        self.assertEqual(result["state"], "earning")
        self.assertEqual(result["revenue"], 0.0)


class RefusalTests(unittest.TestCase):
    """The 403 YouTube actually sends, observed live on 13 October, is
    "Insufficient permission to access this report." — and it is the SAME
    sentence whether the token may not ask or the channel has nothing to
    report. So the message is not read at all; the token's scopes are looked
    up instead, which decides one half with certainty."""

    REAL = "Insufficient permission to access this report."

    def test_the_real_message_without_the_scope_is_a_broken_probe(self):
        """If this were misread as 'not yet', the probe would report that
        forever and nobody would learn it could never have succeeded."""
        with patch.object(monetization, "_granted_scopes",
                          return_value={"https://www.googleapis.com/auth/yt-analytics.readonly"}):
            result = _probe(side_effect=_http(403, self.REAL))
        self.assertEqual(result["state"], "scope_missing")
        self.assertIn("yt-analytics-monetary.readonly", result["detail"])

    def test_the_same_message_with_the_scope_means_not_monetised_yet(self):
        """Identical wording, opposite conclusion — which is exactly why the
        string is not what decides."""
        with patch.object(monetization, "_granted_scopes",
                          return_value={monetization.MONETARY_SCOPE}):
            result = _probe(side_effect=_http(403, self.REAL))
        self.assertEqual(result["state"], "not_yet")

    def test_an_unreadable_scope_list_is_admitted_not_guessed(self):
        with patch.object(monetization, "_granted_scopes", return_value=None):
            result = _probe(side_effect=_http(403, self.REAL))
        self.assertEqual(result["state"], "error")
        self.assertIn("ayırt edilemiyor", result["detail"])

    def test_an_unrelated_failure_is_neither(self):
        result = _probe(side_effect=_http(500, "Backend error"))
        self.assertEqual(result["state"], "error")

    def test_a_network_failure_is_reported_not_swallowed(self):
        result = _probe(side_effect=URLError("dns"))
        self.assertEqual(result["state"], "error")

    def test_youtubes_own_wording_is_kept(self):
        """Whatever YouTube says is carried into the report verbatim, so a
        change in its wording shows up instead of being swallowed."""
        with patch.object(monetization, "_granted_scopes", return_value=set()):
            result = _probe(side_effect=_http(403, "Totally unexpected wording"))
        self.assertIn("Totally unexpected wording", result["detail"])


class SummaryTests(unittest.TestCase):
    def test_earning_is_unmistakable(self):
        line = monetization.summary({"state": "earning", "revenue": 1234.5})
        self.assertIn("PARA KAZANMA AKTİF", line)
        self.assertIn("1.234,50", line)

    def test_not_yet_is_quiet(self):
        line = monetization.summary({"state": "not_yet", "detail": "x"})
        self.assertNotIn("AKTİF", line)

    def test_a_broken_probe_says_so(self):
        line = monetization.summary({"state": "scope_missing", "detail": "x"})
        self.assertIn("çalışamıyor", line)


if __name__ == "__main__":
    unittest.main()
