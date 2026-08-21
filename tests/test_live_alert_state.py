"""Deterministic offline unit tests for LiveAlertStateStore atomic state persistence."""

import concurrent.futures
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.live_alert_state import (
    LiveAlertStateStore,
    normalize_channel_url,
)


class TestLiveAlertStateStore(unittest.TestCase):
    """Deterministic offline tests for LiveAlertStateStore."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_file = Path(self.temp_dir.name) / "data" / "live_alert_state.json"
        self.store = LiveAlertStateStore(
            storage_path=self.state_file, max_history_per_channel=5
        )
        self.channel_url = "https://www.youtube.com/@SampleTrader/live"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_url_normalization(self) -> None:
        """URL normalization handles whitespace, trailing slashes, and case."""
        self.assertEqual(
            normalize_channel_url("  https://www.youtube.com/@Trader/live/  "),
            "https://www.youtube.com/@trader/live",
        )
        self.assertEqual(normalize_channel_url(""), "")
        self.assertEqual(normalize_channel_url(None), "")  # type: ignore[arg-type]

    def test_claim_new_broadcast_id_persists_to_disk(self) -> None:
        """First claim for a broadcast ID succeeds and is durably persisted to JSON file."""
        claimed = self.store.claim_broadcast(self.channel_url, "vid_001")
        self.assertTrue(claimed)
        self.assertTrue(self.state_file.exists())

        # Verify disk contents
        with open(self.state_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        norm_url = normalize_channel_url(self.channel_url)
        self.assertEqual(data.get("version"), 1)
        self.assertIn(norm_url, data.get("channels", {}))
        self.assertEqual(data["channels"][norm_url], ["vid_001"])

        # Check has_claimed and get_history
        self.assertTrue(self.store.has_claimed(self.channel_url, "vid_001"))
        self.assertEqual(self.store.get_history(self.channel_url), ["vid_001"])

    def test_claim_same_broadcast_id_returns_false(self) -> None:
        """Duplicate claim for the same broadcast ID returns False."""
        first = self.store.claim_broadcast(self.channel_url, "vid_001")
        self.assertTrue(first)

        second = self.store.claim_broadcast(self.channel_url, "vid_001")
        self.assertFalse(second)

        # Case/trailing slash variation of URL also rejected
        variant_url = "https://www.youtube.com/@SAMPLETRADER/live/"
        third = self.store.claim_broadcast(variant_url, "vid_001")
        self.assertFalse(third)

    def test_claim_new_broadcast_id_on_same_channel_succeeds(self) -> None:
        """A genuinely new broadcast ID on the same channel is eligible and appended."""
        self.assertTrue(self.store.claim_broadcast(self.channel_url, "vid_001"))
        self.assertTrue(self.store.claim_broadcast(self.channel_url, "vid_002"))

        history = self.store.get_history(self.channel_url)
        self.assertEqual(history, ["vid_001", "vid_002"])

    def test_process_restart_preserves_claimed_history(self) -> None:
        """A fresh LiveAlertStateStore pointing to the same file preserves claimed history."""
        self.store.claim_broadcast(self.channel_url, "vid_001")
        self.store.claim_broadcast(self.channel_url, "vid_002")

        # Simulate process restart by creating a new instance
        restarted_store = LiveAlertStateStore(
            storage_path=self.state_file, max_history_per_channel=5
        )
        self.assertTrue(restarted_store.has_claimed(self.channel_url, "vid_001"))
        self.assertTrue(restarted_store.has_claimed(self.channel_url, "vid_002"))
        self.assertFalse(restarted_store.claim_broadcast(self.channel_url, "vid_001"))
        self.assertTrue(restarted_store.claim_broadcast(self.channel_url, "vid_003"))

    def test_missing_or_empty_broadcast_id_fails_closed(self) -> None:
        """Missing, whitespace-only, or None broadcast ID fails closed."""
        self.assertFalse(self.store.claim_broadcast(self.channel_url, None))
        self.assertFalse(self.store.claim_broadcast(self.channel_url, ""))
        self.assertFalse(self.store.claim_broadcast(self.channel_url, "   "))
        self.assertFalse(self.store.claim_broadcast("", "vid_001"))

    def test_corrupt_json_fails_closed_and_preserves_file(self) -> None:
        """Corrupt JSON in state file causes claim_broadcast to fail closed without overwriting."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_file, "w", encoding="utf-8") as f:
            f.write("{ invalid json corrupted content !!!")

        claimed = self.store.claim_broadcast(self.channel_url, "vid_001")
        self.assertFalse(claimed)

        # File must remain untouched (not overwritten)
        with open(self.state_file, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertEqual(content, "{ invalid json corrupted content !!!")

    def test_invalid_json_schema_fails_closed(self) -> None:
        """Valid JSON but invalid schema (non-dict or non-dict channels) fails closed."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(["not", "a", "dict"], f)

        self.assertFalse(self.store.claim_broadcast(self.channel_url, "vid_001"))

    def test_atomic_replace_failure_fails_closed_and_cleans_temp_file(self) -> None:
        """A disk replacement failure must not grant an unpersisted claim."""
        with patch(
            "src.live_alert_state.os.replace", side_effect=OSError("disk failure")
        ):
            self.assertFalse(
                self.store.claim_broadcast(self.channel_url, "vid_write_failure")
            )

        self.assertFalse(self.state_file.exists())
        self.assertEqual(list(self.state_file.parent.glob("*.tmp")), [])

    def test_history_bounding_per_channel(self) -> None:
        """History per channel is strictly bounded by max_history_per_channel."""
        bounded_store = LiveAlertStateStore(
            storage_path=self.state_file, max_history_per_channel=3
        )
        for i in range(5):
            self.assertTrue(bounded_store.claim_broadcast(self.channel_url, f"vid_{i}"))

        history = bounded_store.get_history(self.channel_url)
        self.assertEqual(history, ["vid_2", "vid_3", "vid_4"])
        # Oldest IDs evicted
        self.assertFalse(bounded_store.has_claimed(self.channel_url, "vid_0"))
        self.assertFalse(bounded_store.has_claimed(self.channel_url, "vid_1"))
        # Evicted IDs become re-claimable if max history window passed
        self.assertTrue(bounded_store.claim_broadcast(self.channel_url, "vid_0"))

    def test_concurrent_claims_serialized_and_atomic(self) -> None:
        """Concurrent claims across multiple threads are serialized and atomic with exact counts."""
        store = LiveAlertStateStore(
            storage_path=self.state_file, max_history_per_channel=200
        )
        channel_count = 5
        broadcasts_per_channel = 10

        channels = [
            f"https://www.youtube.com/@Trader{i}/live" for i in range(channel_count)
        ]

        def try_claim(ch: str, b_id: str) -> bool:
            return store.claim_broadcast(ch, b_id)

        tasks: list[tuple[str, str]] = []
        for ch in channels:
            for b in range(broadcasts_per_channel):
                tasks.append((ch, f"broadcast_{b}"))
        # Add duplicate claim tasks to test exact dedupe
        for ch in channels:
            for b in range(broadcasts_per_channel):
                tasks.append((ch, f"broadcast_{b}"))

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(try_claim, ch, b_id) for ch, b_id in tasks]
            results = [f.result() for f in futures]

        # Exactly channel_count * broadcasts_per_channel claims should have succeeded
        successful_claims = sum(1 for r in results if r is True)
        duplicate_claims = sum(1 for r in results if r is False)
        self.assertEqual(successful_claims, channel_count * broadcasts_per_channel)
        self.assertEqual(duplicate_claims, channel_count * broadcasts_per_channel)

        # Check persisted file integrity
        with open(self.state_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertEqual(len(data["channels"]), channel_count)
        for ch in channels:
            norm = normalize_channel_url(ch)
            self.assertEqual(len(data["channels"][norm]), broadcasts_per_channel)


if __name__ == "__main__":
    unittest.main()
