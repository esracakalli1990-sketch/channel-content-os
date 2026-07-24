"""Comprehensive test suite for Channel Content OS."""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from channel_ops.config_loader import (
    AppConfig,
    ChannelConfig,
    GovernanceConfig,
    AIConfig,
    _parse_channel,
    _parse_governance,
    _parse_ai,
    load_config,
)
from channel_ops.production_ai import build_prompt, template_draft
from channel_ops.project import initialize_video, load_manifest, transition_video, status_summary
from channel_ops.scoring import calculate, parse_score_arguments
from channel_ops.visual_planner import (
    Scene,
    assign_clips_to_scenes,
    build_scenes,
    parse_srt,
)
from channel_ops.workflow import STATES, validate_transition


# -------------------------------------------------------------------------
# Workflow Tests
# -------------------------------------------------------------------------

class WorkflowTests(unittest.TestCase):
    """Tests for the state machine in workflow.py."""

    def test_sequential_forward_transition(self):
        self.assertTrue(validate_transition("idea", "research").allowed)

    def test_cannot_skip_states(self):
        self.assertFalse(validate_transition("idea", "script").allowed)

    def test_approval_state_needs_note(self):
        result = validate_transition("research", "topic_approved")
        self.assertFalse(result.allowed)
        self.assertIn("approval note", result.message.lower())

    def test_approval_state_with_note(self):
        result = validate_transition("research", "topic_approved", "Reviewed sources")
        self.assertTrue(result.allowed)

    def test_published_is_final(self):
        result = validate_transition("published", "idea")
        self.assertFalse(result.allowed)
        self.assertIn("already published", result.message.lower())

    def test_revert_to_research(self):
        """Rejecting at topic_approved should allow going back to research."""
        result = validate_transition("topic_approved", "research")
        self.assertTrue(result.allowed)

    def test_revert_to_script(self):
        """Rejecting at script_approved should allow going back to script."""
        result = validate_transition("script_approved", "script")
        self.assertTrue(result.allowed)

    def test_cannot_revert_two_steps(self):
        result = validate_transition("script_approved", "research")
        self.assertFalse(result.allowed)

    def test_all_forward_transitions(self):
        """Walk the entire pipeline from idea to published."""
        for i in range(len(STATES) - 1):
            current = STATES[i]
            target = STATES[i + 1]
            note = "Test approval" if "approved" in target or target == "published" else None
            result = validate_transition(current, target, note)
            self.assertTrue(result.allowed, f"{current} → {target} should be allowed")

    def test_unknown_state(self):
        self.assertFalse(validate_transition("idea", "nonexistent").allowed)
        self.assertFalse(validate_transition("nonexistent", "idea").allowed)


# -------------------------------------------------------------------------
# Scoring Tests
# -------------------------------------------------------------------------

class ScoringTests(unittest.TestCase):
    """Tests for the topic scoring engine."""

    def _full_scores(self, **overrides) -> dict[str, float]:
        base = {
            "demand": 7, "story": 8, "competition": 5, "evergreen": 8,
            "revenue": 7, "sources": 8, "visuals": 7, "production": 7,
        }
        base.update(overrides)
        return base

    def test_perfect_score(self):
        scores = {k: 10 for k in self._full_scores()}
        result = calculate(scores)
        self.assertEqual(result["total"], 100)
        self.assertTrue(result["qualified"])

    def test_threshold_from_parameter(self):
        result = calculate(self._full_scores(), threshold=90)
        self.assertFalse(result["qualified"])
        self.assertEqual(result["threshold"], 90)

    def test_default_threshold_75(self):
        result = calculate(self._full_scores())
        self.assertEqual(result["threshold"], 75.0)

    def test_missing_criterion_raises(self):
        with self.assertRaises(ValueError):
            calculate({"demand": 5})

    def test_unknown_criterion_raises(self):
        scores = self._full_scores()
        scores["bogus"] = 5
        with self.assertRaises(ValueError):
            calculate(scores)

    def test_out_of_range_raises(self):
        with self.assertRaises(ValueError):
            calculate(self._full_scores(demand=15))

    def test_parse_arguments(self):
        result = parse_score_arguments(["demand=7", "story=8.5"])
        self.assertEqual(result["demand"], 7.0)
        self.assertEqual(result["story"], 8.5)

    def test_parse_bad_format(self):
        with self.assertRaises(ValueError):
            parse_score_arguments(["demand:7"])


# -------------------------------------------------------------------------
# Config Tests
# -------------------------------------------------------------------------

