# Redis Streams Broker - Quick Start Guide

## 5-Minute Setup

### 1. Install Dependencies

```bash
pip install redis anyio
```

### 2. Start Redis

```bash
# Docker
docker run -p 6379:6379 redis:latest

# Or local install
redis-server
```

### 3. Create a Simple App

```python
import asyncio
import json
import redis.asyncio as redis
from dataclasses import dataclass
from evora.brokers.redis_streams import RedisStreamsBroker
from evora.brokers.base import Message

# Event schema
@dataclass
class UserCreated:
    user_id: int
    email: str
    name: str

async def main():
    # 1. Setup
    redis_client = await redis.from_url("redis://localhost:6379")
    broker = RedisStreamsBroker(
        client=redis_client,
        group_id="user-service",
        consumer_name="worker-1"
    )
    
    # 2. Handler
    async def handle_message(msg: Message):
        data = json.loads(msg.value)
        print(f"📨 Received: {data}")
        print(f"   Attempt: {msg.attempt}")
        print(f"   Headers: {msg.headers}")
    
    # 3. Publish some events
    for i in range(5):
        event = UserCreated(
            user_id=i,
            email=f"user{i}@example.com",
            name=f"User {i}"
        )
        
        await broker.publish(
            "users.events",
            value=json.dumps(event.__dict__).encode(),
            headers={"version": "1.0", "source": "api"}
        )
        print(f"✅ Published: user_id={i}")
    
    # 4. Consume events
    print("\n🔄 Starting consumer...\n")
    await broker.run_consumer(
        channels=["users.events"],
        handler=handle_message,
        consumer_name="worker-1"
    )

if __name__ == "__main__":
    asyncio.run(main())
```

### 4. Run It

```bash
python app.py
```

**Output**:
```
✅ Published: user_id=0
✅ Published: user_id=1
✅ Published: user_id=2
✅ Published: user_id=3
✅ Published: user_id=4

🔄 Starting consumer...

📨 Received: {'user_id': 0, 'email': 'user0@example.com', 'name': 'User 0'}
   Attempt: 1
   Headers: {'version': '1.0', 'source': 'api'}
📨 Received: {'user_id': 1, 'email': 'user1@example.com', 'name': 'User 1'}
   Attempt: 1
   Headers: {'version': '1.0', 'source': 'api'}
...
```

---

## Error Handling Demo

```python
from evora.errors import RetryableError, FatalError

async def handle_with_retries(msg: Message):
    data = json.loads(msg.value)
    user_id = data["user_id"]
    
    # Simulate different error scenarios
    if user_id == 1:
        # Transient error - will retry with exponential backoff
        print(f"⚠️  Transient error for user {user_id}")
        raise RetryableError("Database connection timeout")
    
    elif user_id == 2:
        # Permanent error - goes to DLQ immediately
        print(f"❌ Fatal error for user {user_id}")
        raise FatalError("Invalid email format")
    
    else:
        # Success
        print(f"✅ Processed user {user_id}")

# Run consumer with this handler
await broker.run_consumer(
    channels=["users.events"],
    handler=handle_with_retries,
    consumer_name="worker-1"
)
```

**What Happens**:

- **user_id=0**: ✅ Processed immediately
- **user_id=1**: ⚠️ Retries at: 500ms → 1s → 2s → 4s → 8s...
- **user_id=2**: ❌ Goes to DLQ immediately (no retry)
- **user_id=3+**: ✅ Processed normally

---

## Multiple Consumers (Parallel Processing)

```python
# Terminal 1
python app.py --consumer-name worker-1

# Terminal 2
python app.py --consumer-name worker-2

# Terminal 3
python app.py --consumer-name worker-3
```

**Result**: Redis automatically distributes messages across workers. Each message processed by exactly one worker.

---

## Inspect Redis Data

### View Stream

```bash
# Total messages in stream
redis-cli XLEN users.events

# Read messages
redis-cli XREAD COUNT 10 STREAMS users.events 0
```

### View Consumer Group

```bash
# Group info
redis-cli XINFO GROUPS users.events

# Consumer info
redis-cli XINFO CONSUMERS users.events user-service

# Pending messages (PEL)
redis-cli XPENDING users.events user-service
```

### View Retry Queue

```bash
# Number of messages waiting for retry
redis-cli ZCARD users.events.retry.z

# Inspect retry queue
redis-cli ZRANGE users.events.retry.z 0 -1 WITHSCORES
```

### View Dead Letter Queue

```bash
# Number of failed messages
redis-cli XLEN users.events.dlq

# Read failed messages
redis-cli XREAD COUNT 10 STREAMS users.events.dlq 0
```

---

## Configuration Cheat Sheet

```python
broker = RedisStreamsBroker(
    client=redis_client,
    group_id="my-service",          # Consumer group name
    consumer_name="worker-1",       # Unique worker ID
    
    # Reading behavior
    block_ms=2000,                  # Block up to 2s waiting for messages
    batch_size=50,                  # Read up to 50 messages at once
    
    # Retry configuration
    base_delay_ms=500,              # Start with 500ms delay
    max_delay_ms=30_000,            # Cap at 30s max
    
    # Poison message protection
    poison_idle_ms=60_000,          # Reclaim after 60s idle
    poison_max_deliveries=10,       # DLQ after 10 attempts
    
    # Stream management
    mkstream=True,                  # Auto-create streams
    dlq_suffix=".dlq",              # DLQ stream suffix
    retry_zset_suffix=".retry.z",   # Retry queue suffix
)
```

