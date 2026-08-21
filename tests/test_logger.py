"""Regression tests for reliable console logging."""

import io
import logging
import unittest

from src.logger import _EncodingSafeStream, _UTCFormatter


class TestLogger(unittest.TestCase):
    def test_unencodable_unicode_is_preserved_as_escape(self) -> None:
        raw = io.BytesIO()
        terminal = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
        stream = _EncodingSafeStream(terminal)

        stream.write("trade opened 🚨")
        stream.flush()

        self.assertIn(r"\U0001f6a8", raw.getvalue().decode("cp1252"))

    def test_formatter_uses_utc_for_z_suffix(self) -> None:
        formatter = _UTCFormatter("%(asctime)s", datefmt="%Y-%m-%dT%H:%M:%SZ")
        record = logging.LogRecord(
            "test", logging.INFO, __file__, 1, "message", (), None
        )
        record.created = 0

        self.assertEqual(formatter.format(record), "1970-01-01T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
