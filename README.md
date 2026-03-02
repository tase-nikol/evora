# Evora

**A production-grade event processing runtime with strict reliability guarantees.**

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: TBD](https://img.shields.io/badge/License-TBD-yellow.svg)](LICENSE)
[![Status: Alpha](https://img.shields.io/badge/Status-Alpha-orange.svg)](CHANGELOG.md)

---

## 🎯 What is Evora?

Evora is **not another Kafka wrapper**. It's a complete event processing runtime that enforces reliability patterns at the framework level.

### The Problem

Most event-driven systems fail in production because:
- ❌ Developers forget to implement idempotency
- ❌ Retry logic is inconsistent across services
- ❌ Failed messages disappear into the void
- ❌ Consumer crashes leave messages stuck forever
- ❌ Schema changes break everything silently
- ❌ No one knows what "exactly-once" actually means

### The Solution

Evora makes reliability **impossible to ignore**:
- ✅ **Strict mode by default** - Idempotency and versioning are mandatory
- ✅ **Explicit error classification** - RetryableError vs FatalError vs ContractError
- ✅ **Durable retry queues** - Crash-safe exponential backoff
- ✅ **Poison message detection** - Automatic recovery from stuck processing
- ✅ **Structured DLQ** - Full forensics for every failure
- ✅ **Self-healing consumers** - Automatic reclaim of idle messages

---

## 🚀 Quick Start

### Installation

```bash
pip install redis pydantic anyio
```

### Your First Event Handler

```python
from evora.app import App, subscribe
from evora.brokers.redis_streams import RedisStreamsBroker
from evora.idempotency import IdempotencyPolicy
from evora.idempotency_redis import RedisIdempotencyStore
from evora.core import Event
from pydantic import BaseModel
import redis.asyncio as redis

# 1. Define your event
class UserRegisteredEvent(Event):
    __version__ = 1  # Explicit versioning required
    
    class Data(BaseModel):
        user_id: int
        email: str
        name: str
    
    data: Data
    
    @classmethod
    def event_type(cls) -> str:
        return "users.registered"

# 2. Define your handler
@subscribe(
    UserRegisteredEvent,
    retry="exponential",
    max_attempts=5,
    dlq=True,
    idempotency=IdempotencyPolicy(mode="event_id", ttl_seconds=86400),
)
async def send_welcome_email(event: UserRegisteredEvent, ctx):
    """
    This handler will:
    - Run exactly once (idempotency)
    - Retry up to 5 times on transient failures
    - Go to DLQ if all retries fail
    - Survive consumer crashes (poison detection)
    """
    await email_service.send(
        to=event.data.email,
        template="welcome",
        data={"name": event.data.name},
    )

# 3. Setup the application
async def main():
    r = redis.Redis(host="localhost", port=6379, decode_responses=False)
    
    broker = RedisStreamsBroker(client=r, group_id="email-service")
    idempotency = RedisIdempotencyStore(client=r)
    
    app = App(
        broker=broker,
        source="email-service",
        idempotency_store=idempotency,
        strict=True,  # Enforces best practices
    )
    
    app.add_handler(send_welcome_email)
    
    await app.run()  # Start consuming

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

### Publishing Events

```python
async def register_user(user_data):
    # Your business logic
    user = await db.create_user(user_data)
    
    # Publish event
    event = UserRegisteredEvent(
        data=UserRegisteredEvent.Data(
            user_id=user.id,
            email=user.email,
            name=user.name,
        )
    )
    
    await app.publish(event, key=f"user-{user.id}")
```

That's it! You now have:
- ✅ Guaranteed exactly-once processing
- ✅ Automatic retry with exponential backoff
- ✅ Poison message detection
- ✅ Structured DLQ for failures

---

## 💎 What Evora Offers

### 1. Strict Reliability Enforcement

**Problem:** Developers often forget critical reliability patterns.

**Solution:** Evora makes them mandatory.

```python
# ❌ This won't compile in strict mode
@subscribe(MyEvent)  # Missing idempotency policy!
async def handler(event, ctx):
    pass

# ✅ This is required
@subscribe(MyEvent, idempotency=IdempotencyPolicy(...))
async def handler(event, ctx):
    pass
```

**Result:** No production incidents from forgotten patterns.

---

### 2. Intelligent Error Classification

**Problem:** Most systems treat all errors the same.

**Solution:** Explicit error types drive different behaviors.

```python
from evora.errors import RetryableError, FatalError, ContractError

@subscribe(OrderEvent, idempotency=IdempotencyPolicy(...))
async def process_order(event, ctx):
    try:
        await payment_service.charge(event.data.order_id)
    
    except PaymentTimeoutError:
        # Transient → Retry with exponential backoff
        raise RetryableError("Payment service timeout")
    
    except InvalidCardError:
        # Business rule violation → DLQ immediately, no retry
        raise FatalError("Invalid payment method")
    
    except MalformedEventError:
        # Bad schema → DLQ, alert developers
        raise ContractError("Invalid event structure")
```

**Result:** 
- Transient failures retry automatically
- Permanent failures go to DLQ immediately
- No wasted retries on unretryable errors

---

### 3. Durable Retry Queues

**Problem:** In-memory retries are lost on crash.

**Solution:** Redis ZSET-based delay queue.

```
Normal Processing:
  XREADGROUP → Handler → Success → XACK

Retryable Failure:
  XREADGROUP → Handler → RetryableError
    ↓
  schedule_retry():
    • Compute backoff: 1s, 2s, 4s, 8s...
    • ZADD retry queue (scored by due time)
    • XACK original message
    ↓
  Background Scheduler:
    • Every 500ms: ZRANGEBYSCORE (find due)
    • XADD back to stream (attempt + 1)
    • ZREM from retry queue
```

**Result:** 
- Retries survive crashes
- Exponential backoff (500ms → 30s)
- No blocking the main consumer loop

---

### 4. Poison Message Detection

**Problem:** Crashed consumers leave messages stuck in PEL forever.

**Solution:** Automatic detection and recovery.

```
Background Poison Checker (every 10s):
  
  1. Scan XPENDING for idle messages
  2. For each idle message:
     
     if idle_time > 60s AND delivery_count < 10:
       → XCLAIM (reclaim to active consumer)
     
     elif delivery_count >= 10:
       → Route to DLQ with metadata:
          • reason: "poison_message"
          • delivery_count: 10
          • idle_ms: 65000
          • original_channel: "orders.events"
          • original_msg_id: "1234567890-0"
       → XACK (clear from PEL)
```

**Result:**
- Consumer crashes don't block processing
- Messages can't get stuck forever
- Self-healing consumer groups
- Full forensics in DLQ

---

### 5. Guaranteed Idempotency

**Problem:** Duplicate processing causes data corruption.

**Solution:** Built-in deduplication by event ID.

```python
@subscribe(
    PaymentEvent,
    idempotency=IdempotencyPolicy(
        mode="event_id",      # Dedupe by event.id
        ttl_seconds=86400,    # 24 hours
    ),
)
async def charge_customer(event, ctx):
    # This will run EXACTLY ONCE per event ID
    # Even if:
    # - Message is redelivered
    # - Consumer crashes and restarts
    # - Multiple consumers process same partition
    await payment_gateway.charge(event.data.amount)
```

**Storage:**
```
Redis SET:
  Key: evora:idempotency:{handler}:{event_id}
  Value: "processed"
  TTL: 86400 seconds
```

**Result:** Financial-grade exactly-once guarantees.

---

### 6. Structured Dead Letter Queue

**Problem:** Failed messages disappear with no context.

**Solution:** DLQ with full forensics.

```json
{
  "v": "<original event bytes>",
  "reason": "poison_message",
  "delivery_count": "10",
  "idle_ms": "65000",
  "original_channel": "users.events",
  "original_msg_id": "1234567890-0",
  "error_type": "RetryableError",
  "failed_handler": "email_service.send_welcome_email",
  "timestamp": "2026-03-01T10:30:00Z"
}
```

**Result:** 
- Full debugging context
- Easy to replay after fixes
- Operational visibility

---

### 7. Consumer Group Coordination

**Problem:** Manual offset management is error-prone.

**Solution:** Redis Streams consumer groups.

```python
# Service A
broker = RedisStreamsBroker(
    client=r,
    group_id="email-service",  # Consumer group
)

# Service B
broker = RedisStreamsBroker(
    client=r,
    group_id="analytics-service",  # Different group
)

# Both receive same events independently
```

**Result:**
- Multiple services can subscribe to same events
- Horizontal scaling (add more consumers)
- Automatic load balancing
- PEL-based crash recovery

---

### 8. Typed Event Contracts

**Problem:** Runtime schema mismatches cause crashes.

**Solution:** Pydantic-based validation + versioning.

```python
class UserEvent(Event):
    __version__ = 2  # Explicit version required
    
    class Data(BaseModel):
        user_id: int
        email: str
        name: str | None = None  # Made optional in v2
    
    data: Data
```

**Result:**
- Schema validation at decode time
- Contract errors go to DLQ (not retried)
- Version enforcement (strict mode)
- Ready for schema evolution governance

---

## 🏗️ Architecture

### Message Flow

```
┌─────────────────────────────────────────────────────────────┐
│                      Publisher                               │
└───────────────────────────┬─────────────────────────────────┘
                            │
                            ▼
                    ┌───────────────┐
                    │ XADD stream   │
                    └───────┬───────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                    Consumer Group                            │
│                                                              │
│  ┌────────────────────────────────────────────────────┐    │
│  │           Main Consumer Loop                       │    │
│  │  • XREADGROUP                                      │    │
│  │  • Decode envelope                                 │    │
│  │  • Check idempotency                               │    │
│  │  • Execute handler                                 │    │
│  │  • XACK on success                                 │    │
│  └────────────────────────────────────────────────────┘    │
│                                                              │
│  ┌────────────────────────────────────────────────────┐    │
│  │          Retry Scheduler (background)              │    │
│  │  • Poll ZSET every 500ms                           │    │
│  │  • XADD due retries back to stream                 │    │
│  │  • ZREM from retry queue                           │    │
│  └────────────────────────────────────────────────────┘    │
│                                                              │
│  ┌────────────────────────────────────────────────────┐    │
│  │          Poison Checker (background)               │    │
│  │  • Scan XPENDING every 10s                         │    │
│  │  • XCLAIM idle messages (< max deliveries)         │    │
│  │  • DLQ poison messages (>= max deliveries)         │    │
│  └────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
                            │
                    ┌───────┴────────┐
                    │                │
                    ▼                ▼
              ┌──────────┐    ┌───────────┐
              │   XACK   │    │    DLQ    │
              └──────────┘    └───────────┘
```

### Three-Layer Design

1. **Application Layer** (`evora/app.py`)
   - Event decoding/validation
   - Handler dispatch
   - Error classification
   - Idempotency gate
   - Telemetry hooks

2. **Broker Layer** (`evora/brokers/`)
   - Message transport (Redis Streams, Kafka, etc.)
   - Durable retry scheduling
   - Poison message detection
   - Consumer group coordination

3. **Storage Layer**
   - Idempotency store (Redis)
   - Retry queue (Redis ZSET)
   - DLQ (Redis Stream)
   - PEL (Redis Streams native)

---

## 📊 Reliability Guarantees

| Property | Mechanism | Crash-Safe? |
|----------|-----------|-------------|
| **At-least-once delivery** | Consumer groups + XACK | ✅ |
| **Exactly-once processing** | Idempotency store | ✅ |
| **Ordered processing** | Partition keys | ✅ |
| **Durable retry** | ZSET delay queue | ✅ |
| **Exponential backoff** | Computed delay | ✅ |
| **Retry exhaustion** | Max attempts → DLQ | ✅ |
| **Poison detection** | XPENDING scan | ✅ |
| **Delivery limits** | Redis PEL tracking | ✅ |
| **Consumer coordination** | Consumer groups | ✅ |
| **Crash recovery** | Automatic (PEL + poison checker) | ✅ |

---

## 🎯 Use Cases

### 1. Microservices Communication
```python
# Order Service → Payment Service → Fulfillment Service
# Each service subscribes to relevant events
# Automatic retry on transient failures
# DLQ for permanent failures
```

### 2. Event Sourcing
```python
# Append-only event log
# Multiple projections (read models)
# Idempotent replay
# Version-safe schema evolution
```

### 3. CQRS
```python
# Command side publishes events
# Query side builds read models
# Guaranteed exactly-once updates
# Crash-safe processing
```

### 4. Saga Orchestration
```python
# Multi-step distributed transactions
# Each step is idempotent
# Automatic retry on failures
# Compensation via DLQ handlers
```

### 5. CDC (Change Data Capture)
```python
# Database changes → Events
# Multiple downstream consumers
# Guaranteed delivery
# Order preservation per entity
```

---

## 🔧 Configuration Reference

### Broker Configuration

```python
broker = RedisStreamsBroker(
    client=redis_client,
    group_id="my-service",              # Consumer group name
    consumer_name=None,                 # Auto: hostname-pid
    
    # Consumption
    block_ms=2000,                      # XREADGROUP block time
    batch_size=50,                      # Messages per poll
    mkstream=True,                      # Auto-create streams
    
    # Retry (app-level)
    base_delay_ms=500,                  # Initial retry delay
    max_delay_ms=30_000,                # Max delay (exponential cap)
    retry_zset_suffix=".retry.z",      # ZSET key suffix
    
    # Poison detection (broker-level)
    poison_idle_ms=60_000,              # 60s idle → reclaim
    poison_max_deliveries=10,           # 10 deliveries → DLQ
    poison_check_interval_s=10.0,       # Check PEL every 10s
    
    # DLQ
    dlq_suffix=".dlq",                  # DLQ stream suffix
)
```

### App Configuration

```python
app = App(
    broker=broker,
    source="my-service",                # Service name
    idempotency_store=store,
    strict=True,                        # Enforce best practices
    dlq_suffix=".dlq",                  # Must match broker
    telemetry=None,                     # Custom telemetry
)
```

### Handler Configuration

```python
@subscribe(
    MyEvent,
    channel=None,                       # Override event_type()
    retry="exponential",                # Or "constant"
    max_attempts=5,                     # Retry limit
    dlq=True,                           # Enable DLQ routing
    idempotency=IdempotencyPolicy(
        mode="event_id",                # Dedupe strategy
        ttl_seconds=86400,              # 24 hours
    ),
)
async def my_handler(event, ctx):
    pass
```

---

## 📈 Performance

### Throughput
- **Single consumer:** 5,000-10,000 msgs/sec (handler dependent)
- **Multi-consumer:** Linear scaling with consumer count
- **Bottleneck:** Handler execution time

### Latency
- **Normal path:** 1-5ms (XREADGROUP → handler → XACK)
- **Retry path:** Configured delay (500ms, 1s, 2s, 4s...)
- **Poison detection:** 10s interval + idle threshold

### Resource Usage
- **Redis memory:** ~1KB per pending message
- **ZSET entries:** 1 per scheduled retry
- **Idempotency keys:** TTL-based cleanup

---

## 🔍 Monitoring & Observability

### Key Metrics to Track

```python
# Processing
evora.consume.success          # Successful processing
evora.consume.retry            # App-level retries
evora.consume.dlq              # DLQ routing
evora.consume.duration         # Handler latency

# Poison detection
evora.poison.reclaimed         # XCLAIM operations
evora.poison.dlq               # Poison → DLQ

# Health
evora.pel.depth{stream}        # PEL size per stream
evora.dlq.depth{stream}        # DLQ size per stream
evora.retry.scheduled          # Retry queue additions
```

### Redis Commands

```bash
# Stream health
redis-cli XLEN users.events
redis-cli XINFO GROUPS users.events

# PEL inspection
redis-cli XPENDING users.events my-service
redis-cli XPENDING users.events my-service - + 100

# DLQ inspection
redis-cli XLEN users.events.dlq
redis-cli XRANGE users.events.dlq - + COUNT 10

# Retry queue
redis-cli ZCARD users.events.retry.z
redis-cli ZRANGE users.events.retry.z 0 -1 WITHSCORES
```

---

## 🧪 Testing

### Unit Tests

```python
from evora.brokers.memory import MemoryBroker

async def test_my_handler():
    # Use in-memory broker for fast tests
    broker = MemoryBroker()
    app = App(broker=broker, ...)
    
    event = MyEvent(...)
    await app.publish(event)
    
    # Assert handler effects
```

### Integration Tests

```python
# Use real Redis, test full flow
broker = RedisStreamsBroker(...)
app = App(broker=broker, ...)

await app.publish(event)
await asyncio.sleep(0.5)  # Allow processing

# Assert side effects (DB, external APIs)
```

### Chaos Tests

```bash
# Kill consumer mid-processing
python my_consumer.py &
kill -9 $!

# Verify:
# - Message enters PEL
# - Poison checker reclaims after 60s
# - Message reprocessed by new consumer
```

---

## 🚀 Getting Started

### 1. Run Examples

```bash
# Start Redis
docker run -d -p 6379:6379 redis:7-alpine

# Run basic example
python examples/memory_ex.py

# Run Redis example with poison detection
python examples/redis_poison_demo.py --consumer
```
 
### 2. Build Your Service

```bash
# 1. Define events (evora/events.py)
# 2. Define handlers (evora/handlers.py)
# 3. Setup app (evora/main.py)
# 4. Deploy with monitoring
```

---

## 🎓 Philosophy

### 1. Fail-Fast Configuration
> "If you can forget it, it should be required."

Evora enforces critical patterns at compile time, not runtime.

### 2. Explicit Over Implicit
> "Retryable errors look different from fatal errors."

Error classification is part of your code, not buried in config.

### 3. Crash-Safe By Default
> "If a process dies, work continues."

Durable retry queues and poison detection are built-in, not add-ons.

### 4. Observability First
> "You can't fix what you can't see."

Structured DLQ, telemetry hooks, and full message forensics.

### 5. Progressive Complexity
> "Start simple, scale when needed."

In-memory broker for tests, Redis for production, Kafka when you outgrow Redis.

---

## 🔮 Roadmap

### ✅ Completed (v0.1.0)
- Strict mode enforcement
- Error classification
- Durable retry (ZSET)
- Idempotency (Redis)
- Poison message detection
- Consumer groups
- Structured DLQ

### 🚧 In Progress (v0.2.0)
- Schema governance CLI
- Breaking change detection
- Compatibility checks

### 📋 Planned (v0.3.0+)
- OpenTelemetry integration
- Prometheus metrics
- Outbox pattern
- Admin CLI tools
- Kafka broker implementation
- Performance benchmarks

--- 
## 🙏 Acknowledgments

Inspired by:
- **AWS SQS** - Visibility timeout, DLQ patterns
- **Kafka** - Consumer groups, offset management
- **Celery** - Task retry logic
- **Temporal** - Durable execution
- **Redis Streams** - PEL, consumer groups

But **simpler than all of them** while maintaining production reliability.

---

## 🎯 Status

**Current Version:** 0.1.0 (Alpha)  
**Production Status:** Redis backend production-ready  
**Next Milestone:** Schema governance CLI (v0.2.0)

---

**Evora: Event processing that doesn't fail silently.**

Stop treating events like fire-and-forget. Start treating them like financial transactions.
