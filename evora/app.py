from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Optional, Type

from evora.observability.telemetry import NoopTelemetry, Telemetry

from .brokers.base import BaseBroker, Message
from .core import Envelope, Event, Registry
from .errors import ContractError, FatalError, RetryableError
from .idempotency import IdempotencyPolicy, IdempotencyStore
from .runtime import RetryPolicy


@dataclass
class HandlerSpec:
    event_cls: Type[Event]
    fn: Callable[..., Awaitable[None]]
    channel: Optional[str]
    retry: RetryPolicy
    dlq: bool
    idempotency: IdempotencyPolicy


class Context:
    def __init__(self, *, message: Message, envelope: Envelope, handler_name: str) -> None:
        self.message = message
        self.envelope = envelope
        self.handler_name = handler_name
        self.traceparent = envelope.traceparent
        # You can add logger, metrics, correlation IDs, attempt, etc later.


def subscribe(
    event_cls: Type[Event],
    *,
    channel: str | None = None,
    retry: RetryPolicy | str = "exponential",
    max_attempts: int = 5,
    dlq: bool = True,
    idempotency: IdempotencyPolicy | None = None,  # REQUIRED in strict mode
) -> Callable[[Callable[..., Awaitable[None]]], Callable[..., Awaitable[None]]]:
    """
    Strict mode: idempotency policy is required; missing => startup error.
    """

    def decorator(fn: Callable[..., Awaitable[None]]) -> Callable[..., Awaitable[None]]:
        setattr(fn, "__evora_spec__", {
            "event_cls": event_cls,
            "channel": channel,
            "retry": RetryPolicy(strategy=retry, max_attempts=max_attempts)
            if isinstance(retry, str)
            else retry,
            "dlq": dlq,
            "idempotency": idempotency,
        })
        return fn

    return decorator


