import os
import sys

import anyio
import redis.asyncio as redis

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from pydantic import BaseModel

from evora.app import App, subscribe
from evora.brokers.redis_streams import RedisStreamsBroker
from evora.core import Event
from evora.errors import RetryableError
from evora.idempotency import IdempotencyPolicy
from evora.idempotency.redis_store import RedisIdempotencyStore


class UserCreated(Event):
    __version__ = 1

    class Data(BaseModel):
        user_id: int
        email: str

    data: Data

    @classmethod
    def event_type(cls):
        return "users.created"


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
    if event.data.user_id == 1:
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

        # Publish first event (user_id=1, will fail and retry)
        await app.publish(
            UserCreated(data=UserCreated.Data(user_id=1, email="a@b.com")),
            channel="users.events",
        )

        # Publish second event (user_id=2, will succeed)
        await app.publish(
            UserCreated(data=UserCreated.Data(user_id=2, email="c@d.com")),
            channel="users.events",
        )

        await anyio.sleep(2)
        tg.cancel_scope.cancel()


if __name__ == "__main__":
    anyio.run(main())