class ConfigTests(unittest.TestCase):
    """Tests for config_loader.py."""

    def test_defaults(self):
        cfg = AppConfig()
        self.assertEqual(cfg.governance.minimum_topic_score, 75.0)
        self.assertEqual(cfg.ai.active_provider, "gemini")
        self.assertEqual(cfg.channel.language, "en")

    def test_parse_channel(self):
        data = {"channel": {"working_name": "TestChannel", "language": "tr"}}
        ch = _parse_channel(data)
        self.assertEqual(ch.working_name, "TestChannel")
        self.assertEqual(ch.language, "tr")

    def test_parse_governance(self):
        data = {"governance": {"minimum_topic_score": 80}}
        gov = _parse_governance(data)
        self.assertEqual(gov.minimum_topic_score, 80.0)

    def test_parse_ai(self):
        data = {"active_provider": "openai"}
        ai = _parse_ai(data)
        self.assertEqual(ai.active_provider, "openai")

    def test_load_config_with_files(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pyproject.toml").write_text("[project]\nname='test'\n")
            config_dir = root / "config"
            config_dir.mkdir()
            (config_dir / "channel.json").write_text(json.dumps({
                "channel": {"working_name": "LoadTest"},
                "governance": {"minimum_topic_score": 85},
            }))
            (config_dir / "ai.json").write_text(json.dumps({
                "active_provider": "ollama",
            }))
            cfg = load_config(root, force_reload=True)
            self.assertEqual(cfg.channel.working_name, "LoadTest")
            self.assertEqual(cfg.governance.minimum_topic_score, 85.0)
            self.assertEqual(cfg.ai.active_provider, "ollama")

    def test_load_config_missing_files(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pyproject.toml").write_text("[project]\nname='test'\n")
            cfg = load_config(root, force_reload=True)
            # Should use defaults
            self.assertEqual(cfg.ai.active_provider, "gemini")


# -------------------------------------------------------------------------
# Project Tests
# -------------------------------------------------------------------------

class ProjectTests(unittest.TestCase):
    """Tests for project.py."""

    def _make_project(self, tmpdir: str) -> Path:
        root = Path(tmpdir)
        tpl_dir = root / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "production_pack.md").write_text(
            "# Pack - {{VIDEO_ID}}\n", encoding="utf-8"
        )
        return root

    def test_initialize_creates_folders(self):
        with TemporaryDirectory() as tmpdir:
            root = self._make_project(tmpdir)
            path = initialize_video(root, "V-TEST", "Test topic")
            self.assertTrue((path / "01_topic").is_dir())
            self.assertTrue((path / "10_analytics").is_dir())
            self.assertTrue((path / "PRODUCTION_PACK.md").exists())
            self.assertIn("V-TEST", (path / "PRODUCTION_PACK.md").read_text())

    def test_initialize_creates_manifest(self):
        with TemporaryDirectory() as tmpdir:
            root = self._make_project(tmpdir)
            path = initialize_video(root, "V-TEST", "Test topic")
            manifest = json.loads((path / "manifest.json").read_text())
            self.assertEqual(manifest["status"], "idea")
            self.assertEqual(manifest["topic"], "Test topic")

    def test_duplicate_video_raises(self):
        with TemporaryDirectory() as tmpdir:
            root = self._make_project(tmpdir)
            initialize_video(root, "V-TEST", "Test")
            with self.assertRaises(FileExistsError):
                initialize_video(root, "V-TEST", "Test")

    def test_transition_updates_manifest(self):
        with TemporaryDirectory() as tmpdir:
            root = self._make_project(tmpdir)
            initialize_video(root, "V-TEST", "Test")
            manifest = transition_video(root, "V-TEST", "research")
            self.assertEqual(manifest["status"], "research")
            self.assertEqual(len(manifest["history"]), 2)

    def test_status_summary(self):
        with TemporaryDirectory() as tmpdir:
            root = self._make_project(tmpdir)
            initialize_video(root, "V-001", "Topic A")
            initialize_video(root, "V-002", "Topic B")
            rows = status_summary(root)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["video_id"], "V-001")


# -------------------------------------------------------------------------
# Production AI Tests
# -------------------------------------------------------------------------

