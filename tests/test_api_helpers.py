import json
import unittest

import numpy as np
import pandas as pd
from unittest.mock import patch

from api_helpers import _json_default, safe_error
from research_service import ResearchError


class ApiJsonSerializationTests(unittest.TestCase):
    def test_pandas_timestamp_serializes_as_iso_8601(self):
        stamp = pd.Timestamp("2026-10-07T12:30:00Z")
        result = json.dumps({"observed_at": stamp}, default=_json_default, allow_nan=False)
        self.assertEqual(json.loads(result)["observed_at"], "2026-10-07T12:30:00+00:00")

    def test_numpy_scalar_serializes_as_native_value(self):
        result = json.dumps({"close": np.float64(123.45)}, default=_json_default, allow_nan=False)
        self.assertEqual(json.loads(result)["close"], 123.45)


class ApiErrorPrivacyTests(unittest.TestCase):
    def test_safe_error_logs_category_without_user_or_provider_message(self):
        with patch("api_helpers.send_json") as send:
            with self.assertLogs("tsrp", level="WARNING") as captured:
                safe_error(object(), ResearchError("invalid_request", "private query example@invalid.test"))
        self.assertEqual(send.call_args.args[1], 400)
        self.assertNotIn("private query", "\n".join(captured.output))

    def test_unexpected_error_does_not_log_exception_text(self):
        with patch("api_helpers.send_json"):
            with self.assertLogs("tsrp", level="ERROR") as captured:
                safe_error(object(), ValueError("provider token secret-value"))
        self.assertNotIn("secret-value", "\n".join(captured.output))


if __name__ == "__main__":
    unittest.main()
