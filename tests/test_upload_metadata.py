"""Tests for the metadata the uploader sends with every video.

Found on 9 October by reading the Studio details page of a published Short:
"Video dili" and "Başlık ve açıklama dili" both read "Seçin". Not one of the
148 videos has either field set, because the uploader had never sent them.

This is the project's recurring failure in its mildest form -- a field nobody
wired -- so it gets a test rather than a comment. The test asserts on the
request body itself: a default argument is easy to add and just as easy to
drop again on the way into the payload.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from channel_ops import youtube_uploader


def _metadata_part(body: bytes) -> dict:
    """Pull the JSON part back out of the multipart upload body.

    Read from the wire rather than from a mocked helper, because the payload
    is assembled inline in :func:`upload_video` and a field can be dropped
    between the dict and the request without any helper noticing.
    """
    text = body.decode("latin-1")
    metadata, _ = json.JSONDecoder().raw_decode(text, text.index("{"))
    return metadata


def _sent_metadata(**kwargs) -> dict:
    """Upload a dummy file and return the metadata JSON that went out."""
    captured: dict = {}

    class _Response:
        def __enter__(self):
            return BytesIO(json.dumps(
                {"id": "vid", "status": {"privacyStatus": "public"}}
            ).encode())

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=0):
        captured["body"] = request.data
        return _Response()

    with tempfile.TemporaryDirectory() as workspace:
        clip = Path(workspace) / "short.mp4"
        clip.write_bytes(b"not really an mp4")
        with patch.object(youtube_uploader, "get_access_token", return_value="tok"), \
             patch.object(youtube_uploader, "urlopen", fake_urlopen):
            youtube_uploader.upload_video(
                clip, "title", "description", privacy="public", **kwargs
            )
    return _metadata_part(captured["body"])


class LanguageTests(unittest.TestCase):
    def test_both_language_fields_are_in_the_payload(self):
        """Studio shows two separate rows and setting one leaves the other
        reading "Seçin", so the payload has to carry both."""
        snippet = _sent_metadata()["snippet"]
        self.assertEqual(snippet["defaultLanguage"], "en")
        self.assertEqual(snippet["defaultAudioLanguage"], "en")

    def test_the_language_can_be_overridden(self):
        snippet = _sent_metadata(language="tr")["snippet"]
        self.assertEqual(snippet["defaultLanguage"], "tr")
        self.assertEqual(snippet["defaultAudioLanguage"], "tr")

    def test_the_default_is_a_plain_bcp47_code(self):
        """"en-US" would be accepted too, but the audience is global English
        and a region subtag narrows it for no gain."""
        self.assertEqual(youtube_uploader.DEFAULT_LANGUAGE, "en")

    def test_the_rest_of_the_snippet_is_untouched(self):
        """Adding fields to a payload is how an unrelated one goes missing."""
        snippet = _sent_metadata(tags=["bronze"])["snippet"]
        self.assertEqual(snippet["title"], "title")
        self.assertEqual(snippet["description"], "description")
        self.assertEqual(snippet["tags"], ["bronze"])
        self.assertEqual(snippet["categoryId"], "28")

    def test_the_payload_still_serialises(self):
        json.dumps(_sent_metadata())


if __name__ == "__main__":
    unittest.main()
