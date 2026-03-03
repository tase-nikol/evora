import os
import sys

import anyio
import redis.asyncio as redis

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from evora.app import App, subscribe
from evora.brokers.redis_streams import RedisStreamsBroker
from evora.core import Event
from evora.errors import RetryableError
from evora.idempotency import IdempotencyPolicy
from evora.idempotency_redis import RedisIdempotencyStore


class UserCreated(Event):
    __version__ = 1
    user_id: int
    email: str


@subscribe(
    UserCreated,
    channel="users.events",
    idempotency=IdempotencyPolicy(ttl_seconds=3600),
    retry="exponential",
    max_attempts=5,
    dlq=True,
)
async def handle_user_created(event: UserCreated, ctx):
    print("got", event)
    if event.user_id == 1:
        raise RetryableError("temporary failure")


async def main():
    r = redis.Redis(host="localhost", port=4379, decode_responses=False)

    broker = RedisStreamsBroker(client=r, group_id="users-svc")
    idem = RedisIdempotencyStore(client=r)

    app = App(broker=broker, source="users-svc", idempotency_store=idem, strict=True)
    app.add_handler(handle_user_created)

    async with anyio.create_task_group() as tg:
        tg.start_soon(app.run)
        await anyio.sleep(0.3)
        await app.publish(UserCreated(user_id=1, email="a@b.com"), channel="users.events")
        await app.publish(UserCreated(user_id=2, email="c@d.com"), channel="users.events")
        await anyio.sleep(5)


if __name__ == "__main__":
    anyio.run(main)

# expected behavior
# After system stabilizes:
#
# users.events
#
# Contains historical entries (original + retries)
#
# users.events.retry.z
#
# Empty
#
# users.events.dlq
#
# Contains exactly 1 entry (for user_id=1)
#
# XPENDING users.events users-svc
#
# Empty
