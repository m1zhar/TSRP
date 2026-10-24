import threading
import time
import unittest
from concurrent.futures import Future, ThreadPoolExecutor
from unittest.mock import patch

from analysis_coalescer import _SharedCancellation, coalesce_analysis, coalesce_inflight


class AnalysisCoalescerTests(unittest.TestCase):
    def test_pre_cancelled_owner_does_not_start_provider_work_or_poison_key(self):
        cancel_event = threading.Event()
        cancel_event.set()
        calls = []

        def calculate(ticker):
            calls.append(ticker)
            return {"company": {"ticker": ticker}}

        with self.assertRaisesRegex(InterruptedError, "disconnected before shared provider work"):
            coalesce_analysis(
                "AAPL",
                calculate,
                cancel_event=cancel_event,
                run_with_cancellation=lambda _signal, call: call(),
            )

        self.assertEqual(calls, [])
        self.assertEqual(coalesce_analysis("AAPL", calculate), {"company": {"ticker": "AAPL"}})
        self.assertEqual(calls, ["AAPL"])

    def test_disconnect_after_entry_check_stops_owner_before_runner(self):
        class CancelAtOwnerCheck(threading.Event):
            checks = 0

            def is_set(self):
                self.checks += 1
                if self.checks == 2:
                    self.set()
                return super().is_set()

        cancel_event = CancelAtOwnerCheck()
        calls = []

        with self.assertRaisesRegex(InterruptedError, "disconnected before shared provider work"):
            coalesce_analysis(
                "MSFT",
                lambda ticker: calls.append(ticker) or {"company": {"ticker": ticker}},
                cancel_event=cancel_event,
                run_with_cancellation=lambda _signal, call: call(),
            )

        self.assertEqual(calls, [])
        self.assertEqual(
            coalesce_analysis("MSFT", lambda ticker: {"company": {"ticker": ticker}}),
            {"company": {"ticker": "MSFT"}},
        )

    def test_connected_follower_registers_atomically_during_owner_disconnect(self):
        owner_cancel = threading.Event()
        follower_waiting = threading.Event()
        owner_started = threading.Event()
        registration_checked = threading.Event()
        allow_registration = threading.Event()
        release_owner = threading.Event()
        arm_registration_gate = threading.Event()
        signal_holder = []
        calls = []
        follower_checks = 0
        original_check = _SharedCancellation._is_set_locked

        class FollowerEvent(threading.Event):
            def is_set(self):
                nonlocal follower_checks
                follower_checks += 1
                if follower_checks == 2:
                    follower_waiting.set()
                return super().is_set()

        def interposed_check(shared):
            result = original_check(shared)
            if (
                signal_holder
                and shared is signal_holder[0]
                and arm_registration_gate.is_set()
                and not registration_checked.is_set()
            ):
                registration_checked.set()
                if not allow_registration.wait(timeout=1):
                    raise TimeoutError("follower registration was not released")
            return result

        def runner(signal, calculate):
            signal_holder.append(signal)
            owner_started.set()
            if not release_owner.wait(timeout=1):
                raise TimeoutError("owner calculation was not released")
            if signal.is_set():
                raise RuntimeError("shared work cancelled without its connected follower")
            return calculate()

        with patch.object(_SharedCancellation, "_is_set_locked", interposed_check):
            with ThreadPoolExecutor(max_workers=2) as pool:
                owner = pool.submit(
                    coalesce_analysis,
                    "RACE",
                    lambda ticker: calls.append(ticker) or {"company": {"ticker": ticker}},
                    cancel_event=owner_cancel,
                    run_with_cancellation=runner,
                )
                self.assertTrue(owner_started.wait(timeout=1))
                arm_registration_gate.set()
                follower_event = FollowerEvent()
                follower = pool.submit(
                    coalesce_analysis,
                    "RACE",
                    lambda ticker: calls.append(ticker) or {"company": {"ticker": ticker}},
                    cancel_event=follower_event,
                    run_with_cancellation=runner,
                )
                try:
                    self.assertTrue(registration_checked.wait(timeout=1))
                    owner_cancel.set()
                finally:
                    allow_registration.set()
                self.assertTrue(follower_waiting.wait(timeout=1))
                release_owner.set()
                expected = {"company": {"ticker": "RACE"}}
                self.assertEqual(owner.result(timeout=1), expected)
                self.assertEqual(follower.result(timeout=1), expected)

        self.assertEqual(calls, ["RACE"])

    def test_provider_timeout_is_propagated_to_followers_and_key_recovers(self):
        owner_started = threading.Event()
        follower_waiting = threading.Event()
        release = threading.Event()
        calls = []

        class TrackingFuture(Future):
            def result(self, timeout=None):
                if timeout is not None:
                    follower_waiting.set()
                return super().result(timeout)

        def calculate(ticker):
            calls.append(ticker)
            owner_started.set()
            if not release.wait(timeout=1):
                raise TimeoutError("test provider was not released")
            raise TimeoutError("provider deadline exceeded")

        with patch("analysis_coalescer.Future", TrackingFuture):
            with ThreadPoolExecutor(max_workers=2) as pool:
                owner = pool.submit(coalesce_analysis, "TIMEOUT", calculate)
                self.assertTrue(owner_started.wait(timeout=1))
                follower = pool.submit(coalesce_analysis, "TIMEOUT", calculate)
                self.assertTrue(follower_waiting.wait(timeout=1))
                release.set()
                with self.assertRaisesRegex(TimeoutError, "provider deadline exceeded"):
                    owner.result(timeout=1)
                with self.assertRaisesRegex(TimeoutError, "provider deadline exceeded"):
                    follower.result(timeout=1)

        self.assertEqual(calls, ["TIMEOUT"])
        self.assertEqual(
            coalesce_analysis("TIMEOUT", lambda ticker: {"company": {"ticker": ticker}}),
            {"company": {"ticker": "TIMEOUT"}},
        )

    def test_only_concurrent_identical_tickers_share_a_calculation(self):
        waiter_count = 7
        waiters_ready = threading.Event()
        started = threading.Event()
        release = threading.Event()
        counter_lock = threading.Lock()
        calls = []
        waiting = 0

        class TrackingFuture(Future):
            def result(self, timeout=None):
                nonlocal waiting
                with counter_lock:
                    waiting += 1
                    if waiting == waiter_count:
                        waiters_ready.set()
                return super().result(timeout)

        def calculate(ticker):
            with counter_lock:
                calls.append(ticker)
            started.set()
            if not release.wait(timeout=2):
                raise TimeoutError("test calculation was not released")
            return {"company": {"ticker": ticker}}

        with patch("analysis_coalescer.Future", TrackingFuture):
            with ThreadPoolExecutor(max_workers=waiter_count + 1) as pool:
                leader = pool.submit(coalesce_analysis, "AAPL", calculate)
                self.assertTrue(started.wait(timeout=1))
                followers = [pool.submit(coalesce_analysis, "AAPL", calculate) for _ in range(waiter_count)]
                try:
                    self.assertTrue(waiters_ready.wait(timeout=1))
                    self.assertEqual(calls, ["AAPL"])
                finally:
                    release.set()
                results = [leader.result(timeout=1), *(item.result(timeout=1) for item in followers)]

        self.assertEqual(calls, ["AAPL"])
        self.assertTrue(all(result == results[0] for result in results))
        self.assertIsNot(results[0], results[1], "followers receive independent copies before the API adds route metadata")
        results[0]["company"]["ticker"] = "MUTATED"
        self.assertEqual(results[1]["company"]["ticker"], "AAPL")

        later = coalesce_analysis("AAPL", calculate)
        self.assertEqual(later, {"company": {"ticker": "AAPL"}})
        self.assertEqual(calls, ["AAPL", "AAPL"], "completed analyses are not retained as a cache")

    def test_failed_calculation_is_not_retained_for_the_next_request(self):
        attempts = 0

        def fail(_ticker):
            nonlocal attempts
            attempts += 1
            raise ValueError("provider unavailable")

        with self.assertRaisesRegex(ValueError, "provider unavailable"):
            coalesce_analysis("MSFT", fail)
        result = coalesce_analysis("MSFT", lambda ticker: {"company": {"ticker": ticker}})

        self.assertEqual(attempts, 1)
        self.assertEqual(result, {"company": {"ticker": "MSFT"}})

    def test_concurrent_waiters_share_failure_then_a_later_request_can_recover(self):
        waiter_count = 3
        waiters_ready = threading.Event()
        started = threading.Event()
        release = threading.Event()
        calls = 0
        lock = threading.Lock()
        waiting = 0

        class TrackingFuture(Future):
            def result(self, timeout=None):
                nonlocal waiting
                with lock:
                    waiting += 1
                    if waiting == waiter_count:
                        waiters_ready.set()
                return super().result(timeout)

        def fail(_ticker):
            nonlocal calls
            with lock:
                calls += 1
            started.set()
            if not release.wait(timeout=2):
                raise TimeoutError("test calculation was not released")
            raise ValueError("provider unavailable")

        with patch("analysis_coalescer.Future", TrackingFuture):
            with ThreadPoolExecutor(max_workers=waiter_count + 1) as pool:
                owner = pool.submit(coalesce_analysis, "NVDA", fail)
                self.assertTrue(started.wait(timeout=1))
                followers = [pool.submit(coalesce_analysis, "NVDA", fail) for _ in range(waiter_count)]
                try:
                    self.assertTrue(waiters_ready.wait(timeout=1))
                    self.assertEqual(calls, 1)
                finally:
                    release.set()

                for request in [owner, *followers]:
                    with self.assertRaisesRegex(ValueError, "provider unavailable"):
                        request.result(timeout=1)

        recovered = coalesce_analysis("NVDA", lambda ticker: {"company": {"ticker": ticker}})
        self.assertEqual(recovered, {"company": {"ticker": "NVDA"}})
        self.assertEqual(calls, 1, "the failed concurrent calculation must not be retained")

    def test_different_tickers_keep_independent_inflight_results(self):
        barrier = threading.Barrier(2)

        def calculate(ticker):
            barrier.wait(timeout=1)
            return {"company": {"ticker": ticker}}

        with ThreadPoolExecutor(max_workers=2) as pool:
            apple = pool.submit(coalesce_analysis, "AAPL", calculate)
            microsoft = pool.submit(coalesce_analysis, "MSFT", calculate)

        self.assertEqual(apple.result(), {"company": {"ticker": "AAPL"}})
        self.assertEqual(microsoft.result(), {"company": {"ticker": "MSFT"}})

    def test_cancelled_flight_cannot_remove_its_replacement(self):
        old_started = threading.Event()
        old_cancelled = threading.Event()
        old_release = threading.Event()
        new_started = threading.Event()
        new_release = threading.Event()
        replacement_joined = threading.Event()
        owner_cancel = threading.Event()
        new_request_cancel = threading.Event()
        calls = 0
        calls_lock = threading.Lock()

        class TrackingFuture(Future):
            def result(self, timeout=None):
                replacement_joined.set()
                return super().result(timeout)

        def old_runner(signal, _call):
            old_started.set()
            deadline = time.monotonic() + 1
            while not signal.is_set() and time.monotonic() < deadline:
                time.sleep(0.005)
            if not signal.is_set():
                raise TimeoutError("old flight was not cancelled")
            old_cancelled.set()
            if not old_release.wait(timeout=1):
                raise TimeoutError("old flight cleanup was not released")
            raise RuntimeError("old flight cancelled")

        def calculate(ticker):
            nonlocal calls
            with calls_lock:
                calls += 1
                attempt = calls
            new_started.set()
            if not new_release.wait(timeout=1):
                raise TimeoutError("replacement flight was not released")
            return {"company": {"ticker": ticker, "attempt": attempt}}

        with patch("analysis_coalescer.Future", TrackingFuture):
            with ThreadPoolExecutor(max_workers=3) as pool:
                old = pool.submit(
                    coalesce_analysis,
                    "RACE",
                    calculate,
                    cancel_event=owner_cancel,
                    run_with_cancellation=old_runner,
                )
                self.assertTrue(old_started.wait(timeout=1))
                owner_cancel.set()
                self.assertTrue(old_cancelled.wait(timeout=1))

                replacement = pool.submit(
                    coalesce_analysis,
                    "RACE",
                    calculate,
                    cancel_event=new_request_cancel,
                    run_with_cancellation=lambda _signal, call: call(),
                )
                self.assertTrue(new_started.wait(timeout=1))
                waiter = pool.submit(
                    coalesce_analysis,
                    "RACE",
                    calculate,
                    cancel_event=threading.Event(),
                    run_with_cancellation=lambda _signal, call: call(),
                )
                self.assertTrue(replacement_joined.wait(timeout=1))
                old_release.set()
                with self.assertRaisesRegex(RuntimeError, "old flight cancelled"):
                    old.result(timeout=1)
                self.assertEqual(calls, 1, "old-flight cleanup must not delete a replacement still in flight")
                new_release.set()
                expected = {"company": {"ticker": "RACE", "attempt": 1}}
                self.assertEqual(replacement.result(timeout=1), expected)
                self.assertEqual(waiter.result(timeout=1), expected)

    def test_identical_public_feeds_share_only_the_inflight_result(self):
        waiter_count = 4
        waiters_ready = threading.Event()
        started = threading.Event()
        release = threading.Event()
        calls = 0
        calls_lock = threading.Lock()
        waiting = 0

        class TrackingFuture(Future):
            def result(self, timeout=None):
                nonlocal waiting
                with calls_lock:
                    waiting += 1
                    if waiting == waiter_count:
                        waiters_ready.set()
                return super().result(timeout)

        def fetch():
            nonlocal calls
            with calls_lock:
                calls += 1
            started.set()
            if not release.wait(timeout=2):
                raise TimeoutError("test feed was not released")
            return {"items": [{"ticker": "AAPL"}]}

        with patch("analysis_coalescer.Future", TrackingFuture):
            with ThreadPoolExecutor(max_workers=waiter_count + 1) as pool:
                owner = pool.submit(coalesce_inflight, "daily_top_stocks", fetch)
                self.assertTrue(started.wait(timeout=1))
                followers = [
                    pool.submit(coalesce_inflight, "daily_top_stocks", fetch)
                    for _ in range(waiter_count)
                ]
                try:
                    self.assertTrue(waiters_ready.wait(timeout=1))
                    self.assertEqual(calls, 1)
                finally:
                    release.set()
                results = [owner.result(timeout=1), *(item.result(timeout=1) for item in followers)]

        self.assertEqual(calls, 1)
        self.assertTrue(all(result == results[0] for result in results))
        results[1]["items"][0]["ticker"] = "MUTATED"
        self.assertEqual(results[2]["items"][0]["ticker"], "AAPL")
        self.assertEqual(coalesce_inflight("daily_top_stocks", fetch), {"items": [{"ticker": "AAPL"}]})
        self.assertEqual(calls, 2, "a later request performs a fresh provider read")

    def test_different_public_feed_keys_do_not_share_results(self):
        barrier = threading.Barrier(2)

        def fetch(ticker):
            barrier.wait(timeout=1)
            return {"items": [{"ticker": ticker}]}

        with ThreadPoolExecutor(max_workers=2) as pool:
            movers = pool.submit(coalesce_inflight, "daily_market_movers", lambda: fetch("UP"))
            stocks = pool.submit(coalesce_inflight, "daily_top_stocks", lambda: fetch("AAPL"))

        self.assertEqual(movers.result(), {"items": [{"ticker": "UP"}]})
        self.assertEqual(stocks.result(), {"items": [{"ticker": "AAPL"}]})


if __name__ == "__main__":
    unittest.main()
