"""Tests for retry runtime functionality."""

import pytest

from evora.runtime import RetryPolicy, _compute_delay_ms, with_retries


def test_compute_delay_none_strategy():
    """Test delay computation with 'none' strategy."""
    policy = RetryPolicy(strategy="none", base_delay_ms=100)
    assert _compute_delay_ms(policy, 1) == 0
    assert _compute_delay_ms(policy, 5) == 0


def test_compute_delay_fixed_strategy():
    """Test delay computation with 'fixed' strategy."""
    policy = RetryPolicy(strategy="fixed", base_delay_ms=100, jitter=False)
    assert _compute_delay_ms(policy, 1) == 100
    assert _compute_delay_ms(policy, 2) == 100
    assert _compute_delay_ms(policy, 5) == 100


def test_compute_delay_exponential_strategy():
    """Test delay computation with 'exponential' strategy."""
    policy = RetryPolicy(
        strategy="exponential", base_delay_ms=100, max_delay_ms=10000, jitter=False
    )
    assert _compute_delay_ms(policy, 1) == 100  # 100 * 2^0
    assert _compute_delay_ms(policy, 2) == 200  # 100 * 2^1
    assert _compute_delay_ms(policy, 3) == 400  # 100 * 2^2
    assert _compute_delay_ms(policy, 4) == 800  # 100 * 2^3


def test_compute_delay_exponential_with_max():
    """Test exponential backoff respects max_delay_ms."""
    policy = RetryPolicy(strategy="exponential", base_delay_ms=100, max_delay_ms=500, jitter=False)
    assert _compute_delay_ms(policy, 1) == 100
    assert _compute_delay_ms(policy, 2) == 200
    assert _compute_delay_ms(policy, 3) == 400
    assert _compute_delay_ms(policy, 4) == 500  # capped at max
    assert _compute_delay_ms(policy, 5) == 500  # capped at max


def test_compute_delay_with_jitter():
    """Test delay computation with jitter enabled."""
    policy = RetryPolicy(strategy="exponential", base_delay_ms=100, jitter=True)

    # With jitter, delay should be between 0.7x and 1.3x of base
    delays = [_compute_delay_ms(policy, 1) for _ in range(10)]

    # All delays should be in the jitter range
    for delay in delays:
        assert 70 <= delay <= 130


def test_compute_delay_jitter_non_negative():
    """Test that jitter never produces negative delays."""
    policy = RetryPolicy(strategy="exponential", base_delay_ms=1, jitter=True)
    delay = _compute_delay_ms(policy, 1)
    assert delay >= 0


@pytest.mark.asyncio
async def test_with_retries_success_first_attempt():
    """Test successful operation on first attempt."""
    attempts = []

    async def run_once(attempt: int):
        attempts.append(attempt)

    policy = RetryPolicy(max_attempts=3)
    await with_retries(policy=policy, run_once=run_once)

    assert attempts == [1]


@pytest.mark.asyncio
async def test_with_retries_success_after_retries():
    """Test successful operation after retries."""
    attempts = []

    async def run_once(attempt: int):
        attempts.append(attempt)
        if attempt < 3:
            raise ValueError("Temporary failure")

    policy = RetryPolicy(strategy="none", max_attempts=5)
    await with_retries(policy=policy, run_once=run_once)

    assert attempts == [1, 2, 3]


@pytest.mark.asyncio
async def test_with_retries_exhausted():
    """Test retry exhaustion raises the last exception."""
    attempts = []

    async def run_once(attempt: int):
        attempts.append(attempt)
        raise ValueError(f"Failure on attempt {attempt}")

    policy = RetryPolicy(strategy="none", max_attempts=3)

    with pytest.raises(ValueError, match="Failure on attempt 3"):
        await with_retries(policy=policy, run_once=run_once)

    assert attempts == [1, 2, 3]


@pytest.mark.asyncio
async def test_with_retries_fixed_delay():
    """Test retries with fixed delay strategy."""
    attempts = []

    async def run_once(attempt: int):
        attempts.append(attempt)
        if attempt < 2:
            raise ValueError("Temporary failure")

    policy = RetryPolicy(strategy="fixed", base_delay_ms=10, max_attempts=3, jitter=False)
    await with_retries(policy=policy, run_once=run_once)

    assert attempts == [1, 2]


@pytest.mark.asyncio
async def test_with_retries_exponential_delay():
    """Test retries with exponential delay strategy."""
    attempts = []

    async def run_once(attempt: int):
        attempts.append(attempt)
        if attempt < 3:
            raise ValueError("Temporary failure")

    policy = RetryPolicy(strategy="exponential", base_delay_ms=10, max_attempts=5, jitter=False)
    await with_retries(policy=policy, run_once=run_once)

    assert attempts == [1, 2, 3]


def test_retry_policy_defaults():
    """Test RetryPolicy default values."""
    policy = RetryPolicy()
    assert policy.strategy == "exponential"
    assert policy.max_attempts == 5
    assert policy.base_delay_ms == 200
    assert policy.max_delay_ms == 30_000
    assert policy.jitter is True
