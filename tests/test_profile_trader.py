"""Deterministic offline unit tests for scripts/profile_trader.py."""

from __future__ import annotations

import argparse
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import yaml
from pydantic import ValidationError

from scripts.profile_trader import (
    PROFILER_PROMPT_TEMPLATE,
    ExtractedTraderProfile,
    _load_groq_api_keys,
    analyze_transcripts_with_gemini,
    async_main,
    parse_args,
    update_config_yaml,
)


class TestProfileTraderCLI(unittest.TestCase):
    """Tests for CLI argument parsing in scripts/profile_trader.py."""

    def test_parse_args_defaults(self) -> None:
        args = parse_args(["--urls", "https://youtube.com/watch?v=123", "--channel-name", "TestTrader"])
        self.assertEqual(args.urls, ["https://youtube.com/watch?v=123"])
        self.assertEqual(args.channel_name, "TestTrader")
        self.assertEqual(args.config_path, "config.yaml")
        self.assertEqual(args.model, "whisper-large-v3")
        self.assertEqual(args.work_dir, "data/profiler")
        self.assertEqual(args.live_url, "")

    def test_parse_args_custom(self) -> None:
        args = parse_args([
            "--urls",
            "https://youtube.com/watch?v=1",
            "https://youtube.com/watch?v=2",
            "--channel-name",
            "Mehak Trades",
            "--live-url",
            "https://youtube.com/@Mehak/live",
            "--config-path",
            "custom_config.yaml",
            "--model",
            "whisper-large-v3",
            "--work-dir",
            "tmp/profiler",
        ])
        self.assertEqual(len(args.urls), 2)
        self.assertEqual(args.channel_name, "Mehak Trades")
        self.assertEqual(args.live_url, "https://youtube.com/@Mehak/live")
        self.assertEqual(args.config_path, "custom_config.yaml")
        self.assertEqual(args.model, "whisper-large-v3")
        self.assertEqual(args.work_dir, "tmp/profiler")


class TestProfileTraderSchemaAndPrompt(unittest.TestCase):
    """Tests validating prompt instructions and output schema enforce English keywords."""

    def test_extracted_trader_profile_model(self) -> None:
        raw_json = """{
            "channel_name": "Mehak Trades",
            "trigger_keywords": ["active", "entry", "bought", "sell", "short"],
            "exit_keywords": ["stop loss hit", "target achieved", "exit"],
            "instruments_traded": ["GOLD", "XAUUSD", "BTC"],
            "trader_profile": "Forex and Gold price action trader using order blocks."
        }"""
        profile = ExtractedTraderProfile.model_validate_json(raw_json)
        self.assertEqual(profile.channel_name, "Mehak Trades")
        self.assertEqual(profile.trigger_keywords, ["active", "entry", "bought", "sell", "short"])
        self.assertEqual(profile.exit_keywords, ["stop loss hit", "target achieved", "exit"])
        self.assertIn("GOLD", profile.instruments_traded)
        self.assertIn("order blocks", profile.trader_profile)

    def test_profiler_prompt_template_enforces_english_only(self) -> None:
        self.assertIn("English", PROFILER_PROMPT_TEMPLATE)
        self.assertIn("NEVER include Devanagari Hindi or Hinglish transliterations", PROFILER_PROMPT_TEMPLATE)
        self.assertIn("active", PROFILER_PROMPT_TEMPLATE)
        self.assertIn("entry", PROFILER_PROMPT_TEMPLATE)

    def test_schema_rejects_non_english_trigger_keywords(self) -> None:
        with self.assertRaises(ValidationError):
            ExtractedTraderProfile(
                channel_name="Trader",
                trigger_keywords=["entry", "एक्टिव करी"],
                trader_profile="Profile",
            )

    def test_gemini_keywords_are_grounded_and_client_is_closed(self) -> None:
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value.text = (
            '{"channel_name":"Trader","trigger_keywords":["active","hallucinated phrase"],'
            '"exit_keywords":[],"instruments_traded":[],"trader_profile":"Profile"}'
        )
        with patch("scripts.profile_trader.genai.Client", return_value=mock_client):
            profile = analyze_transcripts_with_gemini(
                channel_name="Trader",
                transcripts=["The trade is active now."],
                api_key="gemini_test_key",
            )

        self.assertEqual(profile.trigger_keywords, ["active"])
        mock_client.close.assert_called_once_with()


