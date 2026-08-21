"""Reproducible OpenAI Whisper base-vs-small transcription benchmark.

Evaluates and compares Whisper `base` and `small` models on local live recordings.
Supports deterministic input discovery (latest 5-10 recordings), reference transcript
pairing for defensible accuracy metrics (WER/CER with documented normalization),
fallback to model agreement when references are absent (without claiming accuracy winner),
resumable caching to avoid repeated transcriptions, machine-readable JSON output,
and a concise human-readable summary.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import logging
import os
import sys
import time
import unicodedata
import wave
from collections.abc import Sequence
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

try:
    import resource
except ImportError:
    resource = None

logger = logging.getLogger("whisper_benchmark")

# Supported audio and video media extensions for discovery
MEDIA_EXTENSIONS = (
    ".wav",
    ".mp3",
    ".m4a",
    ".mp4",
    ".mkv",
    ".flv",
    ".webm",
    ".aac",
    ".ogg",
    ".opus",
)

# Common domain trading terms in English and Hindi/Hinglish
CONCEPT_VARIANTS: dict[str, tuple[str, ...]] = {
    "entry": ("entry", "enter", "एंट्री", "एंटर", "enter kiya", "entry li"),
    "trade": ("trade", "ट्रेड", "trading", "ट्रेडिंग"),
    "buy": ("buy", "बाय", "buying", "बायिंग", "long"),
    "sell": ("sell", "सेल", "selling", "सेलिंग", "short"),
    "order": ("order", "ऑर्डर", "order executed"),
    "stop_loss": ("stop loss", "sl", "स्टॉप लॉस", "stoploss"),
    "target": ("target", "tgt", "टारगेट"),
    "call": ("call", "ce", "कॉल"),
    "put": ("put", "pe", "पुट"),
    "nifty": ("nifty", "निफ्टी"),
    "banknifty": ("banknifty", "बैंकनिफ्टी", "bank nifty"),
    "gold": ("gold", "xauusd", "गोल्ड"),
}


# ============================================================================
# 1. Text Normalization & Accuracy / Agreement Metrics
# ============================================================================


def normalize_text(text: str) -> str:
    """Normalize text for defensible STT accuracy (WER/CER) and agreement evaluation.

    Normalization pipeline:
    1. Unicode NFKC normalization to standardize decomposed characters.
    2. Case-folding (lower-casing) across all unicode alphabets.
    3. Removal of punctuation, quotes, symbols, and formatting marks while
       preserving alphanumeric characters and combining marks (matras/accents)
       in Latin, Devanagari, and other scripts.
    4. Collapsing consecutive whitespace into a single space and stripping edges.
    """
    if not text:
        return ""

    # Step 1: Unicode NFKC normalization
    normalized = unicodedata.normalize("NFKC", text)

    # Step 2: Lower-casing
    normalized = normalized.casefold()

    # Step 3: Punctuation removal - keep letters (L*), marks (M*), numbers (N*), and whitespace (Z*)
    cleaned_chars: list[str] = []
    for ch in normalized:
        category = unicodedata.category(ch)
        if category.startswith(("L", "M", "N", "Z")):
            cleaned_chars.append(ch)
        else:
            cleaned_chars.append(" ")

    cleaned = "".join(cleaned_chars)

    # Step 4: Collapse multiple whitespace characters into a single space
    return " ".join(cleaned.split()).strip()


def tokenize(text: str) -> list[str]:
    """Tokenize normalized text into a list of word tokens."""
    norm = normalize_text(text)
    return norm.split() if norm else []


def compute_word_error_rate(
    reference: str,
    hypothesis: str,
) -> dict[str, Any]:
    """Compute Word Error Rate (WER) and edit operations between reference and hypothesis.

    Uses dynamic programming with NIST-standard alignment weights (Sub=4, Del=3, Ins=3)
    to maximize true token matches before tallying unit substitutions, deletions, and insertions:
        WER = (Substitutions + Deletions + Insertions) / Reference_Words

    Edge cases:
    - If reference is empty and hypothesis is empty -> WER = 0.0, accuracy = 1.0
    - If reference is empty and hypothesis is not empty -> WER = 1.0, accuracy = 0.0

    Returns:
        dict containing 'wer', 'word_accuracy', 'substitutions', 'deletions',
        'insertions', 'hits', 'reference_words', and 'hypothesis_words'.
    """
    ref_tokens = tokenize(reference)
    hyp_tokens = tokenize(hypothesis)

    n_ref = len(ref_tokens)
    n_hyp = len(hyp_tokens)

    if n_ref == 0:
        if n_hyp == 0:
            return {
                "wer": 0.0,
                "word_accuracy": 1.0,
                "substitutions": 0,
                "deletions": 0,
                "insertions": 0,
                "hits": 0,
                "reference_words": 0,
                "hypothesis_words": 0,
            }
        return {
            "wer": 1.0,
            "word_accuracy": 0.0,
            "substitutions": 0,
            "deletions": 0,
            "insertions": n_hyp,
            "hits": 0,
            "reference_words": 0,
            "hypothesis_words": n_hyp,
        }

    # Alignment weights: Sub=4, Del=3, Ins=3 to prefer (Del + Ins = 6) over double substitution (8)
    sub_w = 4
    del_w = 3
    ins_w = 3

    dp = [[0] * (n_hyp + 1) for _ in range(n_ref + 1)]
    ops = [[""] * (n_hyp + 1) for _ in range(n_ref + 1)]

    for i in range(n_ref + 1):
        dp[i][0] = i * del_w
        ops[i][0] = "D"
    for j in range(n_hyp + 1):
        dp[0][j] = j * ins_w
        ops[0][j] = "I"
    ops[0][0] = " "

    for i in range(1, n_ref + 1):
        for j in range(1, n_hyp + 1):
            if ref_tokens[i - 1] == hyp_tokens[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
                ops[i][j] = "H"  # Hit
            else:
                sub_cost = dp[i - 1][j - 1] + sub_w
                del_cost = dp[i - 1][j] + del_w
                ins_cost = dp[i][j - 1] + ins_w

                min_cost = min(sub_cost, del_cost, ins_cost)
                dp[i][j] = min_cost

                if min_cost == sub_cost:
                    ops[i][j] = "S"
                elif min_cost == del_cost:
                    ops[i][j] = "D"
                else:
                    ops[i][j] = "I"

    # Backtrace edit operations
    i, j = n_ref, n_hyp
    substitutions = 0
    deletions = 0
    insertions = 0
    hits = 0

    while i > 0 or j > 0:
        op = ops[i][j]
        if op == "H":
            hits += 1
            i -= 1
            j -= 1
        elif op == "S":
            substitutions += 1
            i -= 1
            j -= 1
        elif op == "D":
            deletions += 1
            i -= 1
        elif op == "I":
            insertions += 1
            j -= 1
        else:
            break

    total_errors = substitutions + deletions + insertions
    wer = total_errors / n_ref
    word_accuracy = max(0.0, 1.0 - wer)

    return {
        "wer": round(wer, 4),
        "word_accuracy": round(word_accuracy, 4),
        "substitutions": substitutions,
        "deletions": deletions,
        "insertions": insertions,
        "hits": hits,
        "reference_words": n_ref,
        "hypothesis_words": n_hyp,
    }


def compute_character_error_rate(reference: str, hypothesis: str) -> float:
    """Compute Character Error Rate (CER) on normalized text."""
    ref_norm = normalize_text(reference)
    hyp_norm = normalize_text(hypothesis)

    n_ref = len(ref_norm)
    n_hyp = len(hyp_norm)

    if n_ref == 0:
        return 0.0 if n_hyp == 0 else 1.0

    # Character-level Levenshtein distance
    dp = [[0] * (n_hyp + 1) for _ in range(n_ref + 1)]
    for i in range(n_ref + 1):
        dp[i][0] = i
    for j in range(n_hyp + 1):
        dp[0][j] = j

    for i in range(1, n_ref + 1):
        for j in range(1, n_hyp + 1):
            if ref_norm[i - 1] == hyp_norm[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = 1 + min(dp[i - 1][j - 1], dp[i - 1][j], dp[i][j - 1])

    return round(dp[n_ref][n_hyp] / n_ref, 4)


def concept_counts(text: str) -> dict[str, int]:
    """Count domain-specific financial trading concept mentions."""
    lowered = text.casefold()
    return {
        concept: sum(lowered.count(variant.casefold()) for variant in variants)
        for concept, variants in CONCEPT_VARIANTS.items()
    }


def compute_model_agreement(
    text_a: str,
    text_b: str,
) -> dict[str, Any]:
    """Compute agreement metrics between two model outputs (e.g. base vs small).

    Used when no ground truth reference transcript is available.
    Explicitly labeled as agreement rather than accuracy.
    """
    tokens_a = tokenize(text_a)
    tokens_b = tokenize(text_b)

    set_a = set(tokens_a)
    set_b = set(tokens_b)
    union = set_a | set_b

    seq_matcher = SequenceMatcher(None, tokens_a, tokens_b, autojunk=False)
    seq_ratio = seq_matcher.ratio()
    jaccard = (len(set_a & set_b) / len(union)) if union else 1.0

    return {
        "sequence_similarity": round(seq_ratio, 4),
        "unique_word_jaccard": round(jaccard, 4),
        "word_count_a": len(tokens_a),
        "word_count_b": len(tokens_b),
        "word_count_diff": len(tokens_b) - len(tokens_a),
        "concept_counts_a": concept_counts(text_a),
        "concept_counts_b": concept_counts(text_b),
    }


# ============================================================================
# 2. File Discovery and Reference Transcript Pairing
# ============================================================================


def discover_recordings(
    inputs: Sequence[str | Path] | None = None,
    search_dir: str | Path | None = None,
    limit: int = 10,
    extensions: tuple[str, ...] = MEDIA_EXTENSIONS,
) -> list[Path]:
    """Discover or filter local recording media files deterministically.

    Ordering rule:
    - Files are sorted by modification time descending (latest files first).
    - Ties are broken deterministically by resolved absolute file path string.

    Args:
        inputs: Optional list of explicit file paths or directory paths.
        search_dir: Optional root directory to scan if inputs not given.
        limit: Maximum number of files to return (e.g. 5 to 10).
        extensions: Tuple of allowed media file extensions.

    Returns:
        list[Path]: List of up to `limit` discovered file paths.
    """
    discovered: list[Path] = []
    seen: set[str] = set()

    def add_candidate(path: Path) -> None:
        try:
            resolved = path.resolve()
            key = str(resolved).casefold()
            if (
                key not in seen
                and resolved.exists()
                and resolved.is_file()
                and resolved.suffix.lower() in extensions
            ):
                seen.add(key)
                discovered.append(resolved)
        except OSError:
            pass

    if inputs:
        for item in inputs:
            p = Path(item)
            if p.is_dir():
                for root, _, files in os.walk(p):
                    for file_name in files:
                        add_candidate(Path(root) / file_name)
            elif p.is_file():
                add_candidate(p)
            elif any(char in str(item) for char in ("*", "?", "[")):
                # Glob pattern
                parent = p.parent if str(p.parent) else Path(".")
                pattern = p.name
                for match in parent.glob(pattern):
                    if match.is_file():
                        add_candidate(match)
    else:
        root_dir = Path(search_dir) if search_dir else Path(".")
        if root_dir.exists() and root_dir.is_dir():
            for root, _, files in os.walk(root_dir):
                for file_name in files:
                    add_candidate(Path(root) / file_name)

    # Deterministic sort: mtime DESC, path ASC
    def sort_key(p: Path) -> tuple[float, str]:
        try:
            mtime = p.stat().st_mtime
        except OSError:
            mtime = 0.0
        return (-mtime, str(p).casefold())

    discovered.sort(key=sort_key)

    if limit > 0:
        return discovered[:limit]
    return discovered


def parse_youtube_json3(json_path: Path) -> str:
    """Extract full caption text from a YouTube json3 format caption file."""
    data = json.loads(json_path.read_text(encoding="utf-8"))
    entries: list[str] = []
    for event in data.get("events", []):
        text = "".join(
            segment.get("utf8", "") for segment in event.get("segs", [])
        ).strip()
        if text:
            entries.append(text)
    return " ".join(entries)


def find_reference_transcript(
    audio_path: Path,
    reference_dir: Path | None = None,
    references_map: dict[str, str | Path] | None = None,
) -> str | None:
    """Pair an audio file with its ground truth reference transcript if available.

    Checks:
    1. `references_map` lookup by filename, stem, or string path.
    2. Companion reference file in `reference_dir`.
    3. Companion reference file in the same directory as the audio file.
       Candidate names: `<stem>.ref.txt`, `<stem>.ref.json`, `<stem>_reference.txt`,
       `<stem>.txt`, `<stem>.json3`, `<stem>.json`.
    """
    # 1. Map lookup
    if references_map:
        candidates = [
            audio_path.name,
            audio_path.stem,
            str(audio_path),
            str(audio_path.resolve()),
        ]
        for c in candidates:
            if c in references_map:
                ref_val = references_map[c]
                ref_path = Path(ref_val)
                if ref_path.exists() and ref_path.is_file():
                    if ref_path.suffix.lower() in (".json", ".json3"):
                        try:
                            return parse_youtube_json3(ref_path)
                        except (json.JSONDecodeError, KeyError, ValueError, OSError):
                            return ref_path.read_text(encoding="utf-8").strip()
                    return ref_path.read_text(encoding="utf-8").strip()
                # If literal text passed in mapping
                return str(ref_val).strip()

    # Candidate file suffixes
    suffixes = [
        ".ref.txt",
        "_reference.txt",
        ".ref.json",
        ".txt",
        ".json3",
    ]

    search_dirs = []
    if reference_dir and reference_dir.exists():
        search_dirs.append(reference_dir)
    search_dirs.append(audio_path.parent)

    for s_dir in search_dirs:
        for sfx in suffixes:
            candidate = s_dir / f"{audio_path.stem}{sfx}"
            if candidate.exists() and candidate.is_file():
                try:
                    if candidate.suffix.lower() == ".json3" or candidate.name.endswith(
                        ".ref.json"
                    ):
                        return parse_youtube_json3(candidate)
                    return candidate.read_text(encoding="utf-8").strip()
                except (json.JSONDecodeError, KeyError, ValueError, OSError) as exc:
                    logger.warning(
                        f"Failed to read reference file {candidate}: {exc}"
                    )

    return None


def get_media_duration_seconds(file_path: Path) -> float:
    """Determine duration of an audio or video file in seconds without network calls."""
    # 1. Try WAV header
    if file_path.suffix.lower() == ".wav":
        try:
            with wave.open(str(file_path), "rb") as wf:
                framerate = wf.getframerate()
                nframes = wf.getnframes()
                if framerate > 0:
                    return round(nframes / framerate, 3)
        except (wave.Error, OSError, EOFError):
            pass

    # 2. Try OpenCV for video containers
    try:
        import cv2

        cap = cv2.VideoCapture(str(file_path))
        if cap.isOpened():
            fps = cap.get(cv2.CAP_PROP_FPS)
            frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
            cap.release()
            if fps > 0 and frame_count > 0:
                return round(frame_count / fps, 3)
    except Exception:  # noqa: BLE001, S110
        pass

    # Default fallback estimation or 0.0
    return 0.0


# ============================================================================
# 3. Resumable Caching System
# ============================================================================


class STTBenchmarkCache:
    """Persistent on-disk cache for Whisper transcription results.

    Prevents expensive re-transcription across repeated benchmark runs.
    """

    def __init__(self, cache_dir: str | Path = ".stt_cache") -> None:
        self.cache_dir = Path(cache_dir)

    def _make_key(
        self,
        file_path: Path,
        model_size: str,
        language: str | None,
        compute_type: str,
        beam_size: int,
        initial_prompt: str,
    ) -> str:
        try:
            stat = file_path.stat()
            file_sig = f"{file_path.name}:{stat.st_size}:{stat.st_mtime}"
        except OSError:
            file_sig = f"{file_path.name}:0:0"

        config_sig = f"{model_size}:{language}:{compute_type}:{beam_size}:{initial_prompt}"
        combined = f"{file_sig}|{config_sig}"
        return hashlib.sha256(combined.encode("utf-8")).hexdigest()[:24]

    def get(
        self,
        file_path: Path,
        model_size: str,
        language: str | None,
        compute_type: str,
        beam_size: int,
        initial_prompt: str,
    ) -> dict[str, Any] | None:
        """Retrieve cached transcription result if present and valid."""
        try:
            key = self._make_key(
                file_path,
                model_size,
                language,
                compute_type,
                beam_size,
                initial_prompt,
            )
            cache_file = self.cache_dir / f"{key}.json"
            if cache_file.exists():
                data = json.loads(cache_file.read_text(encoding="utf-8"))
                if (
                    isinstance(data, dict)
                    and "transcript" in data
                    and "processing_seconds" in data
                ):
                    return data
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"Cache read failed for {file_path}: {exc}")
        return None

    def put(
        self,
        file_path: Path,
        model_size: str,
        language: str | None,
        compute_type: str,
        beam_size: int,
        initial_prompt: str,
        result: dict[str, Any],
    ) -> None:
        """Store transcription result to cache atomically."""
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            key = self._make_key(
                file_path,
                model_size,
                language,
                compute_type,
                beam_size,
                initial_prompt,
            )
            cache_file = self.cache_dir / f"{key}.json"
            tmp_file = cache_file.with_suffix(".tmp")
            tmp_file.write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp_file, cache_file)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Cache write failed for {file_path}: {exc}")


# ============================================================================
# 4. Whisper Benchmark Runner & Reporter Data Structures
# ============================================================================


@dataclasses.dataclass
class ModelTranscriptionResult:
    """Transcription outcome for a single file on a specific Whisper model."""

    model_size: str
    transcript: str
    processing_seconds: float
    audio_duration_seconds: float
    real_time_factor: float
    word_count: int
    segment_count: int
    cached: bool
    metrics_vs_reference: dict[str, Any] | None = None


@dataclasses.dataclass
class FileBenchmarkResult:
    """Benchmark results for a single audio/video recording across models."""

    file_path: str
    file_name: str
    duration_seconds: float
    has_reference: bool
    reference_transcript: str | None
    models: dict[str, ModelTranscriptionResult]
    comparison: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "file_name": self.file_name,
            "duration_seconds": self.duration_seconds,
            "has_reference": self.has_reference,
            "reference_transcript": self.reference_transcript,
            "models": {
                m_name: dataclasses.asdict(res)
                for m_name, res in self.models.items()
            },
            "comparison": self.comparison,
        }


@dataclasses.dataclass
class BenchmarkSummaryReport:
    """Aggregated benchmark report across all evaluated live recordings."""

    metadata: dict[str, Any]
    files: list[FileBenchmarkResult]
    aggregate: dict[str, Any]
    verdict: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "metadata": self.metadata,
            "files": [f.to_dict() for f in self.files],
            "aggregate": self.aggregate,
            "verdict": self.verdict,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def format_summary_table(self) -> str:
        """Format a clean, concise terminal summary table."""
        lines: list[str] = [
            "",
            "=" * 78,
            " WHISPER BASE vs SMALL BENCHMARK REPORT",
            "=" * 78,
            f"Files Evaluated:   {self.metadata.get('total_files', len(self.files))}",
            f"Total Audio Time:  {self.aggregate.get('total_audio_seconds', 0.0):.1f}s ({self.aggregate.get('total_audio_seconds', 0.0) / 60:.2f}m)",
            f"Evaluation Mode:   {self.metadata.get('evaluation_mode', 'UNKNOWN').upper()}",
            f"Device / Compute:  {self.metadata.get('device')} ({self.metadata.get('compute_type')})",
            "-" * 78,
        ]

        mode = self.metadata.get("evaluation_mode", "agreement")
        if mode == "accuracy":
            lines.append(
                f"{'File':<24} | {'Dur':<6} | {'Base (s/RTF)':<14} | {'Small (s/RTF)':<14} | {'WER (B vs S)':<14}"
            )
        else:
            lines.append(
                f"{'File':<24} | {'Dur':<6} | {'Base (s/RTF)':<14} | {'Small (s/RTF)':<14} | {'Agreement %':<14}"
            )
        lines.append("-" * 78)

        for f in self.files:
            b_res = f.models.get("base")
            s_res = f.models.get("small")
            b_str = (
                f"{b_res.processing_seconds:.2f}s ({b_res.real_time_factor:.2f})"
                if b_res
                else "N/A"
            )
            s_str = (
                f"{s_res.processing_seconds:.2f}s ({s_res.real_time_factor:.2f})"
                if s_res
                else "N/A"
            )

            name_trunc = (
                f.file_name[:21] + "..." if len(f.file_name) > 24 else f.file_name
            )

            if f.has_reference and b_res and s_res:
                b_wer = (
                    f"{b_res.metrics_vs_reference['wer'] * 100:.1f}%"
                    if b_res.metrics_vs_reference
                    else "-"
                )
                s_wer = (
                    f"{s_res.metrics_vs_reference['wer'] * 100:.1f}%"
                    if s_res.metrics_vs_reference
                    else "-"
                )
                metric_col = f"{b_wer} / {s_wer}"
            else:
                sim = f.comparison.get("sequence_similarity", 0.0)
                metric_col = f"{sim * 100:.1f}%"

            lines.append(
                f"{name_trunc:<24} | {f.duration_seconds:<6.1f} | {b_str:<14} | {s_str:<14} | {metric_col:<14}"
            )

        lines.append("-" * 78)
        b_agg = self.aggregate.get("base", {})
        s_agg = self.aggregate.get("small", {})
        speedup = self.aggregate.get("speedup_factor", 1.0)
        lines.append(
            f"Base Total Time:   {b_agg.get('total_processing_seconds', 0.0):.2f}s "
            f"(Mean RTF: {b_agg.get('mean_rtf', 0.0):.3f})"
        )
        lines.append(
            f"Small Total Time:  {s_agg.get('total_processing_seconds', 0.0):.2f}s "
            f"(Mean RTF: {s_agg.get('mean_rtf', 0.0):.3f})"
        )
        lines.append(f"Base vs Small Speedup: {speedup:.2f}x faster inference with Base")

        if mode == "accuracy":
            lines.append(
                f"Mean Word Error Rate (WER): Base={b_agg.get('mean_wer', 0.0) * 100:.1f}% | "
                f"Small={s_agg.get('mean_wer', 0.0) * 100:.1f}%"
            )
            lines.append(
                f"Accuracy Winner:   {self.verdict.get('accuracy_winner', 'Tie/Inconclusive')}"
            )
        else:
            mean_sim = self.aggregate.get("mean_sequence_similarity", 0.0)
            lines.append(
                f"Mean Model Agreement: {mean_sim * 100:.1f}% Sequence Token Match"
            )
            lines.append(
                "NOTE: No ground-truth reference transcripts supplied. Metric represents "
                "inter-model agreement, not accuracy. No accuracy winner is claimed."
            )

        lines.append("=" * 78)
        lines.append("")
        return "\n".join(lines)


# ============================================================================
# 5. Benchmark Execution Engine
# ============================================================================


class WhisperBenchmarkRunner:
    """Orchestrates Whisper base vs small benchmark across recordings."""

    def __init__(
        self,
        models: Sequence[str] = ("base", "small"),
        device: str = "auto",
        compute_type: str = "auto",
        language: str | None = "hi",
        initial_prompt: str | None = None,
        beam_size: int = 3,
        cache_dir: str | Path | None = ".stt_cache",
        use_cache: bool = True,
        download_root: str | None = None,
        transcriber_factory: Any = None,
    ) -> None:
        self.models = list(models)
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self.initial_prompt = (
            initial_prompt
            or "Nifty, BankNifty, Sensex, Call, Put, CE, PE, Buy, Sell, SL, Target, Stop Loss, Entry, Exit, Long, Short, Premium, Breakout."
        )
        self.beam_size = beam_size
        self.use_cache = use_cache
        self.cache = STTBenchmarkCache(cache_dir or ".stt_cache")
        self.download_root = download_root
        self.transcriber_factory = transcriber_factory
        self._transcribers: dict[str, Any] = {}

    def _get_transcriber(self, model_size: str) -> Any:
        if model_size in self._transcribers:
            return self._transcribers[model_size]

        if self.transcriber_factory is not None:
            transcriber = self.transcriber_factory(model_size)
        else:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise ImportError(
                    "faster-whisper is required for offline Whisper benchmarks. "
                    "Install it as an optional dependency with: pip install faster-whisper"
                ) from exc

            class _OfflineLocalWhisperTranscriber:
                def __init__(
                    self,
                    model_name: str,
                    device: str,
                    compute_type: str,
                    language: str | None,
                    prompt: str,
                    download_root: str | None,
                ) -> None:
                    dev = device if device != "auto" else "cpu"
                    c_type = compute_type if compute_type != "auto" else "int8"
                    self._model = WhisperModel(
                        model_name,
                        device=dev,
                        compute_type=c_type,
                        download_root=download_root or "D:/huggingface_cache/whisper",
                    )
                    self.language = language
                    self.prompt = prompt

                def transcribe_file(
                    self,
                    audio_file_path: str | Path,
                    beam_size: int = 3,
                ) -> list[dict[str, Any]]:
                    segments, _ = self._model.transcribe(
                        str(audio_file_path),
                        language=self.language,
                        initial_prompt=self.prompt,
                        beam_size=beam_size,
                        vad_filter=True,
                    )
                    return [
                        {
                            "start": round(s.start, 2),
                            "end": round(s.end, 2),
                            "text": s.text.strip(),
                        }
                        for s in segments
                    ]

            transcriber = _OfflineLocalWhisperTranscriber(
                model_name=model_size,
                device=self.device,
                compute_type=self.compute_type,
                language=self.language,
                prompt=self.initial_prompt,
                download_root=self.download_root,
            )

        self._transcribers[model_size] = transcriber
        return transcriber

    def run_single_model(
        self,
        file_path: Path,
        model_size: str,
        audio_duration: float,
    ) -> ModelTranscriptionResult:
        """Transcribe a single file with one model, checking and updating cache."""
        if self.use_cache:
            cached_data = self.cache.get(
                file_path=file_path,
                model_size=model_size,
                language=self.language,
                compute_type=self.compute_type,
                beam_size=self.beam_size,
                initial_prompt=self.initial_prompt,
            )
            if cached_data is not None:
                logger.info(
                    f"Cache HIT for {file_path.name} ({model_size}): {cached_data['processing_seconds']}s"
                )
                return ModelTranscriptionResult(
                    model_size=model_size,
                    transcript=cached_data["transcript"],
                    processing_seconds=cached_data["processing_seconds"],
                    audio_duration_seconds=audio_duration
                    or cached_data.get("audio_duration_seconds", 0.0),
                    real_time_factor=cached_data.get(
                        "real_time_factor",
                        round(
                            cached_data["processing_seconds"]
                            / max(audio_duration, 0.001),
                            4,
                        ),
                    ),
                    word_count=len(cached_data["transcript"].split()),
                    segment_count=cached_data.get("segment_count", 1),
                    cached=True,
                )

        # Cache miss: run transcription
        transcriber = self._get_transcriber(model_size)
        started = time.perf_counter()
        segments = transcriber.transcribe_file(
            file_path, beam_size=self.beam_size
        )
        elapsed = time.perf_counter() - started

        transcript = " ".join(
            seg.get("text", "") for seg in segments if seg.get("text")
        ).strip()
        dur = max(audio_duration, 0.001)
        rtf = round(elapsed / dur, 4) if dur > 0 else 0.0

        cache_payload = {
            "transcript": transcript,
            "processing_seconds": round(elapsed, 4),
            "audio_duration_seconds": round(audio_duration, 3),
            "real_time_factor": rtf,
            "segment_count": len(segments),
            "cached_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        if self.use_cache:
            self.cache.put(
                file_path=file_path,
                model_size=model_size,
                language=self.language,
                compute_type=self.compute_type,
                beam_size=self.beam_size,
                initial_prompt=self.initial_prompt,
                result=cache_payload,
            )

        return ModelTranscriptionResult(
            model_size=model_size,
            transcript=transcript,
            processing_seconds=round(elapsed, 4),
            audio_duration_seconds=round(audio_duration, 3),
            real_time_factor=rtf,
            word_count=len(transcript.split()),
            segment_count=len(segments),
            cached=False,
        )

    def evaluate_file(
        self,
        file_path: Path,
        reference_transcript: str | None = None,
    ) -> FileBenchmarkResult:
        """Run benchmark on one file across all configured models."""
        audio_dur = get_media_duration_seconds(file_path)
        model_results: dict[str, ModelTranscriptionResult] = {}

        for m_name in self.models:
            res = self.run_single_model(file_path, m_name, audio_dur)
            if reference_transcript:
                res.metrics_vs_reference = compute_word_error_rate(
                    reference_transcript, res.transcript
                )
                res.metrics_vs_reference["cer"] = compute_character_error_rate(
                    reference_transcript, res.transcript
                )
            model_results[m_name] = res

        # Inter-model comparison
        model_a = self.models[0] if len(self.models) > 0 else "base"
        model_b = (
            self.models[1]
            if len(self.models) > 1
            else (self.models[0] if self.models else "small")
        )

        text_a = model_results[model_a].transcript if model_a in model_results else ""
        text_b = model_results[model_b].transcript if model_b in model_results else ""
        comparison = compute_model_agreement(text_a, text_b)

        time_a = (
            model_results[model_a].processing_seconds
            if model_a in model_results
            else 0.0
        )
        time_b = (
            model_results[model_b].processing_seconds
            if model_b in model_results
            else 0.0
        )
        comparison["speedup_ratio"] = round(time_b / max(time_a, 0.001), 2)

        return FileBenchmarkResult(
            file_path=str(file_path.resolve()),
            file_name=file_path.name,
            duration_seconds=audio_dur,
            has_reference=bool(reference_transcript),
            reference_transcript=reference_transcript,
            models=model_results,
            comparison=comparison,
        )

    def run_benchmark(
        self,
        recordings: Sequence[Path],
        reference_dir: Path | None = None,
        references_map: dict[str, str | Path] | None = None,
    ) -> BenchmarkSummaryReport:
        """Run complete benchmark on multiple recording inputs."""
        if not recordings:
            raise ValueError(
                "No recording files provided for Whisper benchmark."
            )

        file_results: list[FileBenchmarkResult] = []
        total_audio_sec = 0.0
        total_proc_sec: dict[str, float] = {m: 0.0 for m in self.models}
        all_rtfs: dict[str, list[float]] = {m: [] for m in self.models}
        all_wers: dict[str, list[float]] = {m: [] for m in self.models}
        agreements: list[float] = []

        for idx, rec_path in enumerate(recordings, start=1):
            logger.info(
                f"[{idx}/{len(recordings)}] Benchmarking Whisper on {rec_path.name}..."
            )
            ref_text = find_reference_transcript(
                rec_path, reference_dir, references_map
            )
            f_result = self.evaluate_file(rec_path, ref_text)
            file_results.append(f_result)

            total_audio_sec += f_result.duration_seconds
            for m_name, m_res in f_result.models.items():
                total_proc_sec[m_name] = (
                    total_proc_sec.get(m_name, 0.0) + m_res.processing_seconds
                )
                all_rtfs.setdefault(m_name, []).append(m_res.real_time_factor)
                if m_res.metrics_vs_reference:
                    all_wers.setdefault(m_name, []).append(
                        m_res.metrics_vs_reference["wer"]
                    )

            agreements.append(
                f_result.comparison.get("sequence_similarity", 0.0)
            )

        # Summary calculations
        has_any_ref = any(f.has_reference for f in file_results)
        has_all_ref = all(f.has_reference for f in file_results)

        if has_all_ref:
            eval_mode = "accuracy"
        elif has_any_ref:
            eval_mode = "mixed"
        else:
            eval_mode = "model_agreement"

        model_a = self.models[0] if len(self.models) > 0 else "base"
        model_b = (
            self.models[1]
            if len(self.models) > 1
            else (self.models[0] if self.models else "small")
        )
        time_a = total_proc_sec.get(model_a, 0.0)
        time_b = total_proc_sec.get(model_b, 0.0)
        overall_speedup = round(time_b / max(time_a, 0.001), 2)

        aggregate_data: dict[str, Any] = {
            "total_files": len(file_results),
            "total_audio_seconds": round(total_audio_sec, 3),
            "mean_sequence_similarity": round(
                sum(agreements) / max(len(agreements), 1), 4
            ),
            "speedup_factor": overall_speedup,
        }

        for m_name in self.models:
            m_rtfs = all_rtfs.get(m_name, [])
            m_wers = all_wers.get(m_name, [])
            aggregate_data[m_name] = {
                "total_processing_seconds": round(
                    total_proc_sec.get(m_name, 0.0), 3
                ),
                "mean_rtf": round(sum(m_rtfs) / max(len(m_rtfs), 1), 4),
                "mean_wer": round(sum(m_wers) / max(len(m_wers), 1), 4)
                if m_wers
                else None,
            }

        # Verdict
        verdict: dict[str, Any] = {
            "evaluation_mode": eval_mode,
            "faster_model": model_a if time_a < time_b else model_b,
        }

        if eval_mode in ("accuracy", "mixed") and all_wers.get(
            model_a
        ) and all_wers.get(model_b):
            mean_wer_a = aggregate_data[model_a]["mean_wer"]
            mean_wer_b = aggregate_data[model_b]["mean_wer"]
            if mean_wer_a < mean_wer_b:
                verdict["accuracy_winner"] = model_a
                verdict["accuracy_summary"] = (
                    f"{model_a} achieved lower mean WER ({mean_wer_a * 100:.1f}% vs {mean_wer_b * 100:.1f}%)"
                )
            elif mean_wer_b < mean_wer_a:
                verdict["accuracy_winner"] = model_b
                verdict["accuracy_summary"] = (
                    f"{model_b} achieved lower mean WER ({mean_wer_b * 100:.1f}% vs {mean_wer_a * 100:.1f}%)"
                )
            else:
                verdict["accuracy_winner"] = "Tie"
                verdict["accuracy_summary"] = (
                    f"Both models achieved identical mean WER ({mean_wer_a * 100:.1f}%)"
                )
        else:
            verdict["accuracy_winner"] = None
            verdict["accuracy_summary"] = (
                "No ground-truth reference transcripts supplied. Comparison measures "
                "inter-model agreement only; accuracy winner cannot be claimed."
            )

        metadata = {
            "benchmark_name": "whisper_base_vs_small",
            "models_compared": self.models,
            "device": self.device,
            "compute_type": self.compute_type,
            "language": self.language,
            "beam_size": self.beam_size,
            "total_files": len(file_results),
            "evaluation_mode": eval_mode,
            "timestamp_utc": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
            ),
            "max_rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            if resource
            else 0,
        }

        return BenchmarkSummaryReport(
            metadata=metadata,
            files=file_results,
            aggregate=aggregate_data,
            verdict=verdict,
        )


# ============================================================================
# 6. CLI Argument Parsing and Main Entrypoint
# ============================================================================


def parse_whisper_benchmark_args(
    args: list[str] | None = None,
) -> argparse.Namespace:
    """Parse command line arguments for Whisper base-vs-small benchmark."""
    parser = argparse.ArgumentParser(
        description="Reproducible OpenAI Whisper base-vs-small benchmark on latest live recordings",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "positional_inputs",
        nargs="*",
        type=str,
        default=[],
        help="Optional positional recording media file(s) or directory paths",
    )
    parser.add_argument(
        "--inputs",
        "-i",
        nargs="+",
        type=str,
        default=[],
        help="Explicit list of recording file paths or glob patterns",
    )
    parser.add_argument(
        "--recordings-dir",
        "--dir",
        "-d",
        type=str,
        default=None,
        help="Directory to scan for local live recordings (e.g. data/, recordings/)",
    )
    parser.add_argument(
        "--limit",
        "-n",
        type=int,
        default=10,
        help="Number of latest recordings to evaluate (e.g. 5 to 10)",
    )
    parser.add_argument(
        "--reference-dir",
        type=str,
        default=None,
        help="Directory containing ground truth reference transcript files",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default=None,
        help="Path to write machine-readable JSON benchmark report",
    )
    parser.add_argument(
        "--cache-dir",
        type=str,
        default=".stt_cache",
        help="Directory for caching transcriptions across runs",
    )
    parser.add_argument(
        "--no-cache",
        "--force",
        action="store_true",
        help="Bypass cache and force re-transcription",
    )
    parser.add_argument(
        "--models",
        nargs="+",
        default=["base", "small"],
        help="Whisper model sizes to evaluate and compare",
    )
    parser.add_argument(
        "--device",
        type=str,
        default="auto",
        choices=["auto", "cpu", "cuda"],
        help="Compute device for Whisper inference",
    )
    parser.add_argument(
        "--compute-type",
        type=str,
        default="auto",
        help="Quantization compute type ('auto', 'int8', 'float16', 'default')",
    )
    parser.add_argument(
        "--language",
        type=str,
        default="hi",
        help="Language hint ('hi' for Hindi/Hinglish, 'en', or None for auto)",
    )
    parser.add_argument(
        "--initial-prompt",
        type=str,
        default=None,
        help="Initial prompt vocabulary bias",
    )
    parser.add_argument(
        "--beam-size",
        type=int,
        default=3,
        help="Beam search width for offline transcription",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress terminal summary output; only write JSON",
    )
    return parser.parse_args(args)


def main(args: list[str] | None = None) -> int:
    """Main CLI entrypoint for Whisper base-vs-small benchmark."""
    parsed = parse_whisper_benchmark_args(args)

    raw_inputs = list(parsed.inputs) + list(parsed.positional_inputs)
    discovered_files = discover_recordings(
        inputs=raw_inputs if raw_inputs else None,
        search_dir=parsed.recordings_dir,
        limit=parsed.limit,
    )

    if not discovered_files:
        print(
            "ERROR: No live audio/video recordings found to benchmark. "
            "Please specify files with --inputs or provide a valid directory with --recordings-dir.",
            file=sys.stderr,
        )
        return 1

    if not parsed.quiet:
        print(
            f"[*] Found {len(discovered_files)} recording(s) to benchmark with models {parsed.models}:",
            file=sys.stderr,
        )
        for f in discovered_files:
            print(f"    - {f.name}", file=sys.stderr)

    runner = WhisperBenchmarkRunner(
        models=parsed.models,
        device=parsed.device,
        compute_type=parsed.compute_type,
        language=parsed.language,
        initial_prompt=parsed.initial_prompt,
        beam_size=parsed.beam_size,
        cache_dir=parsed.cache_dir,
        use_cache=not parsed.no_cache,
    )

    ref_dir = Path(parsed.reference_dir) if parsed.reference_dir else None
    report = runner.run_benchmark(
        recordings=discovered_files,
        reference_dir=ref_dir,
    )

    json_str = report.to_json(indent=2)

    if parsed.output:
        out_p = Path(parsed.output)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json_str, encoding="utf-8")
        if not parsed.quiet:
            print(f"[*] JSON report saved to: {out_p.resolve()}", file=sys.stderr)

    if not parsed.quiet:
        try:
            print(report.format_summary_table())
        except UnicodeEncodeError:
            encoding = getattr(sys.stdout, "encoding", "ascii") or "ascii"
            print(
                report.format_summary_table()
                .encode(encoding, errors="backslashreplace")
                .decode(encoding)
            )
    else:
        print(json_str)

    return 0


if __name__ == "__main__":
    sys.exit(main())
