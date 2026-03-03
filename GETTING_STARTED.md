# Getting Started with Evora

This guide will help you get up and running with Evora in under 10 minutes.

## Prerequisites

- Python 3.11 or higher
- Redis 6.0+ (can use Docker)
- Basic understanding of async Python

## Installation

### 1. Install Evora

```bash
pip install redis pydantic anyio
```

> **Note**: Evora is not yet published to PyPI. Clone the repository and install locally:
> ```bash
> git clone https://github.com/tase-nikol/evora.git
> cd evora
> pip install -e .
> ```

### 2. Start Redis

Using Docker:
```bash
docker run -d -p 6379:6379 --name evora-redis redis:latest
```

Or use a local Redis installation:
```bash
redis-server
```

## Your First Evora Application

### Step 1: Define an Event

Create `events.py`:

```python
from evora.core import Event
from pydantic import BaseModel

class OrderCreatedEvent(Event):
    __version__ = 1  # Required: explicit version
    
    class Data(BaseModel):
        order_id: str
        customer_id: str
        amount: float
        items: list[str]
    
    data: Data
    
    @classmethod
    def event_type(cls) -> str:
        return "orders.created"
```

**Key Points**:
- Inherit from `Event`
- Set explicit `__version__` (required in strict mode)
- Use Pydantic models for type safety
- Define `event_type()` for routing

### Step 2: Create a Handler

Create `handlers.py`:

```python
from evora.app import subscribe
from evora.idempotency import IdempotencyPolicy
from events import OrderCreatedEvent

@subscribe(
    OrderCreatedEvent,
    retry="exponential",
    max_attempts=5,
    dlq=True,
    idempotency=IdempotencyPolicy(
        mode="event_id",
        ttl_seconds=86400  # 24 hours
    ),
)
async def process_order(event: OrderCreatedEvent, ctx):
    """Process new orders."""
    print(f"Processing order: {event.data.order_id}")
    print(f"Customer: {event.data.customer_id}")
    print(f"Amount: ${event.data.amount}")
    
    # Your business logic here
    # This will run exactly once due to idempotency
    # It will retry automatically on transient failures
    # It will go to DLQ after max_attempts on persistent failures
```

**Key Points**:
- Use `@subscribe` decorator
- **Must** specify idempotency policy in strict mode
- Configure retry behavior
- Enable DLQ for failed messages

### Step 3: Setup the Application

Create `app.py`:

```python
import asyncio
import redis.asyncio as redis
from evora.app import App
from evora.brokers.redis_streams import RedisStreamsBroker
from evora.idempotency_redis import RedisIdempotencyStore
from handlers import process_order

async def main():
    # 1. Connect to Redis
    r = redis.Redis(
        host="localhost",
        port=6379,
        decode_responses=False
    )
    
    # 2. Create broker
    broker = RedisStreamsBroker(
        client=r,
        group_id="order-service",
        consumer_name=None,  # Auto-generated
    )
    
    # 3. Create idempotency store
    idempotency = RedisIdempotencyStore(client=r)
    
    # 4. Create app
    app = App(
        broker=broker,
        source="order-service",
        idempotency_store=idempotency,
        strict=True,  # Enforce best practices
    )
    
    # 5. Register handlers
    app.add_handler(process_order)
    
    # 6. Run consumer
    print("🚀 Starting Evora consumer...")
    await app.run()

if __name__ == "__main__":
    asyncio.run(main())
```

### Step 4: Publish Events

Create `publisher.py`:

