"""OpenCV-based fast pre-filter for detecting Blue/Red horizontal trade entry lines."""

import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from src.config import CVFilterConfig, HSVRange


@dataclass
class DetectedLine:
    """Represents a detected horizontal line segment."""

    x1: int
    y1: int
    x2: int
    y2: int
    length: float
    angle_degrees: float
    color: str  # "BLUE" or "RED"


@dataclass
class CVFilterResult:
    """Result of running the CV filter on a single video frame."""

    candidate_found: bool
    color_detected: str  # "BLUE", "RED", "BOTH", or "NONE"
    lines: list[DetectedLine] = field(default_factory=list)
    roi_box: tuple[int, int, int, int] | None = None  # (ymin, xmin, ymax, xmax)
    processed_width: int = 0
    processed_height: int = 0


class CVFilter:
    """Fast OpenCV computer vision filter to detect trading position horizontal lines."""

    def __init__(self, config: CVFilterConfig | None = None) -> None:
        self.config = config or CVFilterConfig()

    def _apply_roi(
        self, frame: np.ndarray
    ) -> tuple[np.ndarray, tuple[int, int, int, int]]:
        h, w = frame.shape[:2]
        if self.config.roi_crop is None:
            return frame, (0, 0, h, w)

        roi = self.config.roi_crop
        ymin = int(roi.top * h)
        ymax = int(roi.bottom * h)
        xmin = int(roi.left * w)
        xmax = int(roi.right * w)

        # Ensure valid non-empty slice
        ymin = max(0, min(ymin, h - 1))
        ymax = max(ymin + 1, min(ymax, h))
        xmin = max(0, min(xmin, w - 1))
        xmax = max(xmin + 1, min(xmax, w))

        cropped = frame[ymin:ymax, xmin:xmax]
        return cropped, (ymin, xmin, ymax, xmax)

    def _create_color_mask(
        self, hsv_image: np.ndarray, ranges: list[HSVRange]
    ) -> np.ndarray:
        mask = np.zeros(hsv_image.shape[:2], dtype=np.uint8)
        for hsv_range in ranges:
            lower = np.array(hsv_range.lower, dtype=np.uint8)
            upper = np.array(hsv_range.upper, dtype=np.uint8)
            sub_mask = cv2.inRange(hsv_image, lower, upper)
            mask = cv2.bitwise_or(mask, sub_mask)
        return mask

    def _detect_horizontal_lines_in_mask(
        self,
        mask: np.ndarray,
        color_label: str,
        roi_offset: tuple[int, int],
        frame_width: int,
    ) -> list[DetectedLine]:
        """Apply morphological horizontal filter and HoughLinesP to detect lines."""
        kernel_w = max(3, self.config.morph_kernel_width)
        kernel_h = max(1, self.config.morph_kernel_height)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_w, kernel_h))

        # Perform morphological opening to emphasize horizontal structures
        morphed = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

        # Hough line detection
        lines = cv2.HoughLinesP(
            morphed,
            rho=1,
            theta=np.pi / 180,
            threshold=self.config.hough_threshold,
            minLineLength=self.config.hough_min_line_length,
            maxLineGap=self.config.hough_max_line_gap,
        )

        if lines is None:
            return []

        min_len_px = frame_width * self.config.min_line_length_ratio
        max_angle = self.config.max_line_angle_degrees
        ymin_offset, xmin_offset = roi_offset

        detected: list[DetectedLine] = []
        for line_entry in lines:
            flat = np.asarray(line_entry).flatten()
            if len(flat) < 4:
                continue
            x1, y1, x2, y2 = int(flat[0]), int(flat[1]), int(flat[2]), int(flat[3])
            dx = float(x2 - x1)
            dy = float(y2 - y1)
            length = math.hypot(dx, dy)

            if length < min_len_px:
                continue

            # Calculate line angle relative to horizontal (0 degrees)
            angle_deg = abs(math.degrees(math.atan2(dy, dx)))
            # Normalize angle to deviation from horizontal (0 to 90 degrees)
            if angle_deg > 90:
                angle_deg = 180 - angle_deg

            if angle_deg <= max_angle:
                detected.append(
                    DetectedLine(
                        x1=x1 + xmin_offset,
                        y1=y1 + ymin_offset,
                        x2=x2 + xmin_offset,
                        y2=y2 + ymin_offset,
                        length=length,
                        angle_degrees=round(angle_deg, 2),
                        color=color_label,
                    )
                )

        return detected

    def filter_frame(self, frame: np.ndarray | None) -> CVFilterResult:
        """Process a raw video frame (BGR format) and detect candidate trade lines.

        Args:
            frame: Raw numpy image array in BGR format.

        Returns:
            CVFilterResult: Candidate detection status and line metadata.
        """
        if frame is None or not isinstance(frame, np.ndarray) or frame.size == 0:
            return CVFilterResult(
                candidate_found=False,
                color_detected="NONE",
                lines=[],
                roi_box=None,
                processed_width=0,
                processed_height=0,
            )

        h, w = frame.shape[:2]
        if h < 10 or w < 10:
            return CVFilterResult(
                candidate_found=False,
                color_detected="NONE",
                lines=[],
                roi_box=None,
                processed_width=w,
                processed_height=h,
            )

        # 1. Apply ROI cropping
        cropped, (ymin, xmin, ymax, xmax) = self._apply_roi(frame)
        roi_h, roi_w = cropped.shape[:2]

        if roi_h < 5 or roi_w < 5:
            return CVFilterResult(
                candidate_found=False,
                color_detected="NONE",
                lines=[],
                roi_box=(ymin, xmin, ymax, xmax),
                processed_width=w,
                processed_height=h,
            )

        # 2. Convert to HSV
        hsv = cv2.cvtColor(cropped, cv2.COLOR_BGR2HSV)

        # 3. Create color masks
        blue_mask = self._create_color_mask(hsv, self.config.blue_hsv_ranges)
        red_mask = self._create_color_mask(hsv, self.config.red_hsv_ranges)

        # 4. Detect lines in both masks
        blue_lines = self._detect_horizontal_lines_in_mask(
            blue_mask, "BLUE", (ymin, xmin), frame_width=w
        )
        red_lines = self._detect_horizontal_lines_in_mask(
            red_mask, "RED", (ymin, xmin), frame_width=w
        )

        all_lines = blue_lines + red_lines
        has_blue = len(blue_lines) > 0
        has_red = len(red_lines) > 0

        if has_blue and has_red:
            color_detected = "BOTH"
        elif has_blue:
            color_detected = "BLUE"
        elif has_red:
            color_detected = "RED"
        else:
            color_detected = "NONE"

        candidate_found = len(all_lines) > 0

        return CVFilterResult(
            candidate_found=candidate_found,
            color_detected=color_detected,
            lines=all_lines,
            roi_box=(ymin, xmin, ymax, xmax),
            processed_width=w,
            processed_height=h,
        )
