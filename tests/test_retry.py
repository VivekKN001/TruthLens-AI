import pytest

from utils.retry import RetryConfig, with_retry


def test_with_retry_returns_result_on_first_success():
    calls = []

    @with_retry(config=RetryConfig(max_attempts=3, base_delay=0))
    def always_succeeds():
        calls.append(1)
        return "ok"

    assert always_succeeds() == "ok"
    assert len(calls) == 1


def test_with_retry_succeeds_after_transient_failures():
    attempts = {"n": 0}

    @with_retry(config=RetryConfig(max_attempts=3, base_delay=0))
    def fails_twice_then_succeeds():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ValueError("transient")
        return "recovered"

    assert fails_twice_then_succeeds() == "recovered"
    assert attempts["n"] == 3


def test_with_retry_raises_after_exhausting_attempts():
    @with_retry(config=RetryConfig(max_attempts=2, base_delay=0))
    def always_fails():
        raise ValueError("permanent")

    with pytest.raises(ValueError):
        always_fails()


def test_with_retry_uses_fallback_when_all_attempts_fail():
    @with_retry(config=RetryConfig(max_attempts=2, base_delay=0), fallback=lambda: "fallback value")
    def always_fails():
        raise ValueError("permanent")

    assert always_fails() == "fallback value"
