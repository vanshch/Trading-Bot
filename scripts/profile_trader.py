"""Offline Trader Profiler & Calibration Tool.

Analyzes past YouTube live streams of a trader, translates audio to English using Groq
AudioTranscriber (/audio/translations), and uses Gemini to extract English entry trigger keywords into config.yaml.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml
import yt_dlp
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field, field_validator

# Ensure project root is in sys.path when running from scripts directory
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.audio_transcriber import AudioTranscriber
from src.config import AudioSTTConfig, ChannelConfig

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
        description="Top recurring English entry trigger keywords/phrases spoken when taking a trade (from translated audio)",
    )
    exit_keywords: list[str] = Field(
        default_factory=list,
        description="English phrases spoken when exiting, cutting losses, or booking profit",
    )
    instruments_traded: list[str] = Field(
        default_factory=list,
        description="Common instruments or indices discussed (e.g. GOLD, XAUUSD, BTC, NIFTY, BANKNIFTY)",
    )
    trader_profile: str = Field(
        ...,
        description="Concise description of the trader's style, commentary habits, and trading approach",
    )

    @field_validator("trigger_keywords")
    @classmethod
    def validate_trigger_keywords(cls, values: list[str]) -> list[str]:
        """Normalize keywords and reject non-English/script output before persistence."""
        normalized: list[str] = []
        for raw_value in values:
            value = " ".join(raw_value.strip().lower().split())
            if not value:
                continue
            if len(value) > 80 or not re.fullmatch(
                r"[a-z0-9]+(?:[ '-][a-z0-9]+)*", value
            ):
                raise ValueError(
                    "trigger keywords must be concise English/ASCII words or phrases"
                )
            if value not in normalized:
                normalized.append(value)

        if not normalized:
            raise ValueError("at least one valid English trigger keyword is required")
        if len(normalized) > 50:
            raise ValueError("at most 50 trigger keywords are allowed")
        return normalized


PROFILER_PROMPT_TEMPLATE = """You are an expert NLP analyst studying trading commentary and live stream speech patterns.
Analyze the following English-translated transcript(s) from past live streams of the trader/channel: "{channel_name}".

The transcripts have already been translated into English via Whisper audio translation.

Transcripts:
\"\"\"
{transcripts_text}
\"\"\"

Your Objective:
1. Extract the exact recurring English phrases/words the trader uses when they ACTUALLY ENTER OR EXECUTE A TRADE (e.g., 'active', 'activated', 'entry', 'entered', 'bought', 'buy', 'sold', 'sell', 'short', 'long', 'position', 'executed', 'order placed').
   - Extract ONLY concise English trigger keywords/phrases.
   - NEVER include Devanagari Hindi or Hinglish transliterations (such as 'enter kiya', 'le liya', 'banao', etc.) because the live monitoring pipeline operates strictly on English translations.
   - Rank them by how definitively they signal an active trade execution rather than generic analysis or waiting.
2. Extract exit/cancellation phrases in English ('stop loss hit', 'target achieved', 'booked profit', 'closed position', 'exit').
3. Extract common instruments traded (e.g., GOLD, XAUUSD, BTC, NIFTY, BANKNIFTY, CRUDE, etc.).
4. Summarize the trader's trading style and characteristics in 2-3 sentences.

