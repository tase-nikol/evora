"""
Poison Message Handling Example

This demonstrates:
1. Normal message processing
2. Retryable errors (app-level retry)
3. Simulated crash (poison detection)
4. DLQ routing after max deliveries
"""

import asyncio
from dataclasses import dataclass

import redis.asyncio as redis
from pydantic import BaseModel

from evora.app import App, subscribe
from evora.brokers.redis_streams import RedisStreamsBroker
from evora.core import Event
from evora.errors import RetryableError
from evora.idempotency import IdempotencyPolicy
from evora.idempotency_redis import RedisIdempotencyStore

# ============================================================================
# Domain Events
# ============================================================================


class UserEvent(Event):
    __version__ = 1

    class Data(BaseModel):
        user_id: int
        action: str
        metadata: dict | None = None

    data: Data

    @classmethod
    def event_type(cls) -> str:
        return "users.events"


# ============================================================================
# Handler Configuration
# ============================================================================


@dataclass
class HandlerConfig:
    """Control handler behavior for demonstration"""

    simulate_crash_on_user: int | None = None  # Simulate crash for this user_id
    simulate_hang_on_user: int | None = None  # Simulate hang for this user_id
    fail_until_attempt: int | None = None  # Fail with RetryableError until attempt N


CONFIG = HandlerConfig()


# ============================================================================
# Application Setup
# ============================================================================


async def create_app() -> App:
    """Create the event application with Redis broker"""
    r = redis.Redis(host="localhost", port=4379, decode_responses=False)

    broker = RedisStreamsBroker(
        client=r,
        group_id="poison-example",
        # Poison detection config (aggressive for demo)
        poison_idle_ms=10_000,  # 10 seconds idle → reclaim
        poison_max_deliveries=5,  # 5 deliveries → DLQ
        poison_check_interval_s=5.0,  # Check every 5s
    )

    idempotency_store = RedisIdempotencyStore(client=r)

    app = App(
        broker=broker,
        source="poison-example-service",
        idempotency_store=idempotency_store,
        strict=True,
    )

    # Register handler
    app.add_handler(handle_user_event)

    return app


# ============================================================================
# Event Handler
# ============================================================================


@subscribe(
    UserEvent,
    retry="exponential",
    max_attempts=3,
    dlq=True,
    idempotency=IdempotencyPolicy(mode="event_id", ttl_seconds=86400),
)
async def handle_user_event(event: UserEvent, ctx) -> None:
    """
    Handler with configurable failure modes:
    - Normal processing
    - Retryable errors (app-level retry)
    - Crash simulation (poison detection triggers)
    - Hang simulation (poison detection triggers)
    """
    user_id = event.data.user_id
    action = event.data.action
    attempt = getattr(ctx.message, "attempt", 1)

    print(f"📨 Processing user_id={user_id}, action={action}, attempt={attempt}")

    # Scenario 1: Simulate crash (no ACK, message stays in PEL)
    if CONFIG.simulate_crash_on_user == user_id:
        print(f"💥 CRASH SIMULATION for user_id={user_id}")
        import sys

        sys.exit(1)  # Hard crash - message enters PEL

    # Scenario 2: Simulate hang (no ACK, no error, just stuck)
    if CONFIG.simulate_hang_on_user == user_id:
        print(f"⏳ HANG SIMULATION for user_id={user_id}")
        await asyncio.sleep(999999)  # Never returns - poison checker will reclaim

    # Scenario 3: Fail with retryable error until attempt N
    if CONFIG.fail_until_attempt and attempt <= CONFIG.fail_until_attempt:
        print(f"🔄 Simulating retryable failure (attempt {attempt}/{CONFIG.fail_until_attempt})")
        raise RetryableError(f"Simulated transient failure for user_id={user_id}")

    # Normal processing
    print(f"✅ Successfully processed user_id={user_id}, action={action}")


# ============================================================================
# Demo Scenarios
# ============================================================================


async def publish_event(app: App, user_id: int, action: str) -> None:
    """Publish a test event"""
    event = UserEvent(
        data=UserEvent.Data(user_id=user_id, action=action, metadata={"source": "demo"})
    )
    await app.publish(event, key=f"user-{user_id}")
    print(f"📤 Published: user_id={user_id}, action={action}")


async def scenario_normal_processing(app: App) -> None:
    """Scenario 1: Normal message processing"""
    print("\n" + "=" * 70)
    print("SCENARIO 1: Normal Processing")
    print("=" * 70)

    await publish_event(app, user_id=1, action="login")
    await publish_event(app, user_id=2, action="logout")

    print("\n✅ Expected: Messages processed successfully")