---

## Common Patterns

### Pattern 1: Retry with Backoff

```python
async def handler(msg: Message):
    try:
        await external_api.call()
    except TimeoutError:
        # Automatic exponential backoff
        raise RetryableError("API timeout")
```

### Pattern 2: Circuit Breaker

```python
from datetime import datetime, timedelta

circuit_open_until = None

async def handler(msg: Message):
    global circuit_open_until
    
    # Check circuit breaker
    if circuit_open_until and datetime.now() < circuit_open_until:
        raise RetryableError("Circuit breaker open")
    
    try:
        await external_api.call()
        circuit_open_until = None  # Reset on success
    except Exception:
        # Open circuit for 30s
        circuit_open_until = datetime.now() + timedelta(seconds=30)
        raise RetryableError("Service failure, circuit opened")
```

### Pattern 3: Conditional Retry

```python
async def handler(msg: Message):
    data = json.loads(msg.value)
    
    try:
        await process(data)
    except ValidationError:
        # Don't retry validation errors
        raise FatalError("Invalid data format")
    except DatabaseError as e:
        if "connection" in str(e).lower():
            # Retry connection errors
            raise RetryableError("DB connection lost")
        else:
            # Don't retry constraint violations
            raise FatalError(f"Database error: {e}")
```

### Pattern 4: Idempotent Processing

```python
processed_ids = set()  # In production: use Redis SET

async def handler(msg: Message):
    data = json.loads(msg.value)
    message_id = msg.message_id
    
    # Check if already processed
    if message_id in processed_ids:
        print(f"⏭️  Skipping duplicate: {message_id}")
        return
    
    # Process
    await do_work(data)
    
    # Mark as processed
    processed_ids.add(message_id)
    # In production: await redis_client.sadd("processed", message_id)
```

### Pattern 5: Manual DLQ Replay

```python
async def replay_from_dlq(broker, channel, max_messages=100):
    """Manually replay messages from DLQ back to main stream."""
    dlq_stream = f"{channel}.dlq"
    
    # Read from DLQ
    messages = await broker.client.xread(
        {dlq_stream: "0"},
        count=max_messages
    )
    
    if not messages:
        print("No messages in DLQ")
        return
    
    for stream_name, entries in messages:
        for msg_id, fields in entries:
            # Re-publish to main stream
            await broker.publish(
                channel,
                value=fields[b"v"],
                headers=json.loads(fields.get(b"h", b"{}"))
            )
            
            # Remove from DLQ
            await broker.client.xdel(dlq_stream, msg_id)
            print(f"✅ Replayed: {msg_id}")

# Usage
await replay_from_dlq(broker, "users.events")
```

---

## Troubleshooting

### Problem: Messages not being consumed

**Check**:
```bash
# Is the stream created?
redis-cli EXISTS users.events

# Is the consumer group created?
redis-cli XINFO GROUPS users.events

# Are there messages in the stream?
redis-cli XLEN users.events
```

**Fix**:
```python
# Ensure mkstream=True
broker = RedisStreamsBroker(..., mkstream=True)
```

### Problem: Messages stuck in PEL

**Check**:
```bash
# View pending messages
redis-cli XPENDING users.events user-service

# Details on stuck messages
redis-cli XPENDING users.events user-service - + 10
```

**Fix**: Lower `poison_idle_ms` to reclaim faster:
```python
broker = RedisStreamsBroker(..., poison_idle_ms=30_000)  # 30s instead of 60s
```

### Problem: Retry queue growing

**Check**:
```bash
redis-cli ZCARD users.events.retry.z
```

**Possible Causes**:
1. Handler always failing → Check error logs
2. Consumer not running → Start consumer
3. Redis connection issues → Check connectivity

**Monitor**:
```python
retry_depth = await redis_client.zcard("users.events.retry.z")
if retry_depth > 1000:
    logger.warning(f"High retry queue depth: {retry_depth}")
```

### Problem: DLQ growing

**Check**:
```bash
redis-cli XLEN users.events.dlq
redis-cli XREAD COUNT 5 STREAMS users.events.dlq 0
```

**Action**: Inspect DLQ messages and fix the root cause:
```python
# Read DLQ
msgs = await redis_client.xread({"users.events.dlq": "0"}, count=10)
for stream, entries in msgs:
    for msg_id, fields in entries:
        reason = fields[b"reason"].decode()
        error = fields.get(b"error", b"").decode()
        print(f"Failure: {reason} - {error}")
```

---

## Next Steps

- **Full Documentation**: See [REDIS_STREAMS_BROKER.md](./REDIS_STREAMS_BROKER.md)
- **Production Setup**: Configure Redis persistence, monitoring, alerting
- **Integration**: Connect with your Evora App, add idempotency layer
- **Scale**: Add more consumers, use Redis Cluster for high throughput

---

**You now have a production-grade event processing system! 🎉**