Return ONLY a valid JSON object matching the requested schema.
"""


def download_audio_from_youtube(video_url: str, output_dir: Path) -> Path:
    """Download audio track from a YouTube video URL using yt-dlp."""
    output_dir.mkdir(parents=True, exist_ok=True)
    out_tmpl = str(output_dir / "%(id)s.%(ext)s")

    ydl_opts: dict[str, Any] = {
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
        video_id = info.get("id", "audio") if isinstance(info, dict) else "audio"
        expected_file = output_dir / f"{video_id}.mp3"
        if not expected_file.exists():
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
    """Send aggregated translated transcripts to Gemini to extract English trigger keywords."""
    client = genai.Client(api_key=api_key)

    combined_text = "\n\n--- NEXT STREAM ---\n\n".join(transcripts)
    if len(combined_text) > 120000:
        combined_text = combined_text[:120000] + "\n[Truncated for token budget]"

    prompt = PROFILER_PROMPT_TEMPLATE.format(
        channel_name=channel_name,
        transcripts_text=combined_text,
    )

    logger.info(
        f"Sending transcripts ({len(combined_text)} chars) to Gemini {model_name}..."
    )

    config = types.GenerateContentConfig(
        temperature=0.1,
        response_mime_type="application/json",
        response_schema=ExtractedTraderProfile,
    )

    try:
        response = client.models.generate_content(
            model=model_name,
            contents=[prompt],
            config=config,
        )

        if not response or not response.text:
            raise ValueError("Empty response received from Gemini Profiler")

        profile = ExtractedTraderProfile.model_validate_json(response.text)
    finally:
        client.close()

    # Do not trust prompt compliance alone. Persist only keywords demonstrably
    # present in the English-translated source transcript.
    grounded_keywords: list[str] = []
    for keyword in profile.trigger_keywords:
        escaped = re.escape(keyword).replace(r"\ ", r"\s+")
        if re.search(rf"\b{escaped}\b", combined_text, re.IGNORECASE):
            grounded_keywords.append(keyword)

    if not grounded_keywords:
        raise ValueError(
            "Gemini returned no trigger keywords grounded in the translated transcripts"
        )
    return profile.model_copy(update={"trigger_keywords": grounded_keywords})


def update_config_yaml(
    config_path: Path,
    channel_name: str,
    channel_url: str,
    profile: ExtractedTraderProfile,
) -> None:
    """Update or insert channel configuration in config.yaml."""
    if not config_path.exists():
        raise FileNotFoundError(f"Config file {config_path} not found.")

    with open(config_path, "r", encoding="utf-8") as f:
        config_data = yaml.safe_load(f) or {}

    if not isinstance(config_data, dict):
        raise TypeError("config.yaml must contain a mapping at the document root")

    channels = config_data.get("channels", [])
    if not isinstance(channels, list):
        raise TypeError("config.yaml 'channels' must be a list")
    found = False

    for ch in channels:
        if ch.get("name", "").strip().lower() == channel_name.strip().lower() or (
            channel_url and ch.get("url", "").strip() == channel_url.strip()
        ):
            ch["trigger_keywords"] = profile.trigger_keywords
            ch["trader_profile"] = profile.trader_profile
            ChannelConfig.model_validate(ch)
            found = True
            break

    if not found:
        if not channel_url.strip():
            raise ValueError(
                "--live-url is required when adding a channel that is not already in config.yaml"
            )
        new_entry = {
            "name": channel_name,
            "url": channel_url,
            "enabled": True,
            "mode": "full_analysis",
            "trigger_keywords": profile.trigger_keywords,
            "trader_profile": profile.trader_profile,
        }
        channels.append(
            ChannelConfig.model_validate(new_entry).model_dump(mode="json")
        )

    config_data["channels"] = channels

    # Write atomically in the same directory with Unix LF line endings.
    dumped_yaml = yaml.dump(config_data, sort_keys=False, allow_unicode=True)
    temp_path = config_path.with_name(f".{config_path.name}.tmp")
    try:
        temp_path.write_text(dumped_yaml, encoding="utf-8", newline="\n")
        os.replace(temp_path, config_path)
    finally:
        temp_path.unlink(missing_ok=True)

    logger.info(
        f"Updated {config_path} with {len(profile.trigger_keywords)} trigger keywords for '{channel_name}'."
    )


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for the trader profiler."""
    parser = argparse.ArgumentParser(
        description="Transcribe/translate past trader streams and extract English trigger keywords with Gemini."
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
        help="Name / identifier of the trader (e.g. 'Mehak Trades', 'Naresh Cool Trader').",
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
        help="Path to config.yaml to update (default: config.yaml).",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="whisper-large-v3",
        help="Groq Whisper model name for translation (default: whisper-large-v3).",
    )
    parser.add_argument(
        "--work-dir",
        type=str,
        default="data/profiler",
        help="Directory where downloaded audio files are stored (default: data/profiler).",
    )
    return parser.parse_args(args)


def _load_groq_api_keys() -> list[str]:
    """Load and deduplicate single or comma-separated Groq credentials."""
    keys = [
        key.strip()
        for key in os.getenv("GROQ_API_KEYS", "").split(",")
        if key.strip()
    ]
    single_key = os.getenv("GROQ_API_KEY", "").strip()
    if single_key and single_key not in keys:
        keys.insert(0, single_key)
    return keys


async def async_main(args: argparse.Namespace) -> int:
    """Asynchronous profiler workflow coordinating audio download, translation, Gemini profiling, and config persistence."""
    groq_api_keys = _load_groq_api_keys()
    if not groq_api_keys:
        logger.error("GROQ_API_KEY or GROQ_API_KEYS environment variable is required.")
        return 1

    gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not gemini_api_key:
        logger.error("GEMINI_API_KEY environment variable is required.")
        return 1

    work_dir = Path(args.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    stt_config = AudioSTTConfig(model=args.model)
    async with AudioTranscriber(
        config=stt_config, api_key=groq_api_keys
    ) as transcriber:
        all_transcripts: list[str] = []

        for idx, url in enumerate(args.urls, start=1):
            logger.info(f"\n--- Processing Stream {idx}/{len(args.urls)}: {url} ---")
            try:
                audio_file = download_audio_from_youtube(url, work_dir)
                segments = await transcriber.transcribe_file(audio_file)
                transcript_text = " ".join(
                    seg["text"] for seg in segments if isinstance(seg, dict) and "text" in seg
                ).strip()
                if transcript_text:
                    all_transcripts.append(transcript_text)
                    logger.info(
                        f"Extracted {len(segments)} segment(s) ({len(transcript_text)} characters)."
                    )
                else:
                    logger.warning(f"No text transcribed from {url}.")
            except Exception as exc:  # noqa: BLE001 - per-stream transcription failure isolation
                logger.error(f"Failed to process video {url}: {exc}")

        if not all_transcripts:
            logger.error("No transcripts could be extracted from provided URLs.")
            return 1

        logger.info("\n--- Analyzing Transcripts with Gemini ---")
        profile = analyze_transcripts_with_gemini(
            channel_name=args.channel_name,
            transcripts=all_transcripts,
            api_key=gemini_api_key,
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

        try:
            update_config_yaml(
                config_path=Path(args.config_path),
                channel_name=args.channel_name,
                channel_url=args.live_url,
                profile=profile,
            )
        except (
            FileNotFoundError,
            OSError,
            TypeError,
            ValueError,
            yaml.YAMLError,
        ) as exc:
            logger.error(f"Failed to update config: {exc}")
            return 1
        return 0


def main() -> None:
    """CLI entrypoint for trader profiling."""
    load_dotenv()
    args = parse_args()
    exit_code = asyncio.run(async_main(args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
