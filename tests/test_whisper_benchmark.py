"""Deterministic offline unit and integration tests for Whisper base-vs-small benchmark.

Validates discovery, ordering/limits, text normalization, WER/CER and agreement metrics,
caching, result aggregation, failure handling, and CLI execution with test doubles.
Runs 100% offline without provider APIs or model weight downloads.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from src.whisper_benchmark import (
    STTBenchmarkCache,
    WhisperBenchmarkRunner,
    compute_character_error_rate,
    compute_model_agreement,
    compute_word_error_rate,
    concept_counts,
    discover_recordings,
    find_reference_transcript,
    main,
    normalize_text,
    parse_whisper_benchmark_args,
    tokenize,
)


class FakeAudioTranscriber:
    """Mock transcriber returning deterministic output for testing without models."""

    def __init__(self, model_size: str, transcript_map: dict[str, str] | None = None) -> None:
        self.model_size = model_size
        self.transcript_map = transcript_map or {}
        self.call_count = 0

    def transcribe_file(
        self, audio_file_path: str | Path, beam_size: int = 3
    ) -> list[dict[str, str]]:
        self.call_count += 1
        path = Path(audio_file_path)
        default_text = (
            f"Transcribed with {self.model_size}: NIFTY call buy enter kiya"
            if self.model_size == "small"
            else f"Transcribed with {self.model_size}: nifty call buy enter kiya"
        )
        text = self.transcript_map.get(path.name, default_text)
        return [{"start": 0.0, "end": 5.0, "text": text}]


class TestTextNormalization(unittest.TestCase):
    """Tests for documented text normalization pipeline."""

    def test_normalize_latin_and_hindi_text(self) -> None:
        raw = "  Haan! Humne NIFTY 24500 CE BUY kiya... Target: 150, SL: 100! "
        norm = normalize_text(raw)
        self.assertEqual(norm, "haan humne nifty 24500 ce buy kiya target 150 sl 100")

    def test_normalize_devanagari_punctuation(self) -> None:
        raw = "निफ्टी में कॉल बाय किया। स्टॉप लॉस १००!"
        norm = normalize_text(raw)
        self.assertEqual(norm, "निफ्टी में कॉल बाय किया स्टॉप लॉस १००")

    def test_normalize_empty_and_whitespace(self) -> None:
        self.assertEqual(normalize_text(""), "")
        self.assertEqual(normalize_text("   \n\t   "), "")

    def test_tokenize(self) -> None:
        tokens = tokenize("Buy NIFTY 24500 CE at 120!")
        self.assertEqual(tokens, ["buy", "nifty", "24500", "ce", "at", "120"])


class TestAccuracyAndAgreementMetrics(unittest.TestCase):
    """Tests for WER, CER, concept counts, and model agreement calculation."""

    def test_wer_exact_match(self) -> None:
        ref = "Buy NIFTY call option at market price"
        hyp = "buy nifty call option at market price"
        res = compute_word_error_rate(ref, hyp)

        self.assertEqual(res["wer"], 0.0)
        self.assertEqual(res["word_accuracy"], 1.0)
        self.assertEqual(res["substitutions"], 0)
        self.assertEqual(res["deletions"], 0)
        self.assertEqual(res["insertions"], 0)
        self.assertEqual(res["hits"], 7)
        self.assertEqual(res["reference_words"], 7)

    def test_wer_substitutions_deletions_insertions(self) -> None:
        ref = "buy nifty call option now"
        # "sell" replaces "buy" (1 sub), "call" deleted (1 del), "strike" inserted (1 ins)
        hyp = "sell nifty option strike now"
        res = compute_word_error_rate(ref, hyp)

        self.assertEqual(res["reference_words"], 5)
        self.assertEqual(res["substitutions"], 1)
        self.assertEqual(res["deletions"], 1)
        self.assertEqual(res["insertions"], 1)
        self.assertEqual(res["hits"], 3)
        self.assertEqual(res["wer"], 0.6)  # 3 errors / 5 ref words

    def test_wer_empty_edge_cases(self) -> None:
        # Both empty
        res_both_empty = compute_word_error_rate("", "")
        self.assertEqual(res_both_empty["wer"], 0.0)
        self.assertEqual(res_both_empty["word_accuracy"], 1.0)

        # Empty reference, non-empty hypothesis
        res_empty_ref = compute_word_error_rate("", "some words")
        self.assertEqual(res_empty_ref["wer"], 1.0)
        self.assertEqual(res_empty_ref["word_accuracy"], 0.0)

        # Non-empty reference, empty hypothesis
        res_empty_hyp = compute_word_error_rate("reference words", "")
        self.assertEqual(res_empty_hyp["wer"], 1.0)
        self.assertEqual(res_empty_hyp["deletions"], 2)

    def test_cer_computation(self) -> None:
        self.assertEqual(compute_character_error_rate("hello", "hello"), 0.0)
        self.assertEqual(compute_character_error_rate("hello", "helo"), 0.2)
        self.assertEqual(compute_character_error_rate("", ""), 0.0)

    def test_concept_counts(self) -> None:
        text = "Yahan entry banti hai, humne trade execute kiya buy side pe, SL aur target set hai"
        counts = concept_counts(text)
        self.assertGreaterEqual(counts["entry"], 1)
        self.assertGreaterEqual(counts["trade"], 1)
        self.assertGreaterEqual(counts["buy"], 1)
        self.assertGreaterEqual(counts["stop_loss"], 1)
        self.assertGreaterEqual(counts["target"], 1)

    def test_model_agreement_without_references(self) -> None:
        base_text = "humne call buy kiya nifty mein"
        small_text = "humne call buy kiya banknifty mein"
        agreement = compute_model_agreement(base_text, small_text)

        self.assertGreater(agreement["sequence_similarity"], 0.70)
        self.assertGreater(agreement["unique_word_jaccard"], 0.60)
        self.assertEqual(agreement["word_count_a"], 6)
        self.assertEqual(agreement["word_count_b"], 6)
        self.assertEqual(agreement["word_count_diff"], 0)


class TestRecordingDiscoveryAndOrdering(unittest.TestCase):
    """Tests for deterministic discovery, ordering by mtime descending, and limit bounds."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_discover_recordings_sorted_by_mtime_descending(self) -> None:
        # Create 8 dummy audio files with staggered mtimes
        files = []
        base_time = time.time() - 1000.0
        for i in range(8):
            f_path = self.root / f"rec_{i:02d}.wav"
            f_path.write_bytes(b"RIFF dummy wav content")
            # Stagger mtimes: rec_00 oldest, rec_07 newest
            os.utime(f_path, (base_time + i * 50, base_time + i * 50))
            files.append(f_path)

        # Non-media files to ensure filter works
        (self.root / "notes.txt").write_text("not media")
        (self.root / "script.py").write_text("# not media")

        # Discover with limit 5
        discovered = discover_recordings(search_dir=self.root, limit=5)

        self.assertEqual(len(discovered), 5)
        # Should be rec_07, rec_06, rec_05, rec_04, rec_03 (newest first)
        self.assertEqual(discovered[0].name, "rec_07.wav")
        self.assertEqual(discovered[1].name, "rec_06.wav")
        self.assertEqual(discovered[2].name, "rec_05.wav")
        self.assertEqual(discovered[3].name, "rec_04.wav")
        self.assertEqual(discovered[4].name, "rec_03.wav")

    def test_discover_recordings_limit_10(self) -> None:
        for i in range(15):
            f_path = self.root / f"stream_{i:02d}.mp4"
            f_path.write_bytes(b"dummy mp4")

        discovered = discover_recordings(search_dir=self.root, limit=10)
        self.assertEqual(len(discovered), 10)

    def test_discover_explicit_inputs_list(self) -> None:
        f1 = self.root / "clip1.wav"
        f2 = self.root / "clip2.mp3"
        f1.write_bytes(b"wav")
        f2.write_bytes(b"mp3")

        discovered = discover_recordings(inputs=[f1, str(f2)], limit=10)
        self.assertEqual(len(discovered), 2)
        names = {p.name for p in discovered}
        self.assertEqual(names, {"clip1.wav", "clip2.mp3"})

    def test_discover_empty_directory(self) -> None:
        empty_dir = self.root / "empty_folder"
        empty_dir.mkdir()
        discovered = discover_recordings(search_dir=empty_dir)
        self.assertEqual(discovered, [])


