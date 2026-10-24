import logging
import unittest

from provider_utils import provider_ticker_matches


class ProviderTickerIdentityTests(unittest.TestCase):
    def test_exact_identity_is_accepted_case_insensitively(self):
        self.assertTrue(provider_ticker_matches("aapl", "AAPL"))

    def test_yahoo_dot_and_hyphen_share_class_aliases_are_accepted(self):
        self.assertTrue(provider_ticker_matches("BRK.B", "BRK-B"))
        self.assertTrue(provider_ticker_matches("BRK-B", "BRK.B"))

    def test_different_or_malformed_provider_identity_is_rejected(self):
        self.assertFalse(provider_ticker_matches("AAPL", "MSFT"))
        self.assertFalse(provider_ticker_matches("AAPL", 123))
        self.assertFalse(provider_ticker_matches("AAPL", []))

    def test_malformed_identity_is_type_checked_before_comparison(self):
        class UnsafeEquality:
            def __eq__(self, other):
                raise AssertionError("provider value must not be compared before type checking")

        self.assertFalse(provider_ticker_matches("AAPL", UnsafeEquality()))

    def test_missing_provider_identity_remains_compatible(self):
        self.assertTrue(provider_ticker_matches("AAPL", None))
        self.assertTrue(provider_ticker_matches("AAPL", ""))
        self.assertFalse(provider_ticker_matches("", "AAPL"))


class ProviderLoggingPrivacyTests(unittest.TestCase):
    def test_yfinance_diagnostics_cannot_propagate_raw_search_text(self):
        logger = logging.getLogger("yfinance")
        previous_level = logger.level
        previous_propagation = logger.propagate
        self.addCleanup(setattr, logger, "level", previous_level)
        self.addCleanup(setattr, logger, "propagate", previous_propagation)

        captured = []

        class Capture(logging.Handler):
            def emit(self, record):
                captured.append(record.getMessage())

        handler = Capture()
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)

        # provider_utils configures this boundary at import time so upstream
        # diagnostics cannot leak query text or tickers into runtime logs.
        self.assertEqual(logger.level, logging.CRITICAL + 1)
        self.assertFalse(logger.propagate)
        logger.error("provider failed for person@example.test")

        self.assertEqual(captured, [])
