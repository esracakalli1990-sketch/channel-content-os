"""Tests for who is allowed to put work into the pipeline.

Written after a security audit on 12 October found that nothing in the
inbound Telegram path compared a message's chat against TELEGRAM_CHAT_ID.
getUpdates returns messages from every chat the bot is in, and Telegram's
privacy mode covers groups rather than direct messages, so any stranger who
found the bot could send it an MP4 and have it published to a 4,550-subscriber
channel and to Instagram. Nothing downstream would have caught it: the
private-first upload step had been removed on the reasoning that clips are
reviewed before they reach the bot.

So these tests are not about the happy path. They are about the ways the
filter could quietly stop filtering.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from channel_ops import telegram_inbox as inbox

OWNER = 123456789
STRANGER = 987654321


def _update(update_id: int, chat_id, *, video=True, text=None) -> dict:
    message: dict = {"chat": {"id": chat_id}, "from": {"username": "someone"}}
    if video:
        message["video"] = {"file_id": f"file-{update_id}", "file_size": 1024}
        message["caption"] = "1"
    if text is not None:
        message["text"] = text
    return {"update_id": update_id, "message": message}


class OwnerFilterTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict("os.environ", {"TELEGRAM_CHAT_ID": str(OWNER)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_a_strangers_video_is_not_accepted(self):
        """The finding, stated as a test. A video from any other chat must
        never reach the queue, because everything that reaches the queue is
        published publicly without a second look."""
        videos = inbox.extract_videos([_update(1, STRANGER)])
        self.assertEqual(videos, [])

    def test_the_owners_video_still_is(self):
        videos = inbox.extract_videos([_update(1, OWNER)])
        self.assertEqual(len(videos), 1)
        self.assertEqual(videos[0].file_id, "file-1")

    def test_a_strangers_video_cannot_ride_along_with_the_owners(self):
        """A batch is not all-or-nothing: the owner's clip is kept and the
        stranger's is dropped from the same getUpdates response."""
        videos = inbox.extract_videos([_update(1, OWNER), _update(2, STRANGER)])
        self.assertEqual([v.file_id for v in videos], ["file-1"])

    def test_a_strangers_command_is_not_obeyed(self):
        """Commands spend YouTube quota and Gemini credit."""
        commands = inbox.extract_commands(
            [_update(1, STRANGER, video=False, text="/rapor")]
        )
        self.assertEqual(commands, [])

    def test_the_owners_command_still_is(self):
        commands = inbox.extract_commands(
            [_update(1, OWNER, video=False, text="/rapor")]
        )
        self.assertEqual(commands, ["rapor"])

    def test_a_string_chat_id_from_the_api_still_matches(self):
        """Telegram sends the id as a JSON number, but nothing in the wire
        format guarantees it, and a type mismatch must not silently reject
        the owner's own messages."""
        update = {"update_id": 1, "message": {"chat": {"id": str(OWNER)},
                                             "video": {"file_id": "f", "file_size": 1}}}
        self.assertEqual(len(inbox.extract_videos([update])), 1)

    def test_a_message_with_no_chat_is_not_the_owner(self):
        update = {"update_id": 1, "message": {"video": {"file_id": "f", "file_size": 1}}}
        self.assertEqual(inbox.extract_videos([update]), [])

    def test_a_document_video_from_a_stranger_is_also_dropped(self):
        """"Send as file" takes a different branch in extract_videos; the
        filter has to sit in front of both or it only covers half the door."""
        update = {
            "update_id": 1,
            "message": {"chat": {"id": STRANGER},
                        "document": {"file_id": "f", "file_size": 1,
                                     "mime_type": "video/mp4"}},
        }
        self.assertEqual(inbox.extract_videos([update]), [])


class FailClosedTests(unittest.TestCase):
    """An unset owner id must stop the pipeline, not open it.

    The dangerous version of this fix is one that treats a missing
    TELEGRAM_CHAT_ID as "no filter configured, accept everything" -- that
    would restore the vulnerability in the exact deployment where the
    configuration is wrong.
    """

    def test_a_missing_owner_id_refuses_videos(self):
        with patch.dict("os.environ", {"TELEGRAM_CHAT_ID": ""}):
            with self.assertRaises(inbox.OwnerUnknownError):
                inbox.extract_videos([_update(1, OWNER)])

    def test_a_missing_owner_id_refuses_commands(self):
        with patch.dict("os.environ", {"TELEGRAM_CHAT_ID": ""}):
            with self.assertRaises(inbox.OwnerUnknownError):
                inbox.extract_commands([_update(1, OWNER, video=False, text="/rapor")])

    def test_a_non_numeric_owner_id_is_refused_with_a_usable_message(self):
        """Pasting the bot token into the wrong secret is the likely mistake."""
        with patch.dict("os.environ", {"TELEGRAM_CHAT_ID": "@unfoldableslab"}):
            with self.assertRaises(inbox.OwnerUnknownError) as caught:
                inbox.extract_videos([_update(1, OWNER)])
        self.assertIn("numeric chat id", str(caught.exception))


class AcknowledgementTests(unittest.TestCase):
    """The cursor must still move past a stranger's message.

    fetch_updates is deliberately left unfiltered so the pipeline's offset
    arithmetic still sees every update_id. Filtering there instead would mean
    a foreign message was never acknowledged, so Telegram would redeliver it
    on every run, forever.
    """

    def test_fetch_updates_returns_foreign_messages_for_the_cursor(self):
        payload = [_update(1, STRANGER), _update(2, OWNER)]
        with patch.object(inbox, "_call", return_value=payload):
            fetched = inbox.fetch_updates(0)
        self.assertEqual([u["update_id"] for u in fetched], [1, 2])

    def test_whoami_still_reports_every_chat(self):
        """describe_chats must work before an owner id exists, or setup
        depends on its own output."""
        payload = [_update(1, STRANGER, video=False, text="hi")]
        with patch.dict("os.environ", {"TELEGRAM_CHAT_ID": ""}), \
             patch.object(inbox, "_call", return_value=payload):
            chats = inbox.describe_chats()
        self.assertEqual([c["chat_id"] for c in chats], [STRANGER])


if __name__ == "__main__":
    unittest.main()
