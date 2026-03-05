from __future__ import annotations

import time
from collections import defaultdict
from typing import Any


class SimpleMetricsTelemetry:
    """
    Minimal in-memory metrics implementation.

    Useful for:
    - Local development
    - Testing
    - Reference implementation
    """

    def __init__(self) -> None:
        self.counters: defaultdict[str, int] = defaultdict(int)
        self.durations: defaultdict[str, list[float]] = defaultdict(list)

    # ----------------------------
    # Helpers
    # ----------------------------

    def _inc(self, name: str, value: int = 1) -> None:
        self.counters[name] += value

    # ----------------------------
    # Telemetry API
    # ----------------------------

    def on_consume_start(
        self,
        *,
        service: str,
        event_type: str,
        handler: str,
        event_id: str,
        attempt: int,
        attrs: dict[str, Any],
    ) -> Any:
        return time.perf_counter()

    def on_consume_end(
        self,
        token: Any,
        *,
        outcome: str,
        error: Exception | None = None,
    ) -> None:
        duration = time.perf_counter() - token
        self._inc(f"consume.{outcome}")
        self.durations["handler_duration_seconds"].append(duration)

        if error:
            self._inc("consume.errors")

    def on_retry_scheduled(
        self,
        *,
        service: str,
        event_type: str,
        handler: str,
        event_id: str,
        attempt: int,
        next_attempt: int,
    ) -> None:
        self._inc("retry.scheduled")

    def on_publish(
        self,
        *,
        service: str,
        event_type: str,
        channel: str,
    ) -> None:
        self._inc("publish.total")