class ProductionAITests(unittest.TestCase):
    """Tests for production_ai.py."""

    def test_requires_verified_sources(self):
        with self.assertRaises(ValueError):
            build_prompt("V-001", "Example", "script", [])

    def test_prompt_includes_claim_ids(self):
        sources = [{"claim_id": "C-001", "claim": "Claim text", "publisher": "Pub", "source_url": "https://example.com"}]
        prompt = build_prompt("V-001", "Example", "script", sources)
        self.assertIn("[C-001]", prompt)
        self.assertIn("Claim text", prompt)

    def test_unknown_stage_raises(self):
        sources = [{"claim_id": "C-001", "claim": "X", "publisher": "P", "source_url": "https://x.com"}]
        with self.assertRaises(ValueError):
            build_prompt("V-001", "Example", "thumbnail", sources)

    def test_template_draft(self):
        result = template_draft("test prompt")
        self.assertIn("Human/AI Draft Request", result)
        self.assertIn("test prompt", result)

    def test_all_stages_produce_prompt(self):
        sources = [{"claim_id": "C-001", "claim": "X", "publisher": "P", "source_url": "https://x.com"}]
        for stage in ("research", "script", "storyboard"):
            prompt = build_prompt("V-001", "Test", stage, sources)
            self.assertIn("[C-001]", prompt)
            self.assertIn("Test", prompt)


# -------------------------------------------------------------------------
# Visual planning tests
# -------------------------------------------------------------------------

class SceneBuildingTests(unittest.TestCase):
    def test_parse_srt_reads_cues(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "subs.srt"
            path.write_text(
                "1\n00:00:00,100 --> 00:00:04,500\nFirst line.\n\n"
                "2\n00:00:04,500 --> 00:00:09,000\nSecond line.\n",
                encoding="utf-8",
            )
            cues = parse_srt(path)
        self.assertEqual(len(cues), 2)
        self.assertAlmostEqual(cues[0][0], 0.1)
        self.assertAlmostEqual(cues[1][1], 9.0)
        self.assertEqual(cues[1][2], "Second line.")

    def test_parse_srt_missing_file_is_empty(self):
        self.assertEqual(parse_srt(Path("no_such_file.srt")), [])

    def test_scenes_cover_timeline_without_gaps(self):
        cues = [(float(i) * 5, float(i) * 5 + 5, f"Sentence {i}.") for i in range(12)]
        scenes = build_scenes(cues, target_seconds=15.0)
        self.assertGreater(len(scenes), 1)
        self.assertAlmostEqual(scenes[0].start, cues[0][0])
        self.assertAlmostEqual(scenes[-1].end, cues[-1][1])
        for earlier, later in zip(scenes, scenes[1:]):
            self.assertAlmostEqual(earlier.end, later.start)

    def test_scene_count_is_capped(self):
        cues = [(float(i), float(i) + 1, f"S{i}.") for i in range(200)]
        scenes = build_scenes(cues, target_seconds=1.0, max_scenes=10)
        self.assertLessEqual(len(scenes), 10)


class ClipAssignmentTests(unittest.TestCase):
    def setUp(self):
        self.burger = Path("burger.mp4")
        self.office = Path("office.mp4")
        self.crane = Path("crane.mp4")
        self.clip_map = {
            "burger cooking grill": self.burger,
            "real estate office": self.office,
            "construction crane": self.crane,
        }

    def test_exact_query_uses_its_own_clip(self):
        scenes = [Scene(0, 10, "text", query="real estate office")]
        segments = assign_clips_to_scenes(scenes, self.clip_map)
        self.assertEqual(segments[0][0], self.office)

    def test_unmatched_scene_picks_semantically_closest(self):
        # This query was never downloaded; the office clip is the closest.
        scenes = [Scene(0, 10, "Leasing commercial estate property.", query="estate leasing")]
        segments = assign_clips_to_scenes(scenes, self.clip_map)
        self.assertEqual(segments[0][0], self.office)

    def test_never_repeats_a_clip_back_to_back(self):
        scenes = [
            Scene(0, 10, "office talk", query="real estate office"),
            Scene(10, 20, "more office talk", query="real estate office"),
        ]
        segments = assign_clips_to_scenes(scenes, self.clip_map)
        self.assertNotEqual(segments[0][0], segments[1][0])

    def test_durations_follow_the_scenes(self):
        scenes = [Scene(0, 12, "a", query="burger cooking grill"),
                  Scene(12, 30, "b", query="construction crane")]
        segments = assign_clips_to_scenes(scenes, self.clip_map)
        self.assertEqual([round(d, 3) for _, d in segments], [12.0, 18.0])

    def test_reuse_is_spread_across_clips(self):
        # Ten unrelated scenes should not all land on one clip.
        scenes = [Scene(i * 10, i * 10 + 10, "unrelated topic", query="unrelated topic")
                  for i in range(10)]
        segments = assign_clips_to_scenes(scenes, self.clip_map)
        used = {path for path, _ in segments}
        self.assertEqual(len(used), 3)

    def test_empty_clip_map_returns_no_segments(self):
        scenes = [Scene(0, 10, "text", query="anything")]
        self.assertEqual(assign_clips_to_scenes(scenes, {}), [])


if __name__ == "__main__":
    unittest.main()
