"""Unit tests for OpenCV horizontal line and color filter."""

import unittest
from pathlib import Path

import cv2
import numpy as np

from src.config import CVFilterConfig, ROICropConfig
from src.cv_filter import CVFilter, CVFilterResult


class TestCVFilter(unittest.TestCase):
    """Tests for the fast OpenCV pre-filter detecting trade lines."""

    def setUp(self) -> None:
        self.width = 640
        self.height = 480
        # Configure filter without ROI crop for basic tests to test full image
        self.config = CVFilterConfig(
            min_line_length_ratio=0.20,
            max_line_angle_degrees=5.0,
            roi_crop=None,
            morph_kernel_width=15,
            morph_kernel_height=1,
            hough_threshold=20,
            hough_min_line_length=30,
            hough_max_line_gap=10,
        )
        self.filter = CVFilter(self.config)

    def _create_blank_image(self) -> np.ndarray:
        # Dark chart background (BGR)
        return np.full((self.height, self.width, 3), 30, dtype=np.uint8)

    def test_detect_blue_horizontal_line(self) -> None:
        """Synthetic frame with a bright blue horizontal line."""
        img = self._create_blank_image()
        # Draw horizontal blue line (BGR: Blue=255, Green=0, Red=0)
        y = self.height // 2
        x1 = 50
        x2 = 550
        cv2.line(img, (x1, y), (x2, y), (255, 0, 0), thickness=3)

        result: CVFilterResult = self.filter.filter_frame(img)
        self.assertTrue(result.candidate_found)
        self.assertIn(result.color_detected, ("BLUE", "BOTH"))
        self.assertGreater(len(result.lines), 0)
        self.assertEqual(result.lines[0].color, "BLUE")
        self.assertLessEqual(result.lines[0].angle_degrees, 5.0)

    def test_detect_red_horizontal_line(self) -> None:
        """Synthetic frame with a bright red horizontal line."""
        img = self._create_blank_image()
        # Draw horizontal red line (BGR: Blue=0, Green=0, Red=255)
        y = self.height // 2
        x1 = 50
        x2 = 550
        cv2.line(img, (x1, y), (x2, y), (0, 0, 255), thickness=3)

        result: CVFilterResult = self.filter.filter_frame(img)
        self.assertTrue(result.candidate_found)
        self.assertIn(result.color_detected, ("RED", "BOTH"))
        self.assertGreater(len(result.lines), 0)
        self.assertEqual(result.lines[0].color, "RED")

    def test_detect_both_blue_and_red_lines(self) -> None:
        """Synthetic frame with both blue and red horizontal lines."""
        img = self._create_blank_image()
        # Draw blue line at top, red line at bottom
        cv2.line(img, (50, 150), (550, 150), (255, 0, 0), thickness=3)
        cv2.line(img, (50, 350), (550, 350), (0, 0, 255), thickness=3)

        result: CVFilterResult = self.filter.filter_frame(img)
        self.assertTrue(result.candidate_found)
        self.assertEqual(result.color_detected, "BOTH")
        self.assertGreaterEqual(len(result.lines), 2)

    def test_reject_vertical_line(self) -> None:
        """Vertical lines should be rejected by horizontal morphology and angle filter."""
        img = self._create_blank_image()
        # Draw vertical blue line
        x = self.width // 2
        cv2.line(img, (x, 50), (x, 430), (255, 0, 0), thickness=3)

        result: CVFilterResult = self.filter.filter_frame(img)
        self.assertFalse(result.candidate_found)
        self.assertEqual(len(result.lines), 0)

    def test_reject_diagonal_line(self) -> None:
        """Diagonal 45-degree lines should be rejected by angle filter."""
        img = self._create_blank_image()
        # Draw diagonal 45-deg blue line
        cv2.line(img, (100, 100), (400, 400), (255, 0, 0), thickness=3)

        result: CVFilterResult = self.filter.filter_frame(img)
        self.assertFalse(result.candidate_found)
        self.assertEqual(len(result.lines), 0)

    def test_reject_too_short_line(self) -> None:
        """Lines shorter than min_line_length_ratio should be ignored."""
        img = self._create_blank_image()
        # Short segment (length 20px < 640 * 0.20 = 128px)
        cv2.line(img, (200, 200), (220, 200), (255, 0, 0), thickness=3)

        result: CVFilterResult = self.filter.filter_frame(img)
        self.assertFalse(result.candidate_found)

    def test_blank_and_noise_frames(self) -> None:
        """Blank image and pure random noise should not trigger false positives."""
        blank = self._create_blank_image()
        result_blank = self.filter.filter_frame(blank)
        self.assertFalse(result_blank.candidate_found)
        self.assertEqual(result_blank.color_detected, "NONE")

        noise = np.random.randint(0, 256, (self.height, self.width, 3), dtype=np.uint8)
        result_noise = self.filter.filter_frame(noise)
        self.assertFalse(result_noise.candidate_found)

    def test_roi_cropping_behavior(self) -> None:
        """Line inside ROI is detected, while line strictly outside ROI is ignored."""
        roi_config = CVFilterConfig(
            min_line_length_ratio=0.20,
            roi_crop=ROICropConfig(top=0.2, bottom=0.8, left=0.2, right=0.8),
            morph_kernel_width=15,
            hough_threshold=20,
            hough_min_line_length=30,
        )
        roi_filter = CVFilter(roi_config)

        # 1. Line outside ROI (at very top: y = 20, top ROI starts at y = 96)
        img_outside = self._create_blank_image()
        cv2.line(img_outside, (50, 20), (550, 20), (255, 0, 0), thickness=3)
        res_outside = roi_filter.filter_frame(img_outside)
        self.assertFalse(res_outside.candidate_found)

        # 2. Line inside ROI (at center: y = 240)
        img_inside = self._create_blank_image()
        cv2.line(img_inside, (150, 240), (500, 240), (255, 0, 0), thickness=3)
        res_inside = roi_filter.filter_frame(img_inside)
        self.assertTrue(res_inside.candidate_found)

    def test_invalid_frame_inputs(self) -> None:
        """Handle None, empty array, or degenerate images gracefully."""
        self.assertFalse(self.filter.filter_frame(None).candidate_found)
        self.assertFalse(self.filter.filter_frame(np.array([])).candidate_found)
        self.assertFalse(
            self.filter.filter_frame(
                np.zeros((2, 2, 3), dtype=np.uint8)
            ).candidate_found
        )

    def test_reference_fixture_generates_candidate_without_player_chrome(self) -> None:
        """The reference creates a CV candidate while bottom player chrome is excluded.

        The fast filter is deliberately permissive: Gemini, not OpenCV, decides whether
        the candidate is an active position, a pending order, or a drawing.
        """
        fixture_path = Path(__file__).parent / "fixtures" / "live_trade_reference.png"
        self.assertTrue(fixture_path.exists(), f"Fixture missing at {fixture_path}")
        frame = cv2.imread(str(fixture_path))
        self.assertIsNotNone(frame)

        calibrated_filter = CVFilter()  # Uses default calibrated CVFilterConfig
        result: CVFilterResult = calibrated_filter.filter_frame(frame)

        self.assertTrue(result.candidate_found)
        self.assertEqual(result.color_detected, "RED")
        self.assertGreater(len(result.lines), 0)
        # All detected lines must be RED
        for line in result.lines:
            self.assertEqual(line.color, "RED")
            # Must be in the chart area, strictly above bottom player controls (y < 500).
            self.assertLess(line.y1, 500)
            self.assertLess(line.y2, 500)

    def test_player_controls_only_synthetic_negative(self) -> None:
        """Synthetic frame with only video player chrome at the bottom must be rejected."""
        img = self._create_blank_image()
        h, w = img.shape[:2]

        # Draw synthetic player controls at the bottom 15% of frame (y >= 0.85 * h)
        player_y = int(0.88 * h)
        # Blue player progress track across width
        cv2.line(img, (20, player_y), (w - 20, player_y), (255, 0, 0), thickness=4)
        # Red elapsed bar segment
        cv2.line(
            img, (20, player_y), (int(w * 0.3), player_y), (0, 0, 255), thickness=4
        )

        calibrated_filter = CVFilter()
        result: CVFilterResult = calibrated_filter.filter_frame(img)

        self.assertFalse(result.candidate_found)
        self.assertEqual(result.color_detected, "NONE")
        self.assertEqual(len(result.lines), 0)

    def test_fixture_player_controls_isolated_negative(self) -> None:
        """Bottom player controls from live reference alone must not trigger false positives."""
        fixture_path = Path(__file__).parent / "fixtures" / "live_trade_reference.png"
        ref_frame = cv2.imread(str(fixture_path))
        self.assertIsNotNone(ref_frame)

        h, w = ref_frame.shape[:2]
        # Place only the bottom player controls onto an otherwise blank chart
        frame_controls_only = np.full((h, w, 3), 30, dtype=np.uint8)
        frame_controls_only[int(0.85 * h) :, :] = ref_frame[int(0.85 * h) :, :]

        calibrated_filter = CVFilter()
        result: CVFilterResult = calibrated_filter.filter_frame(frame_controls_only)

        self.assertFalse(result.candidate_found)
        self.assertEqual(result.color_detected, "NONE")
        self.assertEqual(len(result.lines), 0)

    def test_fixture_resolution_scaling_robustness(self) -> None:
        """Reference candidate detection remains robust across common resolutions."""
        fixture_path = Path(__file__).parent / "fixtures" / "live_trade_reference.png"
        ref_frame = cv2.imread(str(fixture_path))
        self.assertIsNotNone(ref_frame)

        calibrated_filter = CVFilter()
        for scale in (1.0, 1.25, 1.5, 2.0):
            scaled_w = int(ref_frame.shape[1] * scale)
            scaled_h = int(ref_frame.shape[0] * scale)
            resized = cv2.resize(
                ref_frame, (scaled_w, scaled_h), interpolation=cv2.INTER_LINEAR
            )
            res = calibrated_filter.filter_frame(resized)
            self.assertTrue(res.candidate_found, f"Failed at scale {scale}")
            self.assertEqual(res.color_detected, "RED", f"Wrong color at scale {scale}")


if __name__ == "__main__":
    unittest.main()
