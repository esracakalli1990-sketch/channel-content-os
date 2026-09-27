"""Tests for keeping the Instagram token alive.

The failure these exist for: a token reached its sixtieth day and died in
silence, and the warning written to prevent exactly that was never wired to
anything. So the tests here care less about happy paths than about the ways
this can quietly stop working again.
"""
from __future__ import annotations

import json
import unittest
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError

from channel_ops import github_secrets, instagram_uploader


def _response(payload: dict):
    """A urlopen context manager returning *payload* as JSON."""

    class _Ctx:
        def __enter__(self):
            return BytesIO(json.dumps(payload).encode())

        def __exit__(self, *exc):
            return False

    return _Ctx()


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(
            "os.environ", {"IG_USER_ID": "123", "IG_ACCESS_TOKEN": "old-token"}
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_the_new_token_is_returned_not_discarded(self):
        """The whole point. The endpoint replaces the token rather than
        extending it, so a refresh whose result is thrown away buys nothing --
        which is what the previous version did on purpose."""
        with patch.object(instagram_uploader, "urlopen",
                          return_value=_response({"access_token": "new-token",
                                                  "expires_in": 60 * 86400})):
            token, days = instagram_uploader.refresh_token()
        self.assertEqual(token, "new-token")
        self.assertEqual(days, 60)

    def test_days_remaining_still_works_on_top_of_it(self):
        with patch.object(instagram_uploader, "urlopen",
                          return_value=_response({"access_token": "t",
                                                  "expires_in": 3 * 86400})):
            self.assertEqual(instagram_uploader.token_days_remaining(), 3)

    def test_a_reply_with_no_token_is_an_error_not_an_empty_secret(self):
        """Writing "" into the secret would break publishing far more
        thoroughly than leaving the old token in place."""
        with patch.object(instagram_uploader, "urlopen",
                          return_value=_response({"expires_in": 60 * 86400})):
            with self.assertRaises(instagram_uploader.InstagramError):
                instagram_uploader.refresh_token()

    def test_an_expired_token_surfaces_instagram_s_own_message(self):
        error = HTTPError(
            "url", 400, "Bad Request", {},
            BytesIO(json.dumps({"error": {"message": "Session has expired"}}).encode()),
        )
        with patch.object(instagram_uploader, "urlopen", side_effect=error):
            with self.assertRaises(instagram_uploader.InstagramError) as caught:
                instagram_uploader.refresh_token()
        self.assertIn("expired", str(caught.exception).lower())


class SecretWriteTests(unittest.TestCase):
    def test_a_missing_personal_token_says_why_it_is_needed(self):
        """Actions' own GITHUB_TOKEN cannot write secrets and no permission
        setting grants it that, so the message has to say so or the next
        person will spend an hour adding permissions that do nothing."""
        with patch.dict("os.environ", {"GH_SECRET_TOKEN": ""}):
            with self.assertRaises(github_secrets.SecretWriteError) as caught:
                github_secrets.update_secret("o/r", "NAME", "value")
        self.assertIn("GITHUB_TOKEN cannot write", str(caught.exception))

    def test_a_malformed_repository_is_rejected_before_any_call(self):
        with patch.dict("os.environ", {"GH_SECRET_TOKEN": "pat"}):
            with self.assertRaises(github_secrets.SecretWriteError) as caught:
                github_secrets.update_secret("just-a-name", "NAME", "value")
        self.assertIn("owner/repo", str(caught.exception))

    def test_the_value_is_sealed_and_never_sent_in_the_clear(self):
        sent = {}

        def fake_call(path, *, method="GET", body=None):
            if method == "GET":
                return {"key_id": "kid", "key": "u" * 44}
            sent["path"], sent["body"] = path, body
            return {}

        with patch.dict("os.environ", {"GH_SECRET_TOKEN": "pat"}), \
             patch.object(github_secrets, "_call", side_effect=fake_call), \
             patch.object(github_secrets, "_seal", return_value="SEALED"):
            github_secrets.update_secret("o/r", "IG_ACCESS_TOKEN", "super-secret")

        self.assertEqual(sent["body"]["encrypted_value"], "SEALED")
        self.assertNotIn("super-secret", json.dumps(sent["body"]))

    def test_a_forbidden_reply_points_at_the_token_scopes(self):
        error = HTTPError("url", 403, "Forbidden", {},
                          BytesIO(json.dumps({"message": "Resource not accessible"}).encode()))
        with patch.dict("os.environ", {"GH_SECRET_TOKEN": "pat"}), \
             patch.object(github_secrets, "urlopen", side_effect=error):
            with self.assertRaises(github_secrets.SecretWriteError) as caught:
                github_secrets.update_secret("o/r", "NAME", "value")
        self.assertIn("permission to write repository secrets",
                      str(caught.exception))


if __name__ == "__main__":
    unittest.main()