class TestReferenceTranscriptPairing(unittest.TestCase):
    """Tests for locating and parsing reference transcripts."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_find_reference_companion_txt(self) -> None:
        audio = self.root / "live_01.wav"
        audio.write_bytes(b"wav")
        ref_file = self.root / "live_01.ref.txt"
        ref_file.write_text("Official ground truth transcript", encoding="utf-8")

        ref = find_reference_transcript(audio)
        self.assertEqual(ref, "Official ground truth transcript")

    def test_find_reference_youtube_json3(self) -> None:
        audio = self.root / "yt_live.mp4"
        audio.write_bytes(b"mp4")
        json3_content = {
            "events": [
                {"tStartMs": 0, "dDurationMs": 2000, "segs": [{"utf8": "Hello traders "}]},
                {"tStartMs": 2000, "dDurationMs": 2000, "segs": [{"utf8": "entry in nifty"}]},
            ]
        }
        json3_file = self.root / "yt_live.json3"
        json3_file.write_text(json.dumps(json3_content), encoding="utf-8")

        ref = find_reference_transcript(audio)
        self.assertEqual(ref, "Hello traders entry in nifty")

    def test_find_reference_not_present(self) -> None:
        audio = self.root / "unreferenced.wav"
        audio.write_bytes(b"wav")
        ref = find_reference_transcript(audio)
        self.assertIsNone(ref)


class TestSTTBenchmarkCache(unittest.TestCase):
    """Tests for resumable transcription caching."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.cache_dir = Path(self.temp_dir.name) / "cache"
        self.cache = STTBenchmarkCache(self.cache_dir)
        self.dummy_audio = Path(self.temp_dir.name) / "test_audio.wav"
        self.dummy_audio.write_bytes(b"RIFF1234WAVEfmt ")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_cache_miss_then_put_then_hit(self) -> None:
        # Initial check: Cache Miss
        cached = self.cache.get(
            file_path=self.dummy_audio,
            model_size="base",
            language="hi",
            compute_type="int8",
            beam_size=3,
            initial_prompt="prompt",
        )
        self.assertIsNone(cached)

        # Store entry
        payload = {
            "transcript": "Test cached transcription text",
            "processing_seconds": 1.23,
            "audio_duration_seconds": 10.0,
            "real_time_factor": 0.123,
            "segment_count": 2,
        }
        self.cache.put(
            file_path=self.dummy_audio,
            model_size="base",
            language="hi",
            compute_type="int8",
            beam_size=3,
            initial_prompt="prompt",
            result=payload,
        )

        # Second check: Cache Hit
        cached_hit = self.cache.get(
            file_path=self.dummy_audio,
            model_size="base",
            language="hi",
            compute_type="int8",
            beam_size=3,
            initial_prompt="prompt",
        )
        self.assertIsNotNone(cached_hit)
        self.assertEqual(cached_hit["transcript"], "Test cached transcription text")
        self.assertEqual(cached_hit["processing_seconds"], 1.23)

    def test_corrupt_cache_recovery(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        key = self.cache._make_key(
            self.dummy_audio, "base", "hi", "int8", 3, "prompt"
        )
        corrupt_file = self.cache_dir / f"{key}.json"
        corrupt_file.write_text("invalid json {{{", encoding="utf-8")

        cached = self.cache.get(
            self.dummy_audio, "base", "hi", "int8", 3, "prompt"
        )
        self.assertIsNone(cached)


class TestWhisperBenchmarkRunnerWorkflow(unittest.TestCase):
    """Integration tests for benchmark execution with mock transcribers."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.cache_dir = self.root / "cache"

        # Create 3 dummy audio files
        self.files = []
        for i in range(3):
            f = self.root / f"rec_{i + 1}.wav"
            f.write_bytes(b"RIFF dummy wav data")
            self.files.append(f)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_benchmark_without_references_labels_model_agreement(self) -> None:
        runner = WhisperBenchmarkRunner(
            models=["base", "small"],
            cache_dir=self.cache_dir,
            transcriber_factory=lambda model: FakeAudioTranscriber(model),
        )

        report = runner.run_benchmark(self.files)

        self.assertEqual(report.metadata["evaluation_mode"], "model_agreement")
        self.assertIsNone(report.verdict["accuracy_winner"])
        self.assertIn("agreement", report.verdict["accuracy_summary"].lower())
        self.assertEqual(len(report.files), 3)

        # Check per-file results
        for f_res in report.files:
            self.assertFalse(f_res.has_reference)
            self.assertIn("base", f_res.models)
            self.assertIn("small", f_res.models)
            self.assertIn("sequence_similarity", f_res.comparison)
            self.assertIn("speedup_ratio", f_res.comparison)

        # Summary table formatting check
        table = report.format_summary_table()
        self.assertIn("WHISPER BASE vs SMALL BENCHMARK REPORT", table)
        self.assertIn("MODEL_AGREEMENT", table)
        self.assertIn("No ground-truth reference transcripts supplied", table)

    def test_benchmark_with_references_computes_accuracy_winner(self) -> None:
        # Create reference files
        ref_text = "nifty call buy enter kiya humne"
        for f in self.files:
            (self.root / f"{f.stem}.ref.txt").write_text(ref_text, encoding="utf-8")

        # Fake transcriber map where "small" has exact match and "base" has substitution
        custom_map_base = {f.name: "nifty put buy enter kiya humne" for f in self.files}
        custom_map_small = {f.name: "nifty call buy enter kiya humne" for f in self.files}

        def factory(model: str) -> FakeAudioTranscriber:
            if model == "base":
                return FakeAudioTranscriber("base", custom_map_base)
            return FakeAudioTranscriber("small", custom_map_small)

        runner = WhisperBenchmarkRunner(
            models=["base", "small"],
            cache_dir=self.cache_dir,
            transcriber_factory=factory,
        )

        report = runner.run_benchmark(self.files)

        self.assertEqual(report.metadata["evaluation_mode"], "accuracy")
        self.assertEqual(report.verdict["accuracy_winner"], "small")
        self.assertIn("lower mean WER", report.verdict["accuracy_summary"])

        # Base had 1 substitution out of 6 words (WER ~ 0.1667)
        self.assertAlmostEqual(report.aggregate["base"]["mean_wer"], 1 / 6, places=3)
        # Small had exact match (WER 0.0)
        self.assertEqual(report.aggregate["small"]["mean_wer"], 0.0)

    def test_benchmark_resumable_caching(self) -> None:
        transcribers_created = []

        def factory(model: str) -> FakeAudioTranscriber:
            t = FakeAudioTranscriber(model)
            transcribers_created.append(t)
            return t

        runner = WhisperBenchmarkRunner(
            models=["base", "small"],
            cache_dir=self.cache_dir,
            transcriber_factory=factory,
        )

        # Pass 1: Fresh run (cache misses)
        report1 = runner.run_benchmark(self.files)
        self.assertFalse(report1.files[0].models["base"].cached)

        # Pass 2: Second run (cache hits)
        runner2 = WhisperBenchmarkRunner(
            models=["base", "small"],
            cache_dir=self.cache_dir,
            transcriber_factory=factory,
        )
        report2 = runner2.run_benchmark(self.files)
        self.assertTrue(report2.files[0].models["base"].cached)
        self.assertTrue(report2.files[0].models["small"].cached)


class TestWhisperBenchmarkCLI(unittest.TestCase):
    """Tests for CLI parsing and exit codes."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.audio1 = self.root / "sample_live_1.wav"
        self.audio1.write_bytes(b"RIFF wav")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_cli_argument_defaults(self) -> None:
        args = parse_whisper_benchmark_args([])
        self.assertEqual(args.limit, 10)
        self.assertEqual(args.models, ["base", "small"])
        self.assertEqual(args.device, "auto")
        self.assertFalse(args.no_cache)

    def test_cli_execution_with_mock_transcriber(self) -> None:
        output_json = self.root / "results.json"

        with patch(
            "src.whisper_benchmark.WhisperBenchmarkRunner._get_transcriber",
            return_value=FakeAudioTranscriber("base"),
        ):
            exit_code = main(
                [
                    "--inputs",
                    str(self.audio1),
                    "--output",
                    str(output_json),
                    "--quiet",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertTrue(output_json.exists())
        data = json.loads(output_json.read_text(encoding="utf-8"))
        self.assertEqual(data["metadata"]["benchmark_name"], "whisper_base_vs_small")
        self.assertEqual(len(data["files"]), 1)

    def test_cli_fails_when_no_recordings_found(self) -> None:
        empty_dir = self.root / "nonexistent_subfolder"
        exit_code = main(["--recordings-dir", str(empty_dir), "--quiet"])
        self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()

