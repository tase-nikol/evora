from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Awaitable, Callable

import anyio


@dataclass
class RetryPolicy:
    """
    Configuration for retry behavior.

    Attributes:
        strategy: The retry strategy to use ("none", "fixed", or "exponential").
        max_attempts: Maximum number of retry attempts.
        base_delay_ms: Base delay in milliseconds between attempts.
        max_delay_ms: Maximum delay in milliseconds between attempts.
        jitter: Whether to apply random jitter to the delay.
    """

    strategy: str = "exponential"  # none|fixed|exponential
    max_attempts: int = 5
    base_delay_ms: int = 200
    max_delay_ms: int = 30_000
    jitter: bool = True


def _compute_delay_ms(policy: RetryPolicy, attempt: int) -> int:
    """
    Compute the delay in milliseconds before the next retry attempt.

    Args:
        policy: The retry policy configuration.
        attempt: The current attempt number (1-based).

    Returns:
        The delay in milliseconds.
    """
    if policy.strategy == "none":
        return 0
    if policy.strategy == "fixed":
        delay = policy.base_delay_ms
    else:
        # exponential backoff: base * 2^(attempt-1)
        delay = policy.base_delay_ms * (2 ** (attempt - 1))
    delay = min(delay, policy.max_delay_ms)
    if policy.jitter:
        delay = int(delay * random.uniform(0.7, 1.3))
    return max(0, delay)


async def with_retries(
    *,
    policy: RetryPolicy,
    run_once: Callable[[int], Awaitable[None]],
) -> None:
    """
    Execute an async operation with retries according to the given policy.

    Args:
        policy: The retry policy to use.
        run_once: An async callable that takes the attempt number and performs the operation.

    Raises:
        Exception: Propagates the last exception if max_attempts is reached.
    """
    attempt = 1
    while True:
        try:
            await run_once(attempt)
            return
        except Exception:
            if attempt >= policy.max_attempts:
                raise
            attempt += 1
            delay_ms = _compute_delay_ms(policy, attempt)
            await anyio.sleep(delay_ms / 1000.0)
