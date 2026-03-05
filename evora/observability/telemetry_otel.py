from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from opentelemetry.metrics import Meter
    from opentelemetry.trace import Span, Tracer

try:
    from opentelemetry import metrics, trace
except ImportError:
    pass  # opentelemetry is an optional dependency


class OpenTelemetryTelemetry:
    """
    OpenTelemetry implementation for Evora.
    """

    def __init__(self, tracer: Tracer | None = None, meter: Meter | None = None) -> None:
        self.tracer = tracer or trace.get_tracer("evora")
        self.meter = meter or metrics.get_meter("evora")

        # Metrics
        self._consume_counter = self.meter.create_counter("evora_consume_total")
        self._retry_counter = self.meter.create_counter("evora_retry_total")
        self._publish_counter = self.meter.create_counter("evora_publish_total")
        self._duration_histogram = self.meter.create_histogram("evora_handler_duration_seconds")

    def on_consume_start(
        self,
        *,
        service: str,
        event_type: str,
        handler: str,
        event_id: str,
        attempt: int,
        attrs: dict[str, Any],
    ) -> Span:
        span = self.tracer.start_span(f"evora.consume {event_type}")
        span.set_attribute("evora.service", service)
        span.set_attribute("evora.handler", handler)
        span.set_attribute("evora.event_id", event_id)
        span.set_attribute("evora.attempt", attempt)
        return span

    def on_consume_end(
        self,
        span: Span,
        *,
        outcome: str,
        error: Exception | None = None,
    ) -> None:
        span.set_attribute("evora.outcome", outcome)

        if error:
            span.record_exception(error)
            span.set_status(trace.Status(trace.StatusCode.ERROR))

        span.end()

        self._consume_counter.add(1, {"outcome": outcome})

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
        self._retry_counter.add(1)

    def on_publish(
        self,
        *,
        service: str,
        event_type: str,
        channel: str,
    ) -> None:
        self._publish_counter.add(1)