async def scenario_retryable_error(app: App) -> None:
    """Scenario 2: Retryable error with app-level retry"""
    print("\n" + "=" * 70)
    print("SCENARIO 2: Retryable Error (App-Level Retry)")
    print("=" * 70)

    CONFIG.fail_until_attempt = 2  # Fail twice, succeed on attempt 3

    await publish_event(app, user_id=3, action="purchase")

    print("\n✅ Expected:")
    print("   - Handler fails on attempt 1 → schedule_retry")
    print("   - Handler fails on attempt 2 → schedule_retry")
    print("   - Handler succeeds on attempt 3 → ACK")
    print("   - Total: 3 deliveries (app retry, not poison)")


async def scenario_crash_recovery(app: App) -> None:
    """Scenario 3: Simulated crash → poison detection → reclaim"""
    print("\n" + "=" * 70)
    print("SCENARIO 3: Crash + Poison Detection")
    print("=" * 70)

    print("\n⚠️  This will crash the process!")
    print("   Restart the consumer to see poison checker reclaim the message.\n")

    CONFIG.simulate_crash_on_user = 99

    await publish_event(app, user_id=99, action="critical_action")

    print("\n✅ Expected:")
    print("   1. Process crashes (no ACK)")
    print("   2. Message enters PEL")
    print("   3. After 10s idle → poison checker XCLAIMs")
    print("   4. Message reprocessed by new consumer")
    print("   5. After 5 attempts (broker poison limit) → DLQ routing")


async def scenario_hang_recovery(app: App) -> None:
    """Scenario 4: Simulated hang → poison detection → reclaim"""
    print("\n" + "=" * 70)
    print("SCENARIO 4: Hang + Poison Detection")
    print("=" * 70)

    CONFIG.simulate_hang_on_user = 88

    await publish_event(app, user_id=88, action="slow_action")

    print("\n✅ Expected:")
    print("   1. Handler hangs forever (no ACK, no error)")
    print("   2. Message stays in PEL")
    print("   3. After 10s idle → poison checker XCLAIMs")
    print("   4. Message reprocessed (will hang again)")
    print("   5. After 5 deliveries → DLQ routing")


async def inspect_dlq(app: App) -> None:
    """Inspect the DLQ for poisoned messages"""
    print("\n" + "=" * 70)
    print("DLQ INSPECTION")
    print("=" * 70)

    broker = app.broker
    dlq_channel = "users.events.dlq"

    try:
        # Get DLQ length
        length = await broker.client.xlen(dlq_channel)
        print(f"\n📊 DLQ size: {length} messages")

        if length > 0:
            # Fetch last 10 messages
            messages = await broker.client.xrevrange(dlq_channel, count=10)

            print("\n🔍 Recent DLQ messages:")
            for msg_id, fields in messages:
                print(f"\n  Message ID: {msg_id}")
                for key, value in fields.items():
                    key_str = key.decode("utf-8") if isinstance(key, bytes) else str(key)
                    val_str = value.decode("utf-8") if isinstance(value, bytes) else str(value)
                    print(f"    {key_str}: {val_str}")

    except Exception as e:
        print(f"❌ Error inspecting DLQ: {e}")


# ============================================================================
# Main Entry Point
# ============================================================================


async def run_consumer():
    """Run the consumer (listens for events)"""
    app = await create_app()

    print("\n" + "=" * 70)
    print("🚀 STARTING CONSUMER")
    print("=" * 70)
    print(f"Service: {app.source}")
    print("Poison detection:")
    print(f"  - Idle threshold: {app.broker.poison_idle_ms}ms")
    print(f"  - Max deliveries: {app.broker.poison_max_deliveries}")
    print(f"  - Check interval: {app.broker.poison_check_interval_s}s")
    print("=" * 70)

    await app.run()


async def run_publisher():
    """Run the publisher (sends test events)"""
    app = await create_app()

    print("\n" + "=" * 70)
    print("📤 RUNNING DEMO SCENARIOS")
    print("=" * 70)

    # Choose scenario to run
    import sys

    if len(sys.argv) < 2:
        print("\nUsage:")
        print("  python examples/redis_poison_demo.py <scenario>")
        print("\nScenarios:")
        print("  1  - Normal processing")
        print("  2  - Retryable error (app-level retry)")
        print("  3  - Crash simulation (poison detection)")
        print("  4  - Hang simulation (poison detection)")
        print("  dlq - Inspect DLQ")
        return

    scenario = sys.argv[1]

    if scenario == "1":
        await scenario_normal_processing(app)
    elif scenario == "2":
        await scenario_retryable_error(app)
    elif scenario == "3":
        await scenario_crash_recovery(app)
    elif scenario == "4":
        await scenario_hang_recovery(app)
    elif scenario == "dlq":
        await inspect_dlq(app)
    else:
        print(f"❌ Unknown scenario: {scenario}")


if __name__ == "__main__":
    import sys

    if "--consumer" in sys.argv:
        asyncio.run(run_consumer())
    else:
        asyncio.run(run_publisher())
