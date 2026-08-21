"""Compare production-style local STT against Gemini on a recorded audio clip."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import sys
import time
import wave
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

try:
    import resource
except ImportError:
    resource = None

# Ensure project root is in sys.path when running from scripts/ directory
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from google import genai
from google.genai import types

from src.audio_transcriber import AudioTranscriber
from src.audio_trigger import AudioTriggerDetector
from src.config import load_config

CONCEPT_VARIANTS = {
    "entry": ("entry", "enter", "एंट्री", "एंटर"),
    "trade": ("trade", "ट्रेड"),
    "buy": ("buy", "बाय"),
    "sell": ("sell", "selling", "सेल", "सेलिंग"),
    "order": ("order", "ऑर्डर"),
    "stop_loss": ("stop loss", "sl", "स्टॉप लॉस"),
    "target": ("target", "tgt", "टारगेट"),
    "gold": ("gold", "xauusd", "गोल्ड"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wav", required=True, type=Path)
    parser.add_argument("--gemini-audio", required=True, type=Path)
    parser.add_argument("--config", default="config.yaml", type=Path)
    parser.add_argument("--env", default=".env", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--youtube-json3", type=Path)
    parser.add_argument("--source-start-seconds", type=float, default=0.0)
    return parser.parse_args()


def normalize_tokens(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", text.casefold(), flags=re.UNICODE)


def compare_text(left: str, right: str) -> dict[str, float | int]:
    left_tokens = normalize_tokens(left)
    right_tokens = normalize_tokens(right)
    left_unique = set(left_tokens)
    right_unique = set(right_tokens)
    union = left_unique | right_unique
    return {
        "left_words": len(left_tokens),
        "right_words": len(right_tokens),
        "sequence_similarity": round(
            SequenceMatcher(None, left_tokens, right_tokens, autojunk=False).ratio(),
            4,
        ),
        "unique_word_jaccard": round(
            len(left_unique & right_unique) / len(union) if union else 1.0,
            4,
        ),
    }


def concept_counts(text: str) -> dict[str, int]:
    lowered = text.casefold()
    return {
        concept: sum(lowered.count(variant.casefold()) for variant in variants)
        for concept, variants in CONCEPT_VARIANTS.items()
    }


def extract_youtube_reference(
    json_path: Path,
    start_seconds: float,
    duration_seconds: float,
) -> str:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    end_seconds = start_seconds + duration_seconds
    entries: list[str] = []
    for event in data.get("events", []):
        timestamp = float(event.get("tStartMs", 0)) / 1000
        if not start_seconds <= timestamp < end_seconds:
            continue
        text = "".join(
            segment.get("utf8", "") for segment in event.get("segs", [])
        ).strip()
        if text:
            entries.append(text)
    return " ".join(entries)


async def transcribe_local_chunks(
    wav_path: Path,
    transcriber: AudioTranscriber,
    trigger_detector: AudioTriggerDetector,
    chunk_seconds: float,
) -> tuple[str, list[dict[str, Any]], dict[str, Any], float]:
    segments: list[dict[str, Any]] = []
    chunk_times: list[float] = []
    transcript_parts: list[str] = []

    with wave.open(str(wav_path), "rb") as audio:
        sample_rate = audio.getframerate()
        channels = audio.getnchannels()
        sample_width = audio.getsampwidth()
        total_frames = audio.getnframes()
        if (sample_rate, channels, sample_width) != (16000, 1, 2):
            raise ValueError(
                "WAV must be 16 kHz mono 16-bit PCM; got "
                f"{sample_rate} Hz, {channels} channel(s), {sample_width * 8}-bit"
            )
        duration_seconds = total_frames / sample_rate
        frames_per_chunk = int(sample_rate * chunk_seconds)

        started = time.perf_counter()
        index = 0
        while True:
            pcm = audio.readframes(frames_per_chunk)
            if not pcm:
                break
            chunk_started = time.perf_counter()
            text = await transcriber.transcribe_pcm_chunk(pcm, sample_rate=sample_rate)
            elapsed = time.perf_counter() - chunk_started
            chunk_times.append(elapsed)
            triggered, keyword = trigger_detector.check_trigger(text)
            start = index * chunk_seconds
            record = {
                "index": index,
                "start_seconds": start,
                "end_seconds": min(start + chunk_seconds, duration_seconds),
                "processing_seconds": round(elapsed, 4),
                "text": text,
                "triggered": triggered,
                "matched_keyword": keyword,
            }
            segments.append(record)
            if text:
                transcript_parts.append(text)
            print(
                f"local chunk {index + 1}: {elapsed:.2f}s, "
                f"words={len(text.split())}, trigger={keyword or '-'}",
                flush=True,
            )
            index += 1
        total_elapsed = time.perf_counter() - started

    sorted_times = sorted(chunk_times)
    percentile_index = max(0, int(len(sorted_times) * 0.95) - 1)
    metrics = {
        "audio_duration_seconds": round(duration_seconds, 3),
        "chunks": len(segments),
        "nonempty_chunks": sum(bool(segment["text"]) for segment in segments),
        "triggered_chunks": sum(bool(segment["triggered"]) for segment in segments),
        "total_processing_seconds": round(total_elapsed, 3),
        "real_time_factor": round(total_elapsed / duration_seconds, 4),
        "first_chunk_seconds": round(chunk_times[0], 4) if chunk_times else 0.0,
        "median_chunk_seconds": round(statistics.median(chunk_times), 4)
        if chunk_times
        else 0.0,
        "p95_chunk_seconds": round(sorted_times[percentile_index], 4)
        if chunk_times
        else 0.0,
        "max_chunk_seconds": round(max(chunk_times), 4) if chunk_times else 0.0,
    }
    return " ".join(transcript_parts), segments, metrics, duration_seconds


def transcribe_gemini(
    audio_path: Path,
    api_key: str,
    model_name: str,
    timeout_seconds: float,
) -> tuple[str, float, str]:
    client = genai.Client(api_key=api_key)
    prompt = (
        "Transcribe this trading livestream audio verbatim. The speech is code-mixed "
        "Hindi and English. Preserve Hindi in Devanagari, retain English trading terms "
        "such as entry, buy, sell, order block, stop loss, target, Gold, BTC and XAUUSD "
        "in Latin script where spoken, and do not summarize. Return only the transcript."
    )
    audio_part = types.Part.from_bytes(
        data=audio_path.read_bytes(), mime_type="audio/mpeg"
    )
    started = time.perf_counter()
    try:
        response = client.models.generate_content(
            model=model_name,
            contents=[prompt, audio_part],
            config=types.GenerateContentConfig(
                temperature=0.0,
                max_output_tokens=8192,
                http_options=types.HttpOptions(timeout=int(timeout_seconds * 1000)),
            ),
        )
        elapsed = time.perf_counter() - started
        text = response.text or ""
        finish_reason = "UNKNOWN"
        if response.candidates:
            finish_reason = str(response.candidates[0].finish_reason)
        return text.strip(), elapsed, finish_reason
    finally:
        client.close()


async def async_main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    config = load_config(args.config, args.env)
    channel = next(channel for channel in config.channels if channel.enabled)

    transcriber = AudioTranscriber(
        config.audio, api_key=config.groq_api_keys or config.groq_api_key
    )
    try:
        trigger_detector = AudioTriggerDetector(
            trigger_keywords=channel.trigger_keywords,
            cooldown_seconds=0.0,
        )
        local_text, segments, local_metrics, duration_seconds = await transcribe_local_chunks(
            args.wav,
            transcriber,
            trigger_detector,
            config.audio.chunk_duration_seconds,
        )
    finally:
        await transcriber.close()
    (args.output_dir / "local_whisper.txt").write_text(
        local_text, encoding="utf-8"
    )
    (args.output_dir / "local_segments.json").write_text(
        json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    gemini_text, gemini_seconds, finish_reason = transcribe_gemini(
        args.gemini_audio,
        config.gemini_api_key.get_secret_value(),
        config.ai.model_name,
        max(config.ai.timeout_seconds, 300.0),
    )
    (args.output_dir / "gemini.txt").write_text(gemini_text, encoding="utf-8")

    report: dict[str, Any] = {
        "source": {
            "wav": str(args.wav),
            "gemini_audio": str(args.gemini_audio),
            "source_start_seconds": args.source_start_seconds,
            "duration_seconds": round(duration_seconds, 3),
        },
        "local_whisper": {
            **local_metrics,
            "concept_counts": concept_counts(local_text),
        },
        "gemini": {
            "model": config.ai.model_name,
            "processing_seconds": round(gemini_seconds, 3),
            "finish_reason": finish_reason,
            "concept_counts": concept_counts(gemini_text),
        },
        "local_vs_gemini": compare_text(local_text, gemini_text),
        "max_rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if resource
        else 0,
    }

    if args.youtube_json3:
        youtube_text = extract_youtube_reference(
            args.youtube_json3,
            args.source_start_seconds,
            duration_seconds,
        )
        (args.output_dir / "youtube_auto.txt").write_text(
            youtube_text, encoding="utf-8"
        )
        report["youtube_auto"] = {
            "concept_counts": concept_counts(youtube_text),
        }
        report["local_vs_youtube"] = compare_text(local_text, youtube_text)
        report["gemini_vs_youtube"] = compare_text(gemini_text, youtube_text)

    (args.output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    import sys
    if "--benchmark" in sys.argv or "--recordings-dir" in sys.argv or ("--wav" not in sys.argv and any(arg in sys.argv for arg in ("--inputs", "-i", "--limit", "-n"))):
        from src.whisper_benchmark import main as benchmark_main
        clean_args = [a for a in sys.argv[1:] if a != "--benchmark"]
        sys.exit(benchmark_main(clean_args))
    asyncio.run(async_main())
