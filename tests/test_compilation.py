"""Long-form compilations cut from the back catalogue.

The channel is Shorts-only and Shorts watch time does not count toward the
4,000 hours the Partner Programme asks for, so the compilations are the only
route to that threshold that does not require a tenfold jump in Shorts views.
"""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from channel_ops import shorts_compilation as sc


def _clip(name, views=1000, seconds=10.0):
    return sc.Clip(creature=name, file_id=f"id-{name}", file_size=3_000_000,
                   views=views, seconds=seconds)


class ChapterTests(unittest.TestCase):
    """YouTube ignores a chapter shorter than ten seconds, and the clips run
    nine to eleven — so some chapters have to cover two clips."""

    def test_the_first_chapter_starts_at_zero(self):
        marks = sc.chapters([_clip("a"), _clip("b"), _clip("c")])
        self.assertEqual(marks[0][0], 0)

    def test_no_chapter_is_shorter_than_youtube_will_render(self):
        clips = [_clip(f"c{i}", seconds=9.0) for i in range(8)]
        marks = sc.chapters(clips)
        starts = [at for at, _ in marks]
        gaps = [b - a for a, b in zip(starts, starts[1:])]
        self.assertTrue(all(g >= sc.MIN_CHAPTER_SECONDS for g in gaps), gaps)

    def test_a_merged_chapter_names_both_creatures(self):
        """A chapter covering two clips but announcing one sends the viewer to
        the wrong place."""
        marks = sc.chapters([_clip("lined chiton", seconds=9.0),
                             _clip("box jellyfish", seconds=9.0),
                             _clip("venus flytrap", seconds=11.0)])
        self.assertIn("Lined Chiton & Box Jellyfish", marks[0][1])

    def test_every_creature_appears_somewhere(self):
        clips = [_clip(f"c{i}", seconds=9.0) for i in range(10)]
        text = " ".join(label for _, label in sc.chapters(clips))
        for clip in clips:
            self.assertIn(clip.creature.title(), text)


class OrderingTests(unittest.TestCase):
    """The opening decides whether the rest is watched, and a video that decays
    from best to worst loses people on the way down."""

    def test_the_best_clips_open(self):
        clips = [_clip(f"c{i}", views=1000 - i) for i in range(12)]
        ordered = sc._interleave(clips)
        self.assertEqual([c.creature for c in ordered[:sc.OPENERS]],
                         ["c0", "c1", "c2"])

    def test_no_clip_is_lost_or_duplicated(self):
        clips = [_clip(f"c{i}", views=1000 - i) for i in range(17)]
        ordered = sc._interleave(clips)
        self.assertEqual(len(ordered), 17)
        self.assertEqual({c.creature for c in ordered}, {c.creature for c in clips})

    def test_a_short_list_is_left_alone(self):
        clips = [_clip("a"), _clip("b")]
        self.assertEqual(sc._interleave(clips), clips)


class DescriptionTests(unittest.TestCase):
    def test_it_stays_inside_youtube_limits(self):
        clips = [_clip(f"creature number {i}", seconds=11.0) for i in range(80)]
        title, description = sc.describe(clips)
        self.assertLessEqual(len(title), 100)
        self.assertLessEqual(len(description), 5000)

    def test_the_chapters_are_in_the_description(self):
        _, description = sc.describe([_clip("lined chiton", seconds=11.0),
                                      _clip("walking winch", seconds=11.0)])
        self.assertIn("00:00 Lined Chiton", description)
        self.assertIn("00:11 Walking Winch", description)


