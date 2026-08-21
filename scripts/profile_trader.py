"""Offline Trader Profiler & Calibration Tool.

Analyzes past YouTube live streams of a trader, transcribes them using Faster-Whisper,
and uses Gemini to extract their unique Hindi/English/Hinglish entry trigger words into config.yaml.
"""

import argparse
import logging
import os
import sys
from pathlib import Path

import yaml
import yt_dlp
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from src.audio_transcriber import AudioTranscriber
from src.config import AudioSTTConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("profile_trader")


class ExtractedTraderProfile(BaseModel):
    """Structured profiling output from Gemini analysis."""

    channel_name: str = Field(..., description="Trader/channel name")
    trigger_keywords: list[str] = Field(
        ...,
        description="Top recurring Hindi/English entry trigger phrases spoken when taking a trade",
    )
    exit_keywords: list[str] = Field(
        default_factory=list,
        description="Phrases spoken when exiting, cutting losses, or booking profit",
    )
    instruments_traded: list[str] = Field(
        default_factory=list,
        description="Common instruments or indices discussed (e.g. NIFTY, BANKNIFTY, FINNIFTY)",
    )
    trader_profile: str = Field(
        ...,
        description="Concise description of the trader's style, commentary habits, and language patterns",
    )


PROFILER_PROMPT_TEMPLATE = """You are an expert NLP analyst studying trading commentary and live stream speech patterns.
Analyze the following transcript(s) from past live streams of the trader/channel: "{channel_name}".

Transcripts are in Hindi, English, and/or Hinglish (code-mixed Hindi/English).

Transcripts:
\"\"\"
{transcripts_text}
\"\"\"

Your Objective:
1. Extract the exact recurring spoken phrases the trader uses when they ACTUALLY ENTER OR EXECUTE A TRADE (e.g., 'enter kiya', 'humne call le liya', 'yahan pe buy banta hai, execute kiya', 'position banayi', 'buy now', 'order lagaya').
   - Include both Hindi transliterations (Devanagari/Romanized Hindi like 'le liya', 'entry ho gayi') and English terms.
   - Rank them by how definitively they signal an active execution rather than just generic chatting.
2. Extract exit/cancellation phrases ('SL cut ho gaya', 'profit book karo', 'exit maaro').
3. Extract common instruments traded (e.g., NIFTY, BANKNIFTY, CRUDE, etc.).
4. Summarize the trader's trading style and speech characteristics in 2-3 sentences.

Return ONLY a valid JSON object matching the requested schema.
"""


def download_audio_from_youtube(video_url: str, output_dir: Path) -> Path:
    """Download audio track from a YouTube video URL using yt-dlp."""
    output_dir.mkdir(parents=True, exist_ok=True)
    out_tmpl = str(output_dir / "%(id)s.%(ext)s")

    ydl_opts = {
        "format": "bestaudio/best",
        "js_runtimes": {"node": {}},
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "128",
            }
        ],
        "outtmpl": out_tmpl,
        "quiet": False,
        "no_warnings": True,
    }

    logger.info(f"Downloading audio track for {video_url}...")
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=True)
        video_id = info.get("id", "audio")
        expected_file = output_dir / f"{video_id}.mp3"
        if not expected_file.exists():
            # Search for any file with video_id prefix
            matches = list(output_dir.glob(f"{video_id}.*"))
            if matches:
                return matches[0]
        return expected_file


def analyze_transcripts_with_gemini(
    channel_name: str,
    transcripts: list[str],
    api_key: str,
    model_name: str = "gemini-3.6-flash",
) -> ExtractedTraderProfile:
    """Send aggregated transcripts to Gemini to extract trader trigger words."""
    client = genai.Client(api_key=api_key)

    combined_text = "\n\n--- NEXT STREAM ---\n\n".join(transcripts)
    # Truncate if excessively long (e.g. > 100k chars)
    if len(combined_text) > 120000:
        combined_text = combined_text[:120000] + "\n[Truncated for token budget]"

    prompt = PROFILER_PROMPT_TEMPLATE.format(
        channel_name=channel_name,
        transcripts_text=combined_text,
    )

    logger.info(f"Sending transcripts ({len(combined_text)} chars) to Gemini {model_name}...")

    config = types.GenerateContentConfig(
        temperature=0.1,
        response_mime_type="application/json",
        response_schema=ExtractedTraderProfile,
    )

    response = client.models.generate_content(
        model=model_name,
        contents=[prompt],
        config=config,
    )

    if not response or not response.text:
        raise ValueError("Empty response received from Gemini Profiler")

    return ExtractedTraderProfile.model_validate_json(response.text)