class App:
    def __init__(
        self,
        *,
        broker: BaseBroker,
        source: str,
        idempotency_store: IdempotencyStore,
        telemetry: Telemetry | None = None,
        dlq_suffix: str = ".dlq",
        strict: bool = True,
    ) -> None:
        self.broker = broker
        self.source = source
        self.dlq_suffix = dlq_suffix
        self.strict = strict

        self.registry = Registry()
        self.idempotency_store = idempotency_store
        self.telemetry: Telemetry = telemetry or NoopTelemetry()

        self._handlers: list[HandlerSpec] = []

    def add_handler(self, fn: Callable[..., Awaitable[None]]) -> None:
        spec = getattr(fn, "__evora_spec__", None)
        if not spec:
            raise ValueError("Function is not decorated with @subscribe(...)")

        event_cls: Type[Event] = spec["event_cls"]

        # Enforce explicit event version (strict)
        version = getattr(event_cls, "__version__", None)
        if self.strict and (version is None or not isinstance(version, int)):
            raise ValueError(
                f"{event_cls.__name__} must declare an explicit integer __version__ in strict mode."
            )

        # Enforce idempotency policy (strict)
        idem: IdempotencyPolicy | None = spec.get("idempotency")
        if self.strict and idem is None:
            raise ValueError(
                f"Handler {fn.__module__}.{fn.__name__} must declare "
                f"idempotency=IdempotencyPolicy(...) in strict mode."
            )

        # Registry needs event type → model mapping
        self.registry.register(event_cls)

        self._handlers.append(
            HandlerSpec(
                event_cls=event_cls,
                fn=fn,
                channel=spec["channel"],
                retry=spec["retry"],
                dlq=spec["dlq"],
                idempotency=idem or IdempotencyPolicy(),  # non-strict fallback
            )
        )

    async def publish(
        self,
        event: Event,
        *,
        channel: str | None = None,
        key: str | None = None,
        subject: str | None = None,
        traceparent: str | None = None,
    ) -> None:
        ch = channel or event.event_type()
        envelope = event.to_envelope(
            source=self.source,
            subject=subject,
            traceparent=traceparent,
            meta={"partition_key": key} if key else None,
        )
        raw = self.registry.encode(envelope)
        await self.broker.publish(ch, value=raw, key=key, headers={})
        self.telemetry.on_publish(
            service=self.source,
            event_type=event.event_type(),
            channel=ch,
        )

    async def _send_dlq(
        self,
        *,
        handler: HandlerSpec,
        msg: Message,
        envelope: Envelope,
        handler_name: str,
        error: Exception,
    ) -> None:
        dlq_channel = (handler.channel or handler.event_cls.event_type()) + self.dlq_suffix
        envelope.meta = {
            **(envelope.meta or {}),
            "error_type": type(error).__name__,
            "error_message": str(error),
            "failed_handler": handler_name,
            "original_channel": msg.channel,
        }
        await self.broker.publish(
            dlq_channel, value=self.registry.encode(envelope), key=msg.key, headers=msg.headers
        )

    def _classify_error(self, err: Exception) -> str:
        """
        Returns: "contract" | "retryable" | "fatal" | "unknown"
        """
        if isinstance(err, ContractError):
            return "contract"
        if isinstance(err, FatalError):
            return "fatal"
        if isinstance(err, RetryableError):
            return "retryable"
        return "unknown"

    def _get_attempt(self, msg: Message, envelope: Envelope) -> int:
        if getattr(msg, "attempt", None):
            return int(msg.attempt)
        if envelope.meta and envelope.meta.get("attempt"):
            return int(envelope.meta["attempt"])
        return 1

    async def _handle_message(self, msg: Message) -> None:
        # Decode/validate envelope + event model
        try:
            envelope, event = self.registry.decode(msg.value)
        except Exception:
            # Invalid payloads are contract failures — send to DLQ of the channel itself
            # (no specific handler yet). This is strict and operationally useful.
            # If you prefer a global DLQ, you can route to "evora.contract.dlq".
            fake_dlq = msg.channel + self.dlq_suffix
            # Best-effort: wrap minimal envelope-like metadata
            # but keep the raw bytes for inspection (truncate if needed)
            try:
                # If bytes aren't json, this will still be a "contract" DLQ message.
                await self.broker.publish(
                    fake_dlq,
                    value=msg.value,
                    key=msg.key,
                    headers={**msg.headers, "x-error": "contract"},
                )
            finally:
                return

        matching = [h for h in self._handlers if h.event_cls.event_type() == envelope.type]
        if not matching:
            # Unknown event type for this service: treat as contract issue -> DLQ
            # (Or you might want to ignore. Strict mode: DLQ is safer.)
            await self.broker.publish(
                msg.channel + self.dlq_suffix,
                value=msg.value,
                key=msg.key,
                headers={**msg.headers, "x-error": "no-matching-handler"},
            )
            return

        for h in matching:
            handler_name = f"{h.fn.__module__}.{h.fn.__name__}"
            ctx = Context(message=msg, envelope=envelope, handler_name=handler_name)

            token = self.telemetry.on_consume_start(
                service=self.source,
                event_type=envelope.type,
                handler=handler_name,
                event_id=envelope.id,
                attempt=int(envelope.meta.get("attempt", 1)) if envelope.meta else 1,
                attrs={
                    "channel": msg.channel,
                    "schema": envelope.dataschema,
                },
            )

            # Idempotency gate (mandatory in strict mode)
            scope = handler_name
            if h.idempotency.mode != "event_id":
                # Reserved for future modes
                self.telemetry.on_consume_end(
                    token, outcome="error", error=ValueError("Unsupported idempotency mode")
                )
                raise ValueError(f"Unsupported idempotency mode: {h.idempotency.mode}")

            try:
                if await self.idempotency_store.seen(scope=scope, event_id=envelope.id):
                    self.telemetry.on_consume_end(token, outcome="skip", error=None)
                    continue

                async def run_once(attempt: int) -> None:
                    # Track attempt in envelope meta for downstream tooling
                    envelope.meta = {**(envelope.meta or {}), "attempt": attempt}
                    await h.fn(event, ctx)

                attempt = self._get_attempt(msg, envelope)

                try:
                    # Track attempt for downstream tooling
                    envelope.meta = {**(envelope.meta or {}), "attempt": attempt}
                    await h.fn(event, ctx)

                except Exception as e:
                    category = self._classify_error(e)

                    # Durable retry if:
                    # - retryable/unknown
                    # - attempts left
                    # - broker supports schedule_retry
                    can_retry = (
                        category in ("retryable", "unknown") and attempt < h.retry.max_attempts
                    )

                    if can_retry:
                        await self.broker.schedule_retry(
                            msg=msg,
                            raw_value=msg.value,
                            headers=msg.headers,
                            attempt=attempt,
                            error_type=type(e).__name__,
                            error_message=str(e),
                        )
                        self.telemetry.on_retry_scheduled(
                            service=self.source,
                            event_type=envelope.type,
                            handler=handler_name,
                            event_id=envelope.id,
                            attempt=attempt,
                            next_attempt=attempt + 1,
                        )
                        self.telemetry.on_consume_end(token, outcome="retry", error=e)
                        continue

                    # Otherwise DLQ (contract/fatal OR retry exhausted)
                    if h.dlq:
                        await self._send_dlq(
                            handler=h,
                            msg=msg,
                            envelope=envelope,
                            handler_name=handler_name,
                            error=e,
                        )
                        self.telemetry.on_consume_end(token, outcome="dlq", error=e)
                    else:
                        self.telemetry.on_consume_end(token, outcome="error", error=e)
                    continue

                # Success: mark idempotency
                await self.idempotency_store.mark_seen(
                    scope=scope, event_id=envelope.id, ttl_seconds=h.idempotency.ttl_seconds
                )
                self.telemetry.on_consume_end(token, outcome="success", error=None)

            except Exception as e:
                # If idempotency store fails or unexpected pipeline issues occur
                if h.dlq:
                    await self._send_dlq(
                        handler=h, msg=msg, envelope=envelope, handler_name=handler_name, error=e
                    )
                    self.telemetry.on_consume_end(token, outcome="dlq", error=e)
                else:
                    self.telemetry.on_consume_end(token, outcome="error", error=e)

    async def run(self) -> None:
        channels = list({(h.channel or h.event_cls.event_type()) for h in self._handlers})
        await self.broker.run_consumer(channels, self._handle_message, consumer_name=self.source)
