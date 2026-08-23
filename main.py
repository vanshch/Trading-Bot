"""CLI entrypoint and asynchronous orchestrator for YouTube Live Stream Trade Monitoring Bot.

Features configurable real-time audio chunk transcription with Groq STT (whisper-large-v3-turbo), rolling context tracking,
keyword trigger gating, multimodal Gemini verification (screenshot + transcript), and fast Telegram alerts.
"""

import argparse
import asyncio
import signal
import sys
import time

import cv2

from src.audio_stream import AudioStreamManager
from src.audio_transcriber import AudioTranscriber
from src.audio_trigger import AudioTriggerDetector, RollingTranscriptBuffer
from src.config import (
    AppConfig,
    ChannelConfig,
    ChannelMode,
    ConfigurationError,
    load_config,
)
from src.live_alert_state import LiveAlertStateStore
from src.live_listener import LiveChannelListener
from src.logger import get_channel_logger, register_secret_for_redaction, setup_logger
from src.multimodal_verifier import MultimodalVerifier
from src.notifier import TelegramNotifier
from src.session_logger import LiveSessionLogger
from src.state_tracker import StateTransition, TradeStateTracker
from src.stream_manager import StreamManager, StreamStatus
from src.vision_verifier import TradeVerification


class TradeMonitoringService:
    """Core orchestrator managing channel monitoring tasks, audio STT, and multimodal AI lifecycle."""

    def __init__(
        self,
        config: AppConfig,
        test_duration_seconds: float | None = None,
        session_logger: LiveSessionLogger | None = None,
        live_alert_state: LiveAlertStateStore | None = None,
    ) -> None:
        self.config = config
        self.test_duration_seconds = test_duration_seconds
        self.logger = setup_logger("trade_bot", log_level=config.log_level)

        # Register credentials with secret redactor
        groq_keys = config.groq_api_keys or (
            [config.groq_api_key] if config.groq_api_key else []
        )
        for k in groq_keys:
            register_secret_for_redaction(k.get_secret_value())
        register_secret_for_redaction(config.gemini_api_key.get_secret_value())
        register_secret_for_redaction(config.telegram_bot_token.get_secret_value())

        # Initialize shared components
        self.multimodal_verifier = MultimodalVerifier(
            api_key=config.gemini_api_key.get_secret_value(),
            config=config.ai,
        )
        self.transcriber = AudioTranscriber(
            config=config.audio,
            api_key=groq_keys,
        )
        self.state_tracker = TradeStateTracker(miss_threshold=2)
        self.session_logger = session_logger or LiveSessionLogger()
        self.live_alert_state = live_alert_state or LiveAlertStateStore()
        self.notifier = TelegramNotifier(
            bot_token=config.telegram_bot_token.get_secret_value(),
            chat_id=config.telegram_chat_id,
            config=config.telegram,
        )

        self._tasks: list[asyncio.Task] = []
        self._live_listeners: list[LiveChannelListener] = []
        self._stream_managers: list[StreamManager] = []
        self._audio_stream_managers: list[AudioStreamManager] = []
        self._stop_event = asyncio.Event()

    async def monitor_channel(self, channel: ChannelConfig) -> None:
        """Asynchronous monitoring loop for an individual channel using real-time audio STT & multimodal AI."""
        ch_logger = get_channel_logger(channel.name, "INIT", self.logger)
        ch_logger.info(f"Starting monitoring loop for URL: {channel.url}")

        stream_mgr = StreamManager(channel_url=channel.url, config=self.config.capture)
        self._stream_managers.append(stream_mgr)

        poll_interval = self.config.capture.live_poll_interval_seconds
        chunk_duration = self.config.audio.chunk_duration_seconds
        context_buffer = RollingTranscriptBuffer(
            max_duration_seconds=self.config.audio.context_buffer_seconds
        )
        trigger_detector = AudioTriggerDetector(
            trigger_keywords=channel.trigger_keywords,
            cooldown_seconds=20.0,
        )
        stream_url: str | None = None

        try:
            while not self._stop_event.is_set():
                # 1. Check if stream is live
                if not stream_mgr.is_live:
                    ch_logger = get_channel_logger(channel.name, "OFFLINE", self.logger)
                    ch_logger.debug(
                        f"Polling channel live status (interval {poll_interval}s)..."
                    )

                    (
                        stream_status,
                        resolved_stream_url,
                    ) = await stream_mgr.check_live_status()
                    if stream_status != StreamStatus.LIVE or not resolved_stream_url:
                        if stream_status == StreamStatus.OFFLINE:
                            # Only an explicit offline result may advance trade closure.
                            transition, trade_info = (
                                self.state_tracker.process_verification(
                                    channel.name,
                                    TradeVerification(
                                        is_trade_active=False,
                                        direction="NONE",
                                        reasoning="Stream went offline",
                                    ),
                                )
                            )
                            if (
                                transition == StateTransition.TRADE_CLOSED
                                and trade_info
                            ):
                                ch_logger.info(
                                    "Active trade concluded as stream ended."
                                )
                                await self.notifier.send_trade_closed_alert(
                                    trade_info, channel.url
                                )
                        else:
                            ch_logger.debug(
                                "Live status is temporarily unknown; preserving "
                                "trade state and retrying."
                            )

                        try:
                            await asyncio.wait_for(
                                self._stop_event.wait(), timeout=poll_interval
                            )
                            break
                        except asyncio.TimeoutError:
                            continue
                    stream_url = resolved_stream_url
                    ch_logger = get_channel_logger(channel.name, "LIVE", self.logger)
                    ch_logger.info(
                        "Stream is now live; starting real-time audio capture..."
                    )

                # 2. Start Real-time Audio Stream Capture
                direct_url = stream_mgr.current_stream_url or stream_url
                if not direct_url:
                    await asyncio.sleep(2.0)
                    continue

                audio_input_url = (
                    f"https://www.youtube.com/watch?v={stream_mgr.broadcast_id}"
                    if stream_mgr.broadcast_id
                    else (channel.url or direct_url)
                )

                audio_mgr = AudioStreamManager(
                    stream_url=audio_input_url,
                    chunk_duration_seconds=chunk_duration,
                )
                self._audio_stream_managers.append(audio_mgr)

                session_rec = self.session_logger.start_session(
                    channel_name=channel.name,
                    channel_url=channel.url,
                )
                stream_start_time = time.time()
                ch_logger.info(
                    f"Listening to live audio stream in {chunk_duration:g}s chunks (session {session_rec.session_id})..."
                )

                session_status = "INTERRUPTED"
                session_error: str | None = None
                try:
                    async for audio_chunk in audio_mgr.stream_audio_chunks(
                        stop_event=self._stop_event
                    ):
                        if self._stop_event.is_set():
                            break

                        self.session_logger.record_chunk(session_rec.session_id)

                        # Transcribe in memory (ultra-fast Groq Whisper inference)
                        text = await self.transcriber.transcribe_pcm_chunk(audio_chunk)

                        if text:
                            context_buffer.append(text)
                            ch_logger.info(f"[LIVE STT {chunk_duration:g}s]: {text}")
                        else:
                            ch_logger.info(
                                f"[LIVE STT {chunk_duration:g}s]: (ambient / chart music)"
                            )

                        if text:
                            # Check for trade entry trigger keywords
                            is_triggered, matched_kw = trigger_detector.check_trigger(
                                text
                            )

                            if is_triggered:
                                self.session_logger.record_trigger(
                                    session_rec.session_id
                                )
                                ch_logger.info(
                                    f"AUDIO TRIGGER DETECTED: '{matched_kw}' | Spoken: '{text}'"
                                )
                                ch_logger.info(
                                    "Capturing live chart screenshot for Gemini multimodal verification..."
                                )

                                # Capture current live frame immediately
                                frame = await stream_mgr.capture_frame()
                                frame_jpeg_bytes: bytes | None = None
                                if frame is not None:
                                    success, enc = cv2.imencode(
                                        ".jpg",
                                        frame,
                                        [int(cv2.IMWRITE_JPEG_QUALITY), 90],
                                    )
                                    if success:
                                        frame_jpeg_bytes = enc.tobytes()

                                if frame_jpeg_bytes is None:
                                    ch_logger.warning(
                                        "Could not capture screenshot; verifying with spoken transcript alone..."
                                    )
                                    # Fallback placeholder 1x1 image for multimodal API
                                    import numpy as np

                                    placeholder = np.zeros(
                                        (100, 100, 3), dtype=np.uint8
                                    )
                                    _, enc = cv2.imencode(".jpg", placeholder)
                                    frame_jpeg_bytes = enc.tobytes()

                                # Verify with Gemini Multimodal
                                recent_context = context_buffer.get_context_text()
                                verification = await self.multimodal_verifier.verify_multimodal_trade(
                                    frame=frame_jpeg_bytes,
                                    transcript_context=recent_context,
                                    trigger_phrase=matched_kw or text,
                                    trader_profile=channel.trader_profile or "",
                                )

                                ch_logger.info(
                                    f"Gemini Multimodal Result: is_active={verification.is_trade_active}, "
                                    f"dir={verification.direction}, inst={verification.instrument}, "
                                    f"conf={verification.confidence:.2f}"
                                )

                                transition, trade = (
                                    self.state_tracker.process_verification(
                                        channel_name=channel.name,
                                        verification=verification,
                                        frame_bytes=frame_jpeg_bytes,
                                    )
                                )

                                if verification.is_trade_active:
                                    if (
                                        transition == StateTransition.NEW_TRADE
                                        and trade
                                    ):
                                        ch_logger.info(
                                            f"DISPATCHING ALERT: {trade.direction} on {trade.instrument}"
                                        )
                                        elapsed_stream_sec = max(
                                            0.0, time.time() - stream_start_time
                                        )
                                        alert_sent = await self.notifier.send_fast_audio_trade_alert(
                                            channel_name=channel.name,
                                            verification=verification,
                                            stream_url=channel.url,
                                            frame_bytes=frame_jpeg_bytes,
                                            elapsed_seconds=elapsed_stream_sec,
                                        )
                                        self.session_logger.record_trade(
                                            session_rec.session_id, alerted=alert_sent
                                        )
                                        if not alert_sent:
                                            ch_logger.error(
                                                "Trade was detected, but its Telegram alert could not be delivered."
                                            )
                                    elif transition == StateTransition.STILL_ACTIVE:
                                        ch_logger.debug(
                                            "Trade already active. Suppressing duplicate alert."
                                        )
                                else:
                                    ch_logger.info(
                                        f"Gemini rejected trade execution: {verification.reasoning}"
                                    )
                                    if (
                                        transition == StateTransition.TRADE_CLOSED
                                        and trade
                                    ):
                                        ch_logger.info(
                                            "Gemini confirmed that the active trade concluded."
                                        )
                                        await self.notifier.send_trade_closed_alert(
                                            trade, channel.url
                                        )

                except asyncio.CancelledError:
                    session_error = "Monitoring task cancelled"
                    raise
                except Exception as exc:  # noqa: BLE001 - stream/provider boundary
                    session_error = str(exc)
                    ch_logger.warning(
                        f"Audio stream interrupted: {exc}. Re-verifying stream..."
                    )
                    if not self._stop_event.is_set():
                        await stream_mgr.check_live_status()
                else:
                    if self._stop_event.is_set():
                        session_error = "Monitoring service stopped"
                    else:
                        stream_status, _ = await stream_mgr.check_live_status()
                        if stream_status == StreamStatus.LIVE:
                            session_error = (
                                "Audio stream ended while channel was still live"
                            )
                        elif stream_status == StreamStatus.OFFLINE:
                            session_status = "COMPLETED"
                        else:
                            session_error = "Audio stream ended while live status was temporarily unknown"
                finally:
                    self.session_logger.end_session(
                        session_rec.session_id,
                        status=session_status,
                        error=session_error,
                    )
                    await audio_mgr.stop()
                    if audio_mgr in self._audio_stream_managers:
                        self._audio_stream_managers.remove(audio_mgr)

                if session_status == "INTERRUPTED" and not self._stop_event.is_set():
                    retry_delay = min(poll_interval, 5.0)
                    try:
                        await asyncio.wait_for(
                            self._stop_event.wait(), timeout=retry_delay
                        )
                    except asyncio.TimeoutError:
                        pass

        except asyncio.CancelledError:
            ch_logger.info("Channel monitor task cancelled.")
        except Exception:
            ch_logger.exception("Unexpected error in channel monitor")
            raise
        finally:
            await stream_mgr.close()
            ch_logger.info("Channel monitor task stopped.")

    async def run(self) -> None:
        """Start monitoring all enabled channels concurrently."""
        enabled_channels = [c for c in self.config.channels if c.enabled]
        stop_waiter: asyncio.Task | None = None
        test_timer: asyncio.Task | None = None
        try:
            if not enabled_channels:
                self.logger.warning(
                    "No enabled channels found in configuration. Exiting."
                )
                return

            full_analysis_channels = [
                c for c in enabled_channels if c.mode == ChannelMode.FULL_ANALYSIS
            ]
            notifications_only_channels = [
                c for c in enabled_channels if c.mode == ChannelMode.NOTIFICATIONS_ONLY
            ]

            if self.test_duration_seconds is not None:
                test_minutes = self.test_duration_seconds / 60
                self.logger.info(
                    "TEST MODE: running preflight checks, then monitoring for %.2f minute(s)",
                    test_minutes,
                )
                if full_analysis_channels:
                    telegram_ok, gemini_ok, groq_ok = await asyncio.gather(
                        self.notifier.check_connection(),
                        self.multimodal_verifier.check_connection(),
                        self.transcriber.check_connection(),
                    )
                    if not telegram_ok or not gemini_ok or not groq_ok:
                        failed = []
                        if not telegram_ok:
                            failed.append("Telegram")
                        if not gemini_ok:
                            failed.append("Gemini")
                        if not groq_ok:
                            failed.append("Groq")
                        raise RuntimeError(
                            f"Test-mode preflight failed: {', '.join(failed)}"
                        )
                    self.logger.info(
                        "TEST MODE: Telegram, Gemini, and Groq preflight passed"
                    )
                else:
                    telegram_ok = await self.notifier.check_connection()
                    if not telegram_ok:
                        raise RuntimeError("Test-mode preflight failed: Telegram")
                    self.logger.info("TEST MODE: Telegram preflight passed")

                async def stop_after_test_duration() -> None:
                    try:
                        await asyncio.wait_for(
                            self._stop_event.wait(),
                            timeout=self.test_duration_seconds,
                        )
                    except asyncio.TimeoutError:
                        self.logger.info(
                            "TEST MODE: duration reached; stopping cleanly"
                        )
                        self._stop_event.set()

                test_timer = asyncio.create_task(
                    stop_after_test_duration(), name="test-mode-timer"
                )

            self.logger.info(
                f"Starting YouTube Live Stream Trade Monitoring Bot for {len(enabled_channels)} channel(s) "
                f"({len(full_analysis_channels)} full analysis, {len(notifications_only_channels)} notifications only)..."
            )

            for ch in enabled_channels:
                # Decoupled live status listener task (pure HTTP status check & alert)
                listener = LiveChannelListener(
                    channel_name=ch.name,
                    channel_url=ch.url,
                    notifier=self.notifier,
                    poll_interval_seconds=self.config.capture.live_poll_interval_seconds,
                    capture_config=self.config.capture,
                    alert_state=self.live_alert_state,
                )
                self._live_listeners.append(listener)
                listener_task = asyncio.create_task(
                    listener.listen(self._stop_event),
                    name=f"live-listener-{ch.name}",
                )
                self._tasks.append(listener_task)

                # Real-time audio STT & multimodal trade monitoring task (full analysis only)
                if ch.mode == ChannelMode.FULL_ANALYSIS:
                    task = asyncio.create_task(
                        self.monitor_channel(ch), name=f"monitor-{ch.name}"
                    )
                    self._tasks.append(task)

            stop_waiter = asyncio.create_task(
                self._stop_event.wait(), name="shutdown-waiter"
            )
            done, _ = await asyncio.wait(
                [stop_waiter, *self._tasks], return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                if task is stop_waiter:
                    continue
                if not task.cancelled():
                    exc = task.exception()
                    if exc is not None:
                        raise exc
                if not self._stop_event.is_set():
                    if task.cancelled():
                        task.result()
                    raise RuntimeError(
                        f"Monitor task {task.get_name()} stopped unexpectedly"
                    )
        finally:
            self._stop_event.set()
            if stop_waiter is not None:
                stop_waiter.cancel()
            if test_timer is not None:
                test_timer.cancel()
            for task in self._tasks:
                task.cancel()
            if self._tasks:
                await asyncio.gather(*self._tasks, return_exceptions=True)
            if stop_waiter is not None:
                await asyncio.gather(stop_waiter, return_exceptions=True)
            if test_timer is not None:
                await asyncio.gather(test_timer, return_exceptions=True)
            for listener in list(self._live_listeners):
                await listener.close()
            for audio_mgr in list(self._audio_stream_managers):
                await audio_mgr.stop()
            for mgr in list(self._stream_managers):
                await mgr.close()
            await self.notifier.close()
            await self.multimodal_verifier.close()
            await self.transcriber.close()
            self.logger.info("Trade Monitoring Service shutdown complete.")

    def stop(self) -> None:
        """Signal graceful shutdown."""
        self.logger.info(
            "Shutdown signal received. Stopping all monitors gracefully..."
        )
        self._stop_event.set()


def setup_signal_handlers(
    service: TradeMonitoringService, loop: asyncio.AbstractEventLoop
) -> None:
    """Attach SIGINT and SIGTERM handlers for graceful shutdown."""
    if sys.platform != "win32":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, service.stop)
    else:
        return


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="YouTube Live Stream Trade Monitoring Bot with Audio STT & Multimodal Gemini"
    )
    parser.add_argument(
        "--config",
        "-c",
        type=str,
        default="config.yaml",
        help="Path to YAML configuration file (default: config.yaml)",
    )
    parser.add_argument(
        "--env",
        "-e",
        type=str,
        default=".env",
        help="Path to .env file (default: .env)",
    )
    parser.add_argument(
        "--log-level",
        "-l",
        type=str,
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Override logging level",
    )
    parser.add_argument(
        "--test-minutes",
        type=float,
        default=None,
        metavar="MINUTES",
        help="Run credential preflight and the real monitor for a fixed duration, then stop cleanly",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Print summary report of all recorded live stream captures and metrics",
    )
    return parser.parse_args(args)