class TestProfileTraderConfigUpdate(unittest.TestCase):
    """Tests for YAML config loading, updating, and saving with English keywords."""

    def test_update_config_yaml_existing_channel(self) -> None:
        initial_config = {
            "channels": [
                {
                    "name": "Mehak Trades",
                    "url": "https://www.youtube.com/@tradewithmehaktrades/live",
                    "enabled": True,
                    "trigger_keywords": ["old_keyword"],
                    "trader_profile": "Old profile",
                },
                {
                    "name": "Other Trader",
                    "url": "https://www.youtube.com/@OtherTrader/live",
                    "enabled": False,
                    "trigger_keywords": ["other"],
                },
            ]
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            cfg_path = Path(tmp_dir) / "config.yaml"
            cfg_path.write_text(yaml.dump(initial_config), encoding="utf-8")

            new_profile = ExtractedTraderProfile(
                channel_name="Mehak Trades",
                trigger_keywords=["active", "entry", "buy", "sell"],
                exit_keywords=["exit", "target hit"],
                instruments_traded=["GOLD", "XAUUSD"],
                trader_profile="Updated Mehak profile with English keywords.",
            )

            update_config_yaml(
                config_path=cfg_path,
                channel_name="Mehak Trades",
                channel_url="",
                profile=new_profile,
            )

            loaded = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
            mehak = next(c for c in loaded["channels"] if c["name"] == "Mehak Trades")
            self.assertEqual(mehak["trigger_keywords"], ["active", "entry", "buy", "sell"])
            self.assertEqual(mehak["trader_profile"], "Updated Mehak profile with English keywords.")

            # Ensure other channel is preserved
            other = next(c for c in loaded["channels"] if c["name"] == "Other Trader")
            self.assertEqual(other["trigger_keywords"], ["other"])

    def test_update_config_yaml_new_channel(self) -> None:
        initial_config = {"channels": []}
        with tempfile.TemporaryDirectory() as tmp_dir:
            cfg_path = Path(tmp_dir) / "config.yaml"
            cfg_path.write_text(yaml.dump(initial_config), encoding="utf-8")

            new_profile = ExtractedTraderProfile(
                channel_name="New Trader",
                trigger_keywords=["active", "position"],
                exit_keywords=["exit"],
                instruments_traded=["NIFTY"],
                trader_profile="New Scalper profile.",
            )

            update_config_yaml(
                config_path=cfg_path,
                channel_name="New Trader",
                channel_url="https://youtube.com/@NewTrader/live",
                profile=new_profile,
            )

            loaded = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
            self.assertEqual(len(loaded["channels"]), 1)
            ch = loaded["channels"][0]
            self.assertEqual(ch["name"], "New Trader")
            self.assertEqual(ch["url"], "https://youtube.com/@NewTrader/live")
            self.assertEqual(ch["trigger_keywords"], ["active", "position"])

    def test_update_config_yaml_new_channel_requires_live_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            cfg_path = Path(tmp_dir) / "config.yaml"
            cfg_path.write_text("channels: []\n", encoding="utf-8")
            profile = ExtractedTraderProfile(
                channel_name="New Trader",
                trigger_keywords=["active"],
                trader_profile="Profile",
            )
            with self.assertRaisesRegex(ValueError, "--live-url"):
                update_config_yaml(cfg_path, "New Trader", "", profile)

    def test_update_config_yaml_missing_file_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            profile = ExtractedTraderProfile(
                channel_name="Trader",
                trigger_keywords=["active"],
                trader_profile="Profile",
            )
            with self.assertRaises(FileNotFoundError):
                update_config_yaml(Path(tmp_dir) / "missing.yaml", "Trader", "", profile)

    def test_atomic_replace_failure_preserves_original_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            cfg_path = Path(tmp_dir) / "config.yaml"
            original = (
                "channels:\n"
                "  - name: Trader\n"
                "    url: https://www.youtube.com/@Trader/live\n"
                "    trigger_keywords: [old]\n"
            )
            cfg_path.write_text(original, encoding="utf-8")
            profile = ExtractedTraderProfile(
                channel_name="Trader",
                trigger_keywords=["active"],
                trader_profile="Profile",
            )

            with patch(
                "scripts.profile_trader.os.replace",
                side_effect=OSError("replace failed"),
            ), self.assertRaises(OSError):
                update_config_yaml(cfg_path, "Trader", "", profile)

            self.assertEqual(cfg_path.read_text(encoding="utf-8"), original)
            self.assertFalse((cfg_path.parent / ".config.yaml.tmp").exists())


class TestProfileTraderCredentials(unittest.TestCase):
    def test_load_groq_api_keys_splits_and_deduplicates_pool(self) -> None:
        with patch.dict(
            os.environ,
            {
                "GROQ_API_KEY": "key_primary",
                "GROQ_API_KEYS": "key_pool_1, key_pool_2, key_primary",
            },
            clear=True,
        ):
            self.assertEqual(
                _load_groq_api_keys(),
                ["key_pool_1", "key_pool_2", "key_primary"],
            )


class TestProfileTraderAsyncWorkflow(unittest.IsolatedAsyncioTestCase):
    """Tests verifying async execution, credential checks, and transcriber lifecycle."""

    async def test_async_main_missing_groq_credentials_returns_error_code(self) -> None:
        args = argparse.Namespace(
            urls=["https://youtube.com/watch?v=123"],
            channel_name="Test",
            live_url="",
            config_path="config.yaml",
            model="whisper-large-v3",
            work_dir="tmp/work",
        )
        with patch.dict(os.environ, {}, clear=True):
            exit_code = await async_main(args)
            self.assertEqual(exit_code, 1)

    async def test_async_main_missing_gemini_credentials_returns_error_code(self) -> None:
        args = argparse.Namespace(
            urls=["https://youtube.com/watch?v=123"],
            channel_name="Test",
            live_url="",
            config_path="config.yaml",
            model="whisper-large-v3",
            work_dir="tmp/work",
        )
        with patch.dict(os.environ, {"GROQ_API_KEY": "gsk_valid_key"}, clear=True):
            exit_code = await async_main(args)
            self.assertEqual(exit_code, 1)

    async def test_async_main_success_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            dummy_mp3 = Path(tmp_dir) / "dummy.mp3"
            dummy_mp3.write_bytes(b"\x00" * 100)

            cfg_path = Path(tmp_dir) / "config.yaml"
            cfg_path.write_text(
                "channels:\n"
                "  - name: Sample Trader\n"
                "    url: https://www.youtube.com/@SampleTrader/live\n"
                "    trigger_keywords: []\n",
                encoding="utf-8",
            )

            args = argparse.Namespace(
                urls=["https://youtube.com/watch?v=vid1"],
                channel_name="Sample Trader",
                live_url="",
                config_path=str(cfg_path),
                model="whisper-large-v3",
                work_dir=tmp_dir,
            )

            mock_profile = ExtractedTraderProfile(
                channel_name="Sample Trader",
                trigger_keywords=["active", "entry", "bought"],
                exit_keywords=["exit"],
                instruments_traded=["GOLD"],
                trader_profile="Gold trader.",
            )

            with patch.dict(
                os.environ,
                {"GROQ_API_KEY": "gsk_test_key", "GEMINI_API_KEY": "gemini_test_key"},
                clear=True,
            ), patch(
                "scripts.profile_trader.download_audio_from_youtube",
                return_value=dummy_mp3,
            ), patch(
                "src.audio_transcriber.AudioTranscriber.transcribe_file",
                new_callable=AsyncMock,
                return_value=[{"start": 0.0, "end": 5.0, "text": "Trade is now active and entry taken."}],
            ) as mock_transcribe_file, patch(
                "src.audio_transcriber.AudioTranscriber.close",
                new_callable=AsyncMock,
            ) as mock_close, patch(
                "scripts.profile_trader.analyze_transcripts_with_gemini",
                return_value=mock_profile,
            ) as mock_analyze:
                exit_code = await async_main(args)
                self.assertEqual(exit_code, 0)
                mock_transcribe_file.assert_awaited_once_with(dummy_mp3)
                mock_close.assert_awaited_once()
                mock_analyze.assert_called_once()
                analyzed_transcripts = mock_analyze.call_args[1]["transcripts"]
                self.assertTrue(any("Trade is now active" in t for t in analyzed_transcripts))

            # Check config was updated
            updated_yaml = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
            ch = updated_yaml["channels"][0]
            self.assertEqual(ch["trigger_keywords"], ["active", "entry", "bought"])

    async def test_async_main_all_transcriptions_fail_returns_error_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            args = argparse.Namespace(
                urls=["https://youtube.com/watch?v=vid1"],
                channel_name="Sample Trader",
                live_url="",
                config_path="config.yaml",
                model="whisper-large-v3",
                work_dir=tmp_dir,
            )
            with patch.dict(
                os.environ,
                {"GROQ_API_KEY": "gsk_test_key", "GEMINI_API_KEY": "gemini_test_key"},
                clear=True,
            ), patch(
                "scripts.profile_trader.download_audio_from_youtube",
                side_effect=RuntimeError("Download failed"),
            ):
                exit_code = await async_main(args)
                self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()
