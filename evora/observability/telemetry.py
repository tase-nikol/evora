from __future__ import annotations

from typing import Any, Protocol


class Telemetry(Protocol):
    def on_consume_start(
        self,
        *,
        service: str,
        event_type: str,
        handler: str,
        event_id: str,
        attempt: int,
        attrs: dict[str, Any],
    ) -> Any: ...

    def on_consume_end(
        self,
        token: Any,
        *,
        outcome: str,  # "success" | "retry" | "dlq" | "skip" | "error"
        error: Exception | None = None,
    ) -> None: ...

    def on_retry_scheduled(
        self,
        *,
        service: str,
        event_type: str,
        handler: str,
        event_id: str,
        attempt: int,
        next_attempt: int,
    ) -> None: ...

    def on_publish(
        self,
        *,
        service: str,
        event_type: str,
        channel: str,
    ) -> None: ...


class NoopTelemetry:
    def on_consume_start(
        self, *, event_type: str, handler: str, event_id: str, attrs: dict[str, Any]
    ) -> Any:
        return None

    def on_consume_end(self, token: Any, *, outcome: str, error: Exception | None = None) -> None:
        return None
