# Evora Quick Reference

## 🚀 Quick Start

### Installation
```bash
pip install redis pydantic anyio
```

### Basic Usage
```python
from evora.app import App, subscribe
from evora.brokers.redis_streams import RedisStreamsBroker
from evora.idempotency import IdempotencyPolicy
from evora.idempotency_redis import RedisIdempotencyStore
from evora.core import Event
from pydantic import BaseModel
import redis.asyncio as redis

# Define event
class UserEvent(Event):
    __version__ = 1
    
    class Data(BaseModel):
        user_id: int
        action: str
    
    data: Data
    
    @classmethod
    def event_type(cls) -> str:
        return "users.events"

# Define handler
@subscribe(
    UserEvent,
    retry="exponential",
    max_attempts=5,
    dlq=True,
    idempotency=IdempotencyPolicy(mode="event_id", ttl_seconds=86400),
)
async def handle_user_event(event: UserEvent, ctx):
    print(f"Processing: {event.data.user_id} - {event.data.action}")
    # Your logic here

# Setup app
async def main():
    r = redis.Redis(host="localhost", port=6379, decode_responses=False)
    
    broker = RedisStreamsBroker(client=r, group_id="my-service")
    idempotency = RedisIdempotencyStore(client=r)
    
    app = App(
        broker=broker,
        source="my-service",
        idempotency_store=idempotency,
        strict=True,
    )
    
    app.add_handler(handle_user_event)
    
    # Run consumer
    await app.run()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

---

## 📨 Publishing Events

```python
# Simple publish
event = UserEvent(data=UserEvent.Data(user_id=1, action="login"))
await app.publish(event)

# With partition key
await app.publish(event, key=f"user-{event.data.user_id}")

# Custom channel
await app.publish(event, channel="users.important")

# With tracing
await app.publish(event, traceparent="00-trace-id-span-id-01")
```

---

## ⚠️ Error Handling

```python
from evora.errors import RetryableError, FatalError, ContractError

@subscribe(MyEvent, idempotency=IdempotencyPolicy(...))
async def handle_event(event: MyEvent, ctx):
    try:
        await process(event)
    
    except TimeoutError as e:
        # Transient failure → retry with exponential backoff
        raise RetryableError(f"Timeout: {e}") from e
    
    except ValueError as e:
        # Bad data → DLQ immediately, no retry
        raise ContractError(f"Invalid data: {e}") from e
    
    except PermissionError as e:
        # Business rule violation → DLQ, no retry
        raise FatalError(f"Access denied: {e}") from e
```

**Error Routing:**
- `RetryableError` → Retry up to `max_attempts` → DLQ if exhausted
- `FatalError` → DLQ immediately
- `ContractError` → DLQ immediately
- Unknown error → Retry (treated as retryable)

---

## 🔧 Configuration

### Broker Settings
```python
broker = RedisStreamsBroker(
    client=redis_client,
    group_id="my-service",
    
    # Consumption
    block_ms=2000,              # XREADGROUP block time
    batch_size=50,              # Messages per poll
    
    # Retry (app-level)
    base_delay_ms=500,          # Initial retry delay
    max_delay_ms=30_000,        # Max retry delay (exponential cap)
    
    # Poison detection (broker-level)
    poison_idle_ms=60_000,      # 60s idle → reclaim
    poison_max_deliveries=10,   # 10 deliveries → DLQ
    poison_check_interval_s=10.0,  # Check PEL every 10s
)
```

### App Settings
```python
app = App(
    broker=broker,
    source="my-service",         # Service name (consumer name)
    idempotency_store=store,
    strict=True,                 # Enforce idempotency + versions
    dlq_suffix=".dlq",          # DLQ channel naming
)
```

### Handler Settings
```python
@subscribe(
    MyEvent,
    channel="custom.channel",    # Override default channel
    retry="exponential",         # Or "constant", "custom"
    max_attempts=5,              # Retry limit
    dlq=True,                    # Enable DLQ routing
    idempotency=IdempotencyPolicy(
        mode="event_id",         # Dedup by event ID
        ttl_seconds=86400,       # 24h
    ),
)
```

---

## 🔍 Monitoring

### Redis Commands

```bash
# Stream info
redis-cli XLEN users.events
redis-cli XINFO GROUPS users.events

# Consumer group lag
redis-cli XINFO GROUPS users.events | grep last-delivered-id

# Pending entries (PEL)
redis-cli XPENDING users.events my-service
redis-cli XPENDING users.events my-service - + 100

# DLQ
redis-cli XLEN users.events.dlq
redis-cli XRANGE users.events.dlq - + COUNT 10

# Retry queue (ZSET)
redis-cli ZRANGE users.events.retry.z 0 -1 WITHSCORES
redis-cli ZCARD users.events.retry.z

# Idempotency keys
redis-cli KEYS "evora:idempotency:*"
redis-cli GET "evora:idempotency:my-handler:event-123"
```

### Observability Hooks

```python
from evora.observability.telemetry import Telemetry


class MyTelemetry(Telemetry):
   def on_consume_start(self, event_type, handler, event_id, attrs):
      # Log, create trace span, etc.
      logger.info("consume.start", extra=attrs)
      return (event_type, start_time)

   def on_consume_end(self, token, outcome, error):
      # Log, record metrics, close span
      event_type, start_time = token
      duration = time.time() - start_time

      metrics.histogram("evora.duration").observe(duration)
      metrics.counter(f"evora.outcome.{outcome}").inc()


app = App(..., telemetry=MyTelemetry())
```

---

## 🐛 Debugging

### Check Message Flow
```bash
# Watch Redis commands
redis-cli MONITOR | grep -E "XADD|XREADGROUP|XACK|ZADD"