async def async_main(args: list[str] | None = None) -> int:
    """Asynchronous entrypoint."""
    parsed_args = parse_args(args)

    if parsed_args.stats:
        session_logger = LiveSessionLogger()
        session_logger.print_summary_report()
        return 0

    try:
        config = load_config(yaml_path=parsed_args.config, env_path=parsed_args.env)
        if parsed_args.test_minutes is not None:
            if not 0 < parsed_args.test_minutes <= 60:
                print(
                    "CONFIGURATION ERROR: --test-minutes must be greater than 0 and at most 60.",
                    file=sys.stderr,
                )
                return 1
            config.capture.live_poll_interval_seconds = min(
                config.capture.live_poll_interval_seconds, 15.0
            )
            if not parsed_args.log_level:
                config.log_level = "DEBUG"
        if parsed_args.log_level:
            config.log_level = parsed_args.log_level
    except ConfigurationError as exc:
        print(f"CONFIGURATION ERROR: {exc}", file=sys.stderr)
        return 1

    test_duration_seconds = (
        parsed_args.test_minutes * 60 if parsed_args.test_minutes is not None else None
    )
    service = TradeMonitoringService(
        config,
        test_duration_seconds=test_duration_seconds,
    )
    loop = asyncio.get_running_loop()
    setup_signal_handlers(service, loop)

    try:
        await service.run()
        return 0
    except (KeyboardInterrupt, asyncio.CancelledError):
        service.stop()
        return 0
    except Exception as exc:
        service.logger.critical(
            f"Fatal error in main monitoring loop: {exc}", exc_info=True
        )
        return 1


def main() -> None:
    """Synchronous CLI entrypoint."""
    try:
        exit_code = asyncio.run(async_main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\nProcess interrupted by user. Exiting cleanly.")
        sys.exit(0)


if __name__ == "__main__":
    main()