class FreshnessTests(unittest.TestCase):
    """Picking purely by view count would make every compilation a near-copy of
    the last one, which is duplicate content."""

    def setUp(self):
        self._workspace = TemporaryDirectory()
        self.root = Path(self._workspace.name)
        (self.root / "data").mkdir()
        self.addCleanup(self._workspace.cleanup)

        published = [
            {"creature": f"c{i}", "youtube_video_id": f"v{i}", "file_id": f"id-c{i}",
             "file_size": 3_000_000}
            for i in range(10)
        ]
        (self.root / sc.PUBLISHED_FILE).write_text(json.dumps(published), encoding="utf-8")

        original = sc.fetch_views
        sc.fetch_views = lambda ids: {vid: 1000 - index for index, vid in enumerate(ids)}
        self.addCleanup(setattr, sc, "fetch_views", original)

    def _mark_used(self, creatures):
        (self.root / sc.LONG_PUBLISHED_FILE).write_text(
            json.dumps([{"creatures": creatures}]), encoding="utf-8")

    def test_used_clips_are_held_back(self):
        self._mark_used(["c0", "c1", "c2"])
        chosen = sc.choose_clips(self.root, minutes=0.5)  # 3 clips wanted
        self.assertFalse({c.creature for c in chosen} & {"c0", "c1", "c2"})

    def test_it_refuses_rather_than_shipping_a_re_run(self):
        self._mark_used([f"c{i}" for i in range(9)])
        with self.assertRaises(sc.CompilationError) as caught:
            sc.choose_clips(self.root, minutes=1)  # 6 clips wanted, 1 fresh
        self.assertIn("unused", str(caught.exception))

    def test_reuse_tops_the_list_up_when_asked(self):
        self._mark_used([f"c{i}" for i in range(9)])
        chosen = sc.choose_clips(self.root, minutes=1, reuse=True)
        self.assertEqual(len(chosen), 6)

    def test_a_clip_with_no_recoverable_id_is_skipped(self):
        published = json.loads((self.root / sc.PUBLISHED_FILE).read_text())
        published.append({"creature": "ghost", "youtube_video_id": "vX"})
        (self.root / sc.PUBLISHED_FILE).write_text(json.dumps(published), encoding="utf-8")
        chosen = sc.choose_clips(self.root, minutes=10, reuse=True)
        self.assertNotIn("ghost", {c.creature for c in chosen})


if __name__ == "__main__":
    unittest.main()


class ThumbnailTests(unittest.TestCase):
    """Shorts need no thumbnail because the feed plays them. Long-form is the
    opposite: nothing is watched that is not first clicked."""

    def setUp(self):
        self._workspace = TemporaryDirectory()
        self.dir = Path(self._workspace.name)
        self.addCleanup(self._workspace.cleanup)
        try:
            import imageio_ffmpeg  # noqa: F401
            from PIL import Image  # noqa: F401
        except ImportError:  # pragma: no cover - depends on the machine
            self.skipTest("needs Pillow and imageio-ffmpeg")
        self.clip = self.dir / "clip.mp4"
        import subprocess
        subprocess.run(
            [sc._ffmpeg(), "-y", "-v", "error", "-f", "lavfi",
             "-i", "testsrc=size=720x1280:duration=10:rate=30",
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             str(self.clip)], check=True,
        )

    def test_it_is_the_size_youtube_asks_for(self):
        from PIL import Image
        out = sc.build_thumbnail(self.clip, 66, self.dir / "t.png")
        self.assertEqual(Image.open(out).size, (sc.THUMB_W, sc.THUMB_H))

    def test_it_stays_under_the_two_megabyte_ceiling(self):
        out = sc.build_thumbnail(self.clip, 66, self.dir / "t.png")
        self.assertLess(out.stat().st_size, 2 * 1024 * 1024)

    def test_the_caption_shrinks_rather_than_running_off_the_edge(self):
        """A four-digit count must not push the words past the frame."""
        from PIL import Image, ImageDraw, ImageFont
        from channel_ops.video_overlay import _font_path
        draw = ImageDraw.Draw(Image.new("RGB", (sc.THUMB_W, sc.THUMB_H)))
        caption = f"{9999} UNFOLDING MACHINES"
        font = ImageFont.truetype(_font_path(), 96)
        while draw.textlength(caption, font=font) > sc.THUMB_W * 0.90 and font.size > 40:
            font = ImageFont.truetype(_font_path(), font.size - 2)
        self.assertLessEqual(draw.textlength(caption, font=font), sc.THUMB_W)

    def test_the_two_halves_come_from_different_moments(self):
        """Before and after: one frame with the shell shut, one with it open."""
        self.assertLess(sc.BEFORE_AT, 1.0)
        self.assertGreater(sc.AFTER_RATIO, 0.8)
