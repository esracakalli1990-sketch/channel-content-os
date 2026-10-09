"""Tests for repairing metadata on videos that are already live.

These exist because ``videos.update`` is the most destructive call in the
project. It replaces every part it is handed, so a request meaning to add a
language field to 148 published videos will instead erase 148 titles,
descriptions and tag lists if the body is built carelessly. Most of what is
asserted below is therefore about what the request *keeps*, not what it
changes.
"""
from __future__ import annotations

import unittest

from channel_ops import video_metadata as vm

LANG = "en"

CURRENT = {
    "snippet": {
        "title": "Why does this curved bronze shell have a walnut base?",
        "description": "A precision mechanical object…",
        "tags": ["asmr", "automata", "bronze"],
        "categoryId": "28",
        "channelId": "UCV2EPdqjtZoFnaKIV-2_JqQ",   # read-only, must not be echoed
        "publishedAt": "2026-09-25T10:00:00Z",      # read-only
    },
    "status": {
        "privacyStatus": "public",
        "license": "youtube",
        "embeddable": True,
        "publicStatsViewable": True,
        "madeForKids": False,
        "uploadStatus": "processed",                # read-only
    },
}


def _plan(current=None, *, declare_ai=True):
    return vm.planned_update("vid", current or CURRENT,
                             language=LANG, declare_ai=declare_ai)


class PreservationTests(unittest.TestCase):
    def test_the_title_description_and_tags_survive(self):
        """The whole reason this module has tests."""
        snippet = _plan()["snippet"]
        self.assertEqual(snippet["title"], CURRENT["snippet"]["title"])
        self.assertEqual(snippet["description"], CURRENT["snippet"]["description"])
        self.assertEqual(snippet["tags"], ["asmr", "automata", "bronze"])
        self.assertEqual(snippet["categoryId"], "28")

    def test_privacy_is_carried_over_rather_than_defaulted(self):
        """Omitting privacyStatus from an update clears it, and a cleared
        privacyStatus on 148 public videos is the worst outcome available."""
        self.assertEqual(_plan()["status"]["privacyStatus"], "public")

    def test_an_unlisted_video_does_not_become_public(self):
        current = {**CURRENT, "status": {**CURRENT["status"], "privacyStatus": "unlisted"}}
        self.assertEqual(_plan(current)["status"]["privacyStatus"], "unlisted")

    def test_made_for_kids_is_translated_to_its_writable_twin(self):
        """videos.list returns madeForKids; videos.update only accepts
        selfDeclaredMadeForKids. Sending the read one back fails the request."""
        status = _plan()["status"]
        self.assertFalse(status["selfDeclaredMadeForKids"])
        self.assertNotIn("madeForKids", status)

    def test_read_only_fields_are_not_echoed_back(self):
        plan = _plan()
        for field in ("channelId", "publishedAt"):
            self.assertNotIn(field, plan["snippet"])
        self.assertNotIn("uploadStatus", plan["status"])

    def test_a_video_with_no_tags_sends_an_empty_list_not_nothing(self):
        current = {**CURRENT, "snippet": {k: v for k, v in CURRENT["snippet"].items()
                                          if k != "tags"}}
        self.assertEqual(_plan(current)["snippet"]["tags"], [])


class RepairTests(unittest.TestCase):
    def test_both_language_fields_are_set(self):
        snippet = _plan()["snippet"]
        self.assertEqual(snippet["defaultLanguage"], LANG)
        self.assertEqual(snippet["defaultAudioLanguage"], LANG)

    def test_the_ai_disclosure_is_set(self):
        self.assertTrue(_plan()["status"]["containsSyntheticMedia"])

    def test_declining_the_disclosure_preserves_an_existing_one(self):
        """--no-ai means "do not add it", not "take it off the ones that
        already carry it" -- YouTube locks those anyway."""
        current = {**CURRENT,
                   "status": {**CURRENT["status"], "containsSyntheticMedia": True}}
        self.assertTrue(_plan(current, declare_ai=False)["status"]["containsSyntheticMedia"])

    def test_declining_the_disclosure_does_not_invent_one(self):
        self.assertNotIn("containsSyntheticMedia", _plan(declare_ai=False)["status"])


class NeedsWorkTests(unittest.TestCase):
    def test_a_fully_repaired_video_needs_nothing(self):
        done = {
            "snippet": {**CURRENT["snippet"],
                        "defaultLanguage": LANG, "defaultAudioLanguage": LANG},
            "status": {**CURRENT["status"], "containsSyntheticMedia": True},
        }
        self.assertEqual(vm.needs_work(done, language=LANG, declare_ai=True), [])

    def test_a_video_already_labelled_by_youtube_needs_only_the_language(self):
        """The ones YouTube auto-labelled must not be re-sent for the label;
        re-declaring a locked label is a rejected request for no gain."""
        labelled = {"snippet": CURRENT["snippet"],
                    "status": {**CURRENT["status"], "containsSyntheticMedia": True}}
        gaps = vm.needs_work(labelled, language=LANG, declare_ai=True)
        self.assertEqual(gaps, ["defaultLanguage", "defaultAudioLanguage"])

    def test_a_virgin_video_needs_all_three(self):
        self.assertEqual(
            vm.needs_work(CURRENT, language=LANG, declare_ai=True),
            ["defaultLanguage", "defaultAudioLanguage", "containsSyntheticMedia"],
        )

    def test_a_different_language_counts_as_missing(self):
        current = {"snippet": {**CURRENT["snippet"],
                               "defaultLanguage": "tr", "defaultAudioLanguage": "tr"},
                   "status": CURRENT["status"]}
        gaps = vm.needs_work(current, language="en", declare_ai=False)
        self.assertEqual(gaps, ["defaultLanguage", "defaultAudioLanguage"])


if __name__ == "__main__":
    unittest.main()
