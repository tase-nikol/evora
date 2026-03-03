import os
import sys

import anyio

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from evora.app import App, subscribe
from evora.brokers.memory import MemoryBroker
from evora.core import Event


class UserCreated(Event):
    user_id: int
    email: str


@subscribe(UserCreated, retry="exponential", max_attempts=3, dlq=True)
async def handle_user_created(event: UserCreated, ctx):
    print("HANDLER got:", event)
    # Simulate failure:
    if event.user_id == 1:
        raise RuntimeError("boom")


async def main():
    broker = MemoryBroker()
    app = App(broker=broker, source="users-service")
    app.add_handler(handle_user_created)

    async with anyio.create_task_group() as tg:
        tg.start_soon(app.run)
        await app.publish(UserCreated(user_id=1, email="a@b.com"))
        await app.publish(UserCreated(user_id=2, email="c@d.com"))

        await anyio.sleep(1)


if __name__ == "__main__":
    anyio.run(main)
