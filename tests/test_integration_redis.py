import asyncio

import pytest
import redis.asyncio as redis
from pydantic import BaseModel

from evora.app import App, subscribe
from evora.brokers.redis_streams import RedisStreamsBroker
from evora.core import Event
from evora.errors import RetryableError
from evora.idempotency import IdempotencyPolicy
from evora.idempotency_redis import RedisIdempotencyStore
from evora.observability import SimpleMetricsTelemetry


class TestEvent(Event):
    __version__ = 1

    class Data(BaseModel):
        value: int | None = None

    data: Data

    @classmethod
    def event_type(cls):
        return "integration.events"


@pytest.mark.asyncio
async def test_retry_then_success():
    r = redis.Redis(host="localhost", port=4379, decode_responses=False)

    telemetry = SimpleMetricsTelemetry()

    broker = RedisStreamsBroker(
        client=r,
        group_id="integration-test",
        poison_idle_ms=2000,
    )

    idempotency = RedisIdempotencyStore(client=r)

    app = App(
        broker=broker,
        source="integration-service",
        idempotency_store=idempotency,
        telemetry=telemetry,
        strict=True,
    )

    state = {"calls": 0}

    @subscribe(TestEvent, max_attempts=2, idempotency=IdempotencyPolicy(mode="event_id"))
    async def handler(event, ctx):
        state["calls"] += 1
        if state["calls"] == 1:
            raise RetryableError("temporary")

    app.add_handler(handler)
    await app.publish(TestEvent(data=TestEvent.Data()))

    # Run consumer in background briefly
    async def run():
        await app.run()

    task = asyncio.create_task(run())
    await asyncio.sleep(2)
    task.cancel()

    assert telemetry.counters["retry.scheduled"] >= 1
    assert telemetry.counters["consume.success"] >= 1
