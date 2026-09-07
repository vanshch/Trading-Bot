"""Unit tests for configuration loading, validation, and secret masking."""

import os
import tempfile
import unittest
from pathlib import Path

import yaml
from pydantic import ValidationError

from src.config import (
    AIConfig,
    CaptureConfig,
    ChannelConfig,
    ChannelMode,
    ConfigurationError,
    HSVRange,
    NewsConfig,
    ROICropConfig,
    load_config,
    mask_secret,
)


class TestConfigValidation(unittest.TestCase):
    """Tests for configuration models and fail-fast validation."""

    def test_valid_channel_config(self) -> None:
        ch = ChannelConfig(
            name="Trader One",
            url="https://www.youtube.com/@TraderOne/live",
            enabled=True,
        )
        self.assertEqual(ch.name, "Trader One")
        self.assertEqual(ch.url, "https://www.youtube.com/@TraderOne/live")
        self.assertTrue(ch.enabled)
        self.assertEqual(ch.mode, ChannelMode.FULL_ANALYSIS)

    def test_channel_config_mode_default_full_analysis(self) -> None:
        ch = ChannelConfig(
            name="Default Mode Trader",
            url="https://www.youtube.com/@DefaultMode/live",
        )
        self.assertEqual(ch.mode, ChannelMode.FULL_ANALYSIS)
        self.assertEqual(ch.mode.value, "full_analysis")

    def test_channel_config_mode_explicit_full_analysis(self) -> None:
        ch_str = ChannelConfig(
            name="Full Analysis Trader",
            url="https://www.youtube.com/@FullAnalysis/live",
            mode="full_analysis",  # type: ignore[arg-type]
        )
        self.assertEqual(ch_str.mode, ChannelMode.FULL_ANALYSIS)

        ch_enum = ChannelConfig(
            name="Full Analysis Trader Enum",
            url="https://www.youtube.com/@FullAnalysisEnum/live",
            mode=ChannelMode.FULL_ANALYSIS,
        )
        self.assertEqual(ch_enum.mode, ChannelMode.FULL_ANALYSIS)

    def test_channel_config_mode_notifications_only(self) -> None:
        ch_str = ChannelConfig(
            name="Notify Only Trader",
            url="https://www.youtube.com/@NotifyOnly/live",
            mode="notifications_only",  # type: ignore[arg-type]
        )
        self.assertEqual(ch_str.mode, ChannelMode.NOTIFICATIONS_ONLY)
        self.assertEqual(ch_str.mode.value, "notifications_only")

        ch_enum = ChannelConfig(
            name="Notify Only Trader Enum",
            url="https://www.youtube.com/@NotifyOnlyEnum/live",
            mode=ChannelMode.NOTIFICATIONS_ONLY,
        )
        self.assertEqual(ch_enum.mode, ChannelMode.NOTIFICATIONS_ONLY)

    def test_channel_config_mode_invalid_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            ChannelConfig(
                name="Bad Mode Trader",
                url="https://www.youtube.com/@BadMode/live",
                mode="unsupported_mode",  # type: ignore[arg-type]
            )

    def test_invalid_channel_url_scheme(self) -> None:
        with self.assertRaises(ValidationError):
            ChannelConfig(name="Bad Trader", url="ftp://youtube.com/live")

    def test_invalid_channel_url_domain(self) -> None:
        with self.assertRaises(ValidationError):
            ChannelConfig(name="Bad Trader", url="https://vimeo.com/123456")

    def test_rejects_youtube_text_in_untrusted_host(self) -> None:
        with self.assertRaises(ValidationError):
            ChannelConfig(
                name="Bad Trader", url="https://example.com/?next=youtube.com/live"
            )

    def test_current_default_gemini_model(self) -> None:
        self.assertEqual(AIConfig().model_name, "gemini-3.6-flash")

    def test_current_default_audio_stt_config(self) -> None:
        from src.config import AudioSTTConfig

        audio_cfg = AudioSTTConfig()
        self.assertEqual(audio_cfg.model, "whisper-large-v3")
        self.assertEqual(audio_cfg.temperature, 0.0)
        self.assertEqual(audio_cfg.chunk_duration_seconds, 10.0)
        self.assertIsNone(audio_cfg.language)
        self.assertEqual(
            audio_cfg.initial_prompt,
            "Trade active. Entry taken. Buy executed. Sell executed. Short position. Long position. Stop loss. Target hit. Order placed. Scalp. Scalping. Book profit. Move SL to CTC. Trade closed. Gold, XAUUSD, BTC, Bitcoin, Forex.",
        )

    def test_capture_restart_delay_bounds(self) -> None:
        cfg = CaptureConfig(
            restart_initial_delay_seconds=2.0,
            restart_max_delay_seconds=2.0,
        )
        self.assertEqual(cfg.restart_max_delay_seconds, 2.0)

        with self.assertRaises(ValidationError):
            CaptureConfig(
                restart_initial_delay_seconds=5.0,
                restart_max_delay_seconds=2.0,
            )

    def test_capture_provider_rate_limit_cooldown_setting(self) -> None:
        # Default is 0.0 seconds (cooldown disabled)
        default_cfg = CaptureConfig()
        self.assertEqual(default_cfg.provider_rate_limit_cooldown_seconds, 0.0)

        # Valid positive custom cooldown
        custom_cfg = CaptureConfig(provider_rate_limit_cooldown_seconds=45.0)
        self.assertEqual(custom_cfg.provider_rate_limit_cooldown_seconds, 45.0)

        # Allow zero cooldown
        zero_cfg = CaptureConfig(provider_rate_limit_cooldown_seconds=0.0)
        self.assertEqual(zero_cfg.provider_rate_limit_cooldown_seconds, 0.0)

        # Reject negative cooldown
        with self.assertRaises(ValidationError):
            CaptureConfig(provider_rate_limit_cooldown_seconds=-10.0)

    def test_capture_youtube_proxy_url_validation(self) -> None:
        self.assertIsNone(CaptureConfig().youtube_proxy_url)
        self.assertEqual(
            CaptureConfig(
                youtube_proxy_url=" socks5://127.0.0.1:40000 "
            ).youtube_proxy_url,
            "socks5://127.0.0.1:40000",
        )
        self.assertIsNone(CaptureConfig(youtube_proxy_url="  ").youtube_proxy_url)

        for invalid_url in (
            "ftp://127.0.0.1:40000",
            "socks5://",
            "socks5://user:password@127.0.0.1:40000",
        ):
            with self.subTest(invalid_url=invalid_url):
                with self.assertRaises(ValidationError):
                    CaptureConfig(youtube_proxy_url=invalid_url)

    def test_capture_internal_youtube_restart_settings(self) -> None:
        default_cfg = CaptureConfig()
        self.assertEqual(default_cfg.audio_max_consecutive_timeouts, 2)
        self.assertEqual(default_cfg.youtube_max_internal_restarts, 3)
        self.assertEqual(default_cfg.youtube_startup_timeout_seconds, 30.0)
        self.assertEqual(default_cfg.youtube_stall_timeout_seconds, 15.0)
        self.assertEqual(default_cfg.youtube_internal_restart_delay_seconds, 1.0)

        custom_cfg = CaptureConfig(
            audio_max_consecutive_timeouts=4,
            youtube_max_internal_restarts=0,
            youtube_startup_timeout_seconds=40.0,
            youtube_stall_timeout_seconds=20.0,
            youtube_internal_restart_delay_seconds=0.0,
        )
        self.assertEqual(custom_cfg.audio_max_consecutive_timeouts, 4)
        self.assertEqual(custom_cfg.youtube_max_internal_restarts, 0)
        self.assertEqual(custom_cfg.youtube_startup_timeout_seconds, 40.0)
        self.assertEqual(custom_cfg.youtube_stall_timeout_seconds, 20.0)
        self.assertEqual(custom_cfg.youtube_internal_restart_delay_seconds, 0.0)

        with self.assertRaises(ValueError):
            CaptureConfig(audio_max_consecutive_timeouts=0)
        with self.assertRaises(ValueError):
            CaptureConfig(youtube_max_internal_restarts=-1)
        with self.assertRaises(ValueError):
            CaptureConfig(youtube_startup_timeout_seconds=0)
        with self.assertRaises(ValueError):
            CaptureConfig(youtube_stall_timeout_seconds=0)
        with self.assertRaises(ValueError):
            CaptureConfig(youtube_internal_restart_delay_seconds=-1)

    def test_valid_roi_crop(self) -> None:
        roi = ROICropConfig(top=0.1, bottom=0.9, left=0.2, right=0.8)
        self.assertEqual(roi.top, 0.1)
        self.assertEqual(roi.bottom, 0.9)

    def test_invalid_roi_crop_inverted_vertical(self) -> None:
        with self.assertRaises(ValidationError):
            ROICropConfig(top=0.9, bottom=0.1, left=0.0, right=1.0)

    def test_invalid_roi_crop_inverted_horizontal(self) -> None:
        with self.assertRaises(ValidationError):
            ROICropConfig(top=0.0, bottom=1.0, left=0.8, right=0.2)

    def test_valid_hsv_range(self) -> None:
        hsv = HSVRange(lower=[95, 80, 80], upper=[135, 255, 255])
        self.assertEqual(hsv.lower, [95, 80, 80])

    def test_invalid_hsv_hue_out_of_bounds(self) -> None:
        with self.assertRaises(ValidationError):
            HSVRange(lower=[200, 80, 80], upper=[220, 255, 255])

    def test_invalid_hsv_saturation_out_of_bounds(self) -> None:
        with self.assertRaises(ValidationError):
            HSVRange(lower=[0, 300, 80], upper=[10, 255, 255])

    def test_mask_secret(self) -> None:
        self.assertEqual(mask_secret(""), "<empty>")
        self.assertEqual(mask_secret("1234"), "***")
        self.assertEqual(mask_secret("my_super_secret_api_key_12345"), "my_...345")

    def test_valid_news_config_defaults(self) -> None:
        cfg = NewsConfig()
        self.assertTrue(cfg.enabled)
        self.assertEqual(cfg.schedule_times, ["08:00", "14:00"])
        self.assertEqual(cfg.timezone, "Asia/Kolkata")
        self.assertEqual(cfg.impact_levels, ["High"])
        self.assertTrue(cfg.pre_event_alert_enabled)
        self.assertEqual(cfg.pre_event_minutes, 30)
        self.assertEqual(cfg.poll_interval_seconds, 30.0)

    def test_invalid_news_config_schedule_times(self) -> None:
        # Invalid format
        with self.assertRaises(ValidationError):
            NewsConfig(schedule_times=["8am"])

        # Invalid hours
        with self.assertRaises(ValidationError):
            NewsConfig(schedule_times=["25:00"])

        # Invalid minutes
        with self.assertRaises(ValidationError):
            NewsConfig(schedule_times=["12:65"])

        # Empty schedule
        with self.assertRaises(ValidationError):
            NewsConfig(schedule_times=[])

    def test_invalid_news_config_pre_event_settings(self) -> None:
        # Invalid pre_event_minutes (< 1 or > 180)
        with self.assertRaises(ValidationError):
            NewsConfig(pre_event_minutes=0)
        with self.assertRaises(ValidationError):
            NewsConfig(pre_event_minutes=200)

        # Invalid poll_interval_seconds (< 5.0 or > 300.0)
        with self.assertRaises(ValidationError):
            NewsConfig(poll_interval_seconds=2.0)
        with self.assertRaises(ValidationError):
            NewsConfig(poll_interval_seconds=400.0)