def update_config_yaml(
    config_path: Path,
    channel_name: str,
    channel_url: str,
    profile: ExtractedTraderProfile,
) -> None:
    """Update or insert channel configuration in config.yaml."""
    if not config_path.exists():
        logger.error(f"Config file {config_path} not found.")
        return

    with open(config_path, "r", encoding="utf-8") as f:
        config_data = yaml.safe_load(f) or {}

    channels = config_data.get("channels", [])
    found = False

    for ch in channels:
        if ch.get("name", "").strip().lower() == channel_name.strip().lower() or (
            channel_url and ch.get("url", "").strip() == channel_url.strip()
        ):
            ch["trigger_keywords"] = profile.trigger_keywords
            ch["trader_profile"] = profile.trader_profile
            found = True
            break

    if not found:
        new_entry = {
            "name": channel_name,
            "url": channel_url or "https://www.youtube.com/@Channel/live",
            "enabled": True,
            "trigger_keywords": profile.trigger_keywords,
            "trader_profile": profile.trader_profile,
        }
        channels.append(new_entry)

    config_data["channels"] = channels

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config_data, f, sort_keys=False, allow_unicode=True)

    logger.info(f"Updated {config_path} with {len(profile.trigger_keywords)} trigger keywords for '{channel_name}'.")


def main() -> None:
    """CLI entrypoint for trader profiling."""
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Transcribe past trader streams and extract trigger keywords with Gemini."
    )
    parser.add_argument(
        "--urls",
        nargs="+",
        required=True,
        help="List of past YouTube stream URLs to transcribe (e.g. 2 to 4 streams).",
    )
    parser.add_argument(
        "--channel-name",
        type=str,
        required=True,
        help="Name / identifier of the trader (e.g. 'Art of Trading', 'Power of Stocks').",
    )
    parser.add_argument(
        "--live-url",
        type=str,
        default="",
        help="The channel's active live stream URL to save into config.yaml.",
    )
    parser.add_argument(
        "--config-path",
        type=str,
        default="config.yaml",
        help="Path to config.yaml to update.",
    )
    parser.add_argument(
        "--model-size",
        type=str,
        default="large-v3-turbo",
        help="Faster-Whisper model size for profiling (default: large-v3-turbo).",
    )
    parser.add_argument(
        "--download-root",
        type=str,
        default="D:/huggingface_cache/whisper",
        help="Directory where Whisper model weights are stored (default: D:/huggingface_cache/whisper).",
    )

    args = parser.parse_args()

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        logger.error("GEMINI_API_KEY environment variable is required.")
        sys.exit(1)

    work_dir = Path(args.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Initializing Whisper ({args.model_size}) from {args.download_root}...")
    stt_config = AudioSTTConfig(
        model_size=args.model_size,
        device="auto",
        language="hi",
        download_root=args.download_root,
    )
    transcriber = AudioTranscriber(stt_config)

    all_transcripts: list[str] = []

    for idx, url in enumerate(args.urls, start=1):
        logger.info(f"\n--- Processing Stream {idx}/{len(args.urls)}: {url} ---")
        try:
            audio_file = download_audio_from_youtube(url, work_dir)
            segments = transcriber.transcribe_file(audio_file)
            transcript_text = " ".join(seg["text"] for seg in segments)
            all_transcripts.append(f"Stream {idx} ({url}):\n{transcript_text}")
            logger.info(f"Extracted {len(segments)} segments ({len(transcript_text)} characters).")
        except Exception as exc:  # noqa: BLE001 - per-stream transcription failure isolation
            logger.error(f"Failed to process video {url}: {exc}")

    if not all_transcripts:
        logger.error("No transcripts could be extracted from provided URLs.")
        sys.exit(1)

    logger.info("\n--- Analyzing Transcripts with Gemini ---")
    profile = analyze_transcripts_with_gemini(
        channel_name=args.channel_name,
        transcripts=all_transcripts,
        api_key=api_key,
    )

    print("\n" + "=" * 60)
    print(f"TRADER PROFILE: {profile.channel_name}")
    print("=" * 60)
    print(f"Style / Notes: {profile.trader_profile}\n")
    print("Top Entry Trigger Keywords:")
    for kw in profile.trigger_keywords:
        print(f"  • {kw}")
    print("\nExit Keywords:")
    for kw in profile.exit_keywords:
        print(f"  • {kw}")
    print(f"\nInstruments: {', '.join(profile.instruments_traded)}")
    print("=" * 60 + "\n")

    update_config_yaml(
        config_path=Path(args.config_path),
        channel_name=args.channel_name,
        channel_url=args.live_url or (args.urls[0] if args.urls else ""),
        profile=profile,
    )


if __name__ == "__main__":
    main()