# View stream messages
redis-cli XRANGE users.events - + COUNT 10

# View specific message
redis-cli XRANGE users.events 1234567890-0 1234567890-0
```

### Inspect PEL Issues
```bash
# Find stuck messages
redis-cli XPENDING users.events my-service - + 100 \
  | awk '{if ($4 > 60000) print $0}'  # Idle > 60s

# Manual reclaim
redis-cli XCLAIM users.events my-service my-consumer 60000 <msg_id>

# Force ACK (dangerous!)
redis-cli XACK users.events my-service <msg_id>
```

### DLQ Analysis
```bash
# DLQ reasons
redis-cli XRANGE users.events.dlq - + COUNT 100 \
  | grep -o "reason:[^,]*" | sort | uniq -c

# Recent poison messages
redis-cli XRANGE users.events.dlq - + COUNT 10 \
  | grep "reason:poison_message"
```

---

## 🎯 Common Patterns

### Fan-out (Multiple Handlers)
```python
@subscribe(UserEvent, idempotency=IdempotencyPolicy(...))
async def send_email(event: UserEvent, ctx):
    await email_service.send(event.data.user_id)

@subscribe(UserEvent, idempotency=IdempotencyPolicy(...))
async def update_analytics(event: UserEvent, ctx):
    await analytics.track(event.data.user_id)

# Both handlers receive same event, independent processing
```

### Enrichment
```python
@subscribe(UserEvent, idempotency=IdempotencyPolicy(...))
async def enrich_and_forward(event: UserEvent, ctx):
    # Fetch additional data
    user = await db.get_user(event.data.user_id)
    
    # Publish enriched event
    enriched = UserEnrichedEvent(
        data=UserEnrichedEvent.Data(
            user_id=event.data.user_id,
            name=user.name,
            email=user.email,
        )
    )
    await app.publish(enriched)
```

### Saga Orchestration
```python
@subscribe(OrderPlacedEvent, idempotency=IdempotencyPolicy(...))
async def start_order_saga(event: OrderPlacedEvent, ctx):
    # Step 1: Reserve inventory
    await inventory_service.reserve(event.data.order_id)
    await app.publish(InventoryReservedEvent(...))

@subscribe(InventoryReservedEvent, idempotency=IdempotencyPolicy(...))
async def charge_payment(event: InventoryReservedEvent, ctx):
    # Step 2: Charge payment
    await payment_service.charge(event.data.order_id)
    await app.publish(PaymentChargedEvent(...))

# Idempotency ensures each step runs exactly once
```

---

## 🚨 Troubleshooting

### Messages Not Processing
1. Check consumer is running: `ps aux | grep python`
2. Check consumer group: `redis-cli XINFO GROUPS <stream>`
3. Check PEL depth: `redis-cli XPENDING <stream> <group>`
4. Check handler errors in logs

### High DLQ Rate
1. Check DLQ messages: `redis-cli XRANGE <stream>.dlq - + COUNT 10`
2. Look for `reason` field in DLQ messages
3. Common causes:
   - `poison_message`: Crashes/hangs
   - `contract`: Invalid payload
   - Handler exceptions: Business logic errors

### PEL Growing
1. Check for slow handlers (add timeouts)
2. Check for crashed consumers (restart)
3. Check poison detection config (lower `poison_idle_ms`)
4. Manual reclaim if needed: `XCLAIM`

### Idempotency Not Working
1. Check key exists: `redis-cli GET "evora:idempotency:<scope>:<event_id>"`
2. Check TTL: `redis-cli TTL "evora:idempotency:<scope>:<event_id>"`
3. Verify `IdempotencyPolicy` is set
4. Verify `strict=True` in App

---

## 📚 Examples

- `examples/memory_ex.py` - Basic in-memory broker
- `examples/redis_streams_idemp.py` - Redis with idempotency
- `examples/redis_poison_demo.py` - Poison detection scenarios

Run:
```bash
# Start Redis
docker run -d -p 6379:6379 redis:7-alpine

# Run example
python examples/redis_poison_demo.py --consumer
```

---

## 📖 Documentation

- `docs/ARCHITECTURE.md` - System design
- `docs/POISON_MESSAGE_HANDLING.md` - Poison detection deep dive
- `docs/IMPLEMENTATION_STATUS.md` - Current features
- `examples/README.md` - Hands-on tutorials

---

## 🎓 Best Practices

1. **Always use strict mode** in production
2. **Always set idempotency policy** on handlers
3. **Classify errors correctly** (Retryable vs Fatal)
4. **Monitor DLQ depth** and set alerts
5. **Monitor PEL depth** for consumer health
6. **Use partition keys** for ordering guarantees
7. **Version your events** explicitly
8. **Test crash scenarios** before production
9. **Configure poison detection** for your workload
10. **Log structured data** for debugging

---

## ⚡️ Performance Tips

1. **Increase batch_size** for high throughput
2. **Run multiple consumers** for parallelism
3. **Use shorter TTLs** for idempotency keys
4. **Tune poison_idle_ms** to avoid false positives
5. **Pipeline Redis commands** where possible
6. **Use connection pooling** for Redis
7. **Monitor handler duration** and optimize slow paths

---

## 🔗 Quick Links

- GitHub: [Coming soon]
- Issues: [Coming soon]
- Docs: `docs/`
- Examples: `examples/`

---

**Version:** 0.2.0 (Reliability Core)  
**Status:** Production-Ready (Redis Backend)  
**Last Updated:** March 1, 2026
