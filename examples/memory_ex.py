import os
import sys

import anyio

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from pydantic import BaseModel

from evora.app import App, subscribe
from evora.brokers.memory import MemoryBroker
from evora.core import Event
from evora.idempotency import IdempotencyPolicy
from evora.idempotency.base import IdempotencyStore


class MemoryIdempotencyStore(IdempotencyStore):
    """Simple in-memory idempotency store for testing"""

    def __init__(self):
        self._store = {}

    async def check_and_set(self, key: str, ttl_seconds: int) -> bool:
        if key in self._store:
            return False
        self._store[key] = True
        return True

    async def close(self):
        pass


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
    retry="exponential",
    max_attempts=3,
    dlq=True,
    idempotency=IdempotencyPolicy(mode="event_id", ttl_seconds=3600),
)
async def handle_user_created(event: UserCreated, ctx):
    print("HANDLER got:", event)
    # Simulate failure:
    if event.data.user_id == 1:
        raise RuntimeError("boom")


async def main():
    broker = MemoryBroker()
    idempotency = MemoryIdempotencyStore()
    app = App(broker=broker, source="users-service", idempotency_store=idempotency)
    app.add_handler(handle_user_created)
    async with anyio.create_task_group() as tg:
        tg.start_soon(app.run)
        await app.publish(UserCreated(data=UserCreated.Data(user_id=1, email="a@b.com")))
        await app.publish(UserCreated(data=UserCreated.Data(user_id=2, email="c@d.com")))
        await anyio.sleep(1)


if __name__ == "__main__":
    anyio.run(main())