```python
import asyncio
import redis.asyncio as redis
from evora.app import App
from evora.brokers.redis_streams import RedisStreamsBroker
from evora.idempotency_redis import RedisIdempotencyStore
from events import OrderCreatedEvent

async def publish_order():
    # Setup (same as consumer)
    r = redis.Redis(host="localhost", port=6379, decode_responses=False)
    broker = RedisStreamsBroker(client=r, group_id="order-service")
    idempotency = RedisIdempotencyStore(client=r)
    
    app = App(
        broker=broker,
        source="order-service",
        idempotency_store=idempotency,
        strict=True,
    )
    
    # Create and publish event
    event = OrderCreatedEvent(
        data=OrderCreatedEvent.Data(
            order_id="ORD-001",
            customer_id="CUST-123",
            amount=99.99,
            items=["Widget A", "Widget B"]
        )
    )
    
    await app.publish(event, key=f"order-{event.data.order_id}")
    print(f"✅ Published: {event.data.order_id}")
    
    await r.close()

if __name__ == "__main__":
    asyncio.run(publish_order())
```

### Step 5: Run It!

Terminal 1 - Start the consumer:
```bash
python app.py
```

Terminal 2 - Publish an event:
```bash
python publisher.py
```

You should see:
```
🚀 Starting Evora consumer...
Processing order: ORD-001
Customer: CUST-123
Amount: $99.99
```

## Error Handling

### Handling Transient Errors

```python
from evora.errors import RetryableError

@subscribe(OrderCreatedEvent, idempotency=IdempotencyPolicy(...))
async def process_order(event, ctx):
    try:
        await external_api.create_order(event.data)
    except ConnectionError as e:
        # This will retry with exponential backoff
        raise RetryableError(f"API connection failed: {e}") from e
```

### Handling Fatal Errors

```python
from evora.errors import FatalError

@subscribe(OrderCreatedEvent, idempotency=IdempotencyPolicy(...))
async def process_order(event, ctx):
    if event.data.amount <= 0:
        # This goes to DLQ immediately (no retry)
        raise FatalError("Invalid order amount")
```

## Next Steps

### 1. Explore Examples

Check out the `examples/` directory:
- `examples/redis_streams_idemp.py` - Full idempotency demo
- `examples/redis_poison_demo.py` - Poison message handling
- `examples/memory_ex.py` - In-memory broker for testing

### 2. Read the Docs

- [ARCHITECTURE.md](docs/ARCHITECTURE.md) - System architecture
- [QUICK_REFERENCE.md](docs/QUICK_REFERENCE.md) - API quick reference
- [REDIS_STREAMS_QUICKSTART.md](docs/REDIS_STREAMS_QUICKSTART.md) - Redis Streams deep dive
- [POISON_MESSAGE_HANDLING.md](docs/POISON_MESSAGE_HANDLING.md) - Poison detection

### 3. Production Configuration

For production deployments, see:
- [IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md) - Production readiness guide
- [EXECUTIVE_SUMMARY.md](docs/EXECUTIVE_SUMMARY.md) - Business value and use cases

### 4. Schema Governance

Use the CLI to manage schemas:

```bash
# Check compatibility between schemas
evora schema check old_events.py:OrderEvent new_events.py:OrderEvent

# Export schema to JSON
evora schema export events.py:OrderEvent --out order_schema.json
```

## Common Issues

### "Missing idempotency policy in strict mode"

**Problem**: Handler doesn't have idempotency configured.

**Solution**: Add `idempotency` parameter to `@subscribe`:
```python
@subscribe(
    MyEvent,
    idempotency=IdempotencyPolicy(mode="event_id", ttl_seconds=86400)
)
```

### "Event must declare __version__ in strict mode"

**Problem**: Event class doesn't have explicit version.

**Solution**: Add `__version__` to your event:
```python
class MyEvent(Event):
    __version__ = 1
```

### Redis Connection Errors

**Problem**: Can't connect to Redis.

**Solution**: Ensure Redis is running:
```bash
docker ps  # Check if redis container is running
redis-cli ping  # Should return "PONG"
```

## Testing Your Application

Use the in-memory broker for unit tests:

```python
from evora.brokers.memory import MemoryBroker

async def test_handler():
    broker = MemoryBroker()
    # ... setup app with memory broker
    
    # Publish and consume synchronously
    await app.publish(event)
    # Handler will be called in-memory
```

**You're now ready to build reliable event-driven systems with Evora!** 🎉