class TestLoadConfig(unittest.TestCase):
    """Tests for loading and parsing config from YAML and environment."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temp_dir.name) / "test_config.yaml"
        self.env_path = Path(self.temp_dir.name) / ".env"

        self.valid_yaml = {
            "channels": [
                {
                    "name": "Trader Test",
                    "url": "https://www.youtube.com/@Test/live",
                    "enabled": True,
                }
            ],
            "capture": {
                "frame_interval_seconds": 3.0,
                "live_poll_interval_seconds": 30.0,
            },
            "ai": {"model_name": "gemini-3.6-flash", "confidence_threshold": 0.75},
            "telegram": {"include_screenshot": True, "caption_timezone": "UTC"},
        }
        with open(self.config_path, "w", encoding="utf-8") as f:
            yaml.dump(self.valid_yaml, f)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_load_config_success(self) -> None:
        with open(self.env_path, "w", encoding="utf-8") as f:
            f.write("GROQ_API_KEY=test_groq_key_12345\n")
            f.write("GEMINI_API_KEY=test_gemini_key_12345\n")
            f.write(
                "TELEGRAM_BOT_TOKEN=123456789:ABCDefGhIjKlMnOpQrStUvWxYz_12345678\n"
            )
            f.write("TELEGRAM_CHAT_ID=987654321\n")
            f.write("LOG_LEVEL=DEBUG\n")

        config = load_config(yaml_path=self.config_path, env_path=self.env_path)
        self.assertEqual(len(config.channels), 1)
        self.assertEqual(config.channels[0].name, "Trader Test")
        self.assertEqual(config.capture.frame_interval_seconds, 3.0)
        self.assertEqual(config.groq_api_key.get_secret_value(), "test_groq_key_12345")
        self.assertEqual(
            config.gemini_api_key.get_secret_value(), "test_gemini_key_12345"
        )
        self.assertEqual(config.telegram_chat_id, "987654321")
        self.assertEqual(config.log_level, "DEBUG")

    def test_load_config_missing_yaml_file(self) -> None:
        non_existent = Path(self.temp_dir.name) / "non_existent.yaml"
        with self.assertRaises(ConfigurationError) as ctx:
            load_config(yaml_path=non_existent, env_path=self.env_path)
        self.assertIn("not found", str(ctx.exception).lower())

    def test_load_config_missing_groq_key(self) -> None:
        with open(self.env_path, "w", encoding="utf-8") as f:
            f.write("GEMINI_API_KEY=test_gemini_key_12345\n")
            f.write(
                "TELEGRAM_BOT_TOKEN=123456789:ABCDefGhIjKlMnOpQrStUvWxYz_12345678\n"
            )
            f.write("TELEGRAM_CHAT_ID=987654321\n")

        os.environ.pop("GROQ_API_KEY", None)
        os.environ["GEMINI_API_KEY"] = "test_gemini_key_12345"
        os.environ["TELEGRAM_BOT_TOKEN"] = (
            "123456789:ABCDefGhIjKlMnOpQrStUvWxYz_12345678"
        )
        os.environ["TELEGRAM_CHAT_ID"] = "987654321"

        with self.assertRaises(ConfigurationError) as ctx:
            load_config(yaml_path=self.config_path, env_path=self.env_path)
        self.assertIn("GROQ_API_KEY", str(ctx.exception))

    def test_load_config_missing_gemini_key(self) -> None:
        with open(self.env_path, "w", encoding="utf-8") as f:
            f.write("GROQ_API_KEY=test_groq_key_12345\n")
            f.write(
                "TELEGRAM_BOT_TOKEN=123456789:ABCDefGhIjKlMnOpQrStUvWxYz_12345678\n"
            )
            f.write("TELEGRAM_CHAT_ID=987654321\n")

        # Ensure env does not have GEMINI_API_KEY
        os.environ["GROQ_API_KEY"] = "test_groq_key_12345"
        os.environ.pop("GEMINI_API_KEY", None)
        os.environ["TELEGRAM_BOT_TOKEN"] = (
            "123456789:ABCDefGhIjKlMnOpQrStUvWxYz_12345678"
        )
        os.environ["TELEGRAM_CHAT_ID"] = "987654321"

        with self.assertRaises(ConfigurationError) as ctx:
            load_config(yaml_path=self.config_path, env_path=self.env_path)
        self.assertIn("GEMINI_API_KEY", str(ctx.exception))

    def test_load_config_missing_telegram_token(self) -> None:
        with open(self.env_path, "w", encoding="utf-8") as f:
            f.write("GROQ_API_KEY=test_groq_key_12345\n")
            f.write("GEMINI_API_KEY=test_gemini_key_12345\n")
            f.write("TELEGRAM_CHAT_ID=987654321\n")

        os.environ["GROQ_API_KEY"] = "test_groq_key_12345"
        os.environ["GEMINI_API_KEY"] = "test_gemini_key_12345"
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        os.environ["TELEGRAM_CHAT_ID"] = "987654321"

        with self.assertRaises(ConfigurationError) as ctx:
            load_config(yaml_path=self.config_path, env_path=self.env_path)
        self.assertIn("TELEGRAM_BOT_TOKEN", str(ctx.exception))

    def test_load_config_invalid_yaml_syntax(self) -> None:
        bad_yaml = Path(self.temp_dir.name) / "bad.yaml"
        with open(bad_yaml, "w", encoding="utf-8") as f:
            f.write("channels: [unclosed_bracket")

        with self.assertRaises(ConfigurationError) as ctx:
            load_config(yaml_path=bad_yaml, env_path=self.env_path)
        self.assertIn("failed to parse yaml", str(ctx.exception).lower())

    def test_load_config_with_comma_separated_groq_keys(self) -> None:
        with open(self.env_path, "w", encoding="utf-8") as f:
            f.write("GROQ_API_KEYS=key1_abc, key2_xyz\n")
            f.write("GEMINI_API_KEY=test_gemini_key_12345\n")
            f.write(
                "TELEGRAM_BOT_TOKEN=123456789:ABCDefGhIjKlMnOpQrStUvWxYz_12345678\n"
            )
            f.write("TELEGRAM_CHAT_ID=987654321\n")

        os.environ.pop("GROQ_API_KEY", None)
        config = load_config(yaml_path=self.config_path, env_path=self.env_path)
        self.assertEqual(config.groq_api_key.get_secret_value(), "key1_abc")
        self.assertEqual(len(config.groq_api_keys), 2)
        self.assertEqual(
            [k.get_secret_value() for k in config.groq_api_keys],
            ["key1_abc", "key2_xyz"],
        )

    def test_load_config_with_channel_routing_modes(self) -> None:
        mixed_yaml = {
            "channels": [
                {
                    "name": "Mehak Trades",
                    "url": "https://www.youtube.com/@tradewithmehaktrades/live",
                    "enabled": True,
                    "mode": "full_analysis",
                    "trigger_keywords": ["trade active", "enter kiya"],
                    "trader_profile": "Forex/Gold scalper",
                },
                {
                    "name": "Naresh Cool Trader",
                    "url": "https://www.youtube.com/@NareshCoolTrader/live",
                    "enabled": True,
                    "mode": "notifications_only",
                    "trigger_keywords": ["call liya"],
                },
                {
                    "name": "Legacy Mode Trader",
                    "url": "https://www.youtube.com/@Legacy/live",
                    "enabled": False,
                },
            ],
            "capture": {"frame_interval_seconds": 3.0},
            "ai": {"model_name": "gemini-3.6-flash"},
            "telegram": {"include_screenshot": True},
        }
        mixed_config_path = Path(self.temp_dir.name) / "mixed_config.yaml"
        with open(mixed_config_path, "w", encoding="utf-8") as f:
            yaml.dump(mixed_yaml, f)

        with open(self.env_path, "w", encoding="utf-8") as f:
            f.write("GROQ_API_KEY=test_groq_key_12345\n")
            f.write("GEMINI_API_KEY=test_gemini_key_12345\n")
            f.write(
                "TELEGRAM_BOT_TOKEN=123456789:ABCDefGhIjKlMnOpQrStUvWxYz_12345678\n"
            )
            f.write("TELEGRAM_CHAT_ID=987654321\n")

        config = load_config(yaml_path=mixed_config_path, env_path=self.env_path)
        self.assertEqual(len(config.channels), 3)
        self.assertEqual(config.channels[0].mode, ChannelMode.FULL_ANALYSIS)
        self.assertEqual(config.channels[1].mode, ChannelMode.NOTIFICATIONS_ONLY)
        self.assertEqual(config.channels[2].mode, ChannelMode.FULL_ANALYSIS)

    def test_repo_config_yaml_structure_and_modes(self) -> None:
        repo_config_path = Path("config.yaml")
        self.assertTrue(repo_config_path.exists(), "Root config.yaml must exist")

        with open(repo_config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        raw_channels = raw.get("channels", [])
        self.assertEqual(len(raw_channels), 4)

        channels_by_name = {ch["name"]: ch for ch in raw_channels}
        self.assertIn("Sample Trader 1", channels_by_name)
        self.assertIn("Sample Trader 2", channels_by_name)
        self.assertIn("Mehak Trades", channels_by_name)
        self.assertIn("Naresh Cool Trader", channels_by_name)

        # Sample channels must be disabled
        self.assertFalse(channels_by_name["Sample Trader 1"]["enabled"])
        self.assertFalse(channels_by_name["Sample Trader 2"]["enabled"])

        # Mehak Trades must be enabled with full_analysis
        mehak = ChannelConfig.model_validate(channels_by_name["Mehak Trades"])
        self.assertTrue(mehak.enabled)
        self.assertEqual(mehak.mode, ChannelMode.FULL_ANALYSIS)
        self.assertEqual(
            mehak.url, "https://www.youtube.com/@tradewithmehaktrades/live"
        )
        self.assertGreater(len(mehak.trigger_keywords), 0)
        self.assertIn("active", mehak.trigger_keywords)
        self.assertIn("entry", mehak.trigger_keywords)
        self.assertIn("buy", mehak.trigger_keywords)
        self.assertIn("sell", mehak.trigger_keywords)
        self.assertIn("short", mehak.trigger_keywords)
        self.assertIn("long", mehak.trigger_keywords)
        self.assertIsNotNone(mehak.trader_profile)

        # Naresh Cool Trader must be enabled with notifications_only
        naresh = ChannelConfig.model_validate(channels_by_name["Naresh Cool Trader"])
        self.assertTrue(naresh.enabled)
        self.assertEqual(naresh.mode, ChannelMode.NOTIFICATIONS_ONLY)
        self.assertEqual(naresh.url, "https://www.youtube.com/@NareshCoolTrader/live")
        self.assertGreater(len(naresh.trigger_keywords), 0)
        self.assertIsNotNone(naresh.trader_profile)

        audio_prompt = raw["audio"]["initial_prompt"]
        self.assertIn("XAUUSD", audio_prompt)
        self.assertIn("Gold", audio_prompt)
        self.assertIn("Scalp", audio_prompt)
        self.assertIn("Book profit", audio_prompt)
        self.assertIn("Move SL to CTC", audio_prompt)
        self.assertNotIn("BankNifty", audio_prompt)


if __name__ == "__main__":
    unittest.main()
