"""Provider requests in the Urdu vision route."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from specter import urdu_vision


class GeminiRequestTests(unittest.TestCase):
    def test_api_key_travels_in_a_header_never_the_url(self):
        # A URL is what proxies, error messages and access logs record.
        with tempfile.TemporaryDirectory() as tmp:
            crop = Path(tmp) / "crop.png"
            crop.write_bytes(b"\x89PNG\r\n\x1a\n")
            captured = {}

            def fake_request(url, payload, headers, timeout=120):
                captured.update(url=url, headers=headers)
                return {"candidates": [{"content": {"parts": [{"text": '{"items": []}'}]}}]}

            with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "secret-key"}), \
                    mock.patch.object(urdu_vision, "_json_request", side_effect=fake_request), \
                    mock.patch.object(urdu_vision, "_validate_items", return_value=[]):
                provider = urdu_vision.GeminiVision(Path(tmp))
                provider.quota.reserve = lambda: None
                provider.complete([{"id": "c1", "image": str(crop)}])

        self.assertNotIn("secret-key", captured["url"])
        self.assertNotIn("key=", captured["url"])
        self.assertEqual(captured["headers"].get("x-goog-api-key"), "secret-key")


if __name__ == "__main__":
    unittest.main()
