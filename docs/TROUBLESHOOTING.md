# Troubleshooting Guide

This guide helps you diagnose and fix common issues with Evora.

## 🔧 Quick Diagnostics

### Check System Health

```bash
# 1. Redis is running
redis-cli ping
# Expected: PONG

# 2. Check Redis memory
redis-cli INFO memory | grep used_memory_human

# 3. List all streams
redis-cli KEYS "*"

# 4. Check stream length
redis-cli XLEN users.events

# 5. Check consumer groups
redis-cli XINFO GROUPS users.events

# 6. Check pending messages
redis-cli XPENDING users.events my-service
```

---

## 🚨 Common Issues

### Issue: Messages Not Being Consumed

**Symptoms**:
- Messages published to Redis
- Consumer is running
- No handler execution

**Diagnosis**:
```bash
# Check if messages exist
redis-cli XLEN users.events

# Check consumer group
redis-cli XINFO GROUPS users.events

# Check consumers in group
redis-cli XINFO CONSUMERS users.events my-service
```

**Common Causes**:

#### 1. Consumer group doesn't exist
```bash
# Check
redis-cli XINFO GROUPS users.events
# If empty, group wasn't created

# Fix: Restart consumer (it will auto-create)
python app.py
```

#### 2. Wrong channel name
```python
# Check your event's event_type()
class MyEvent(Event):
    @classmethod
    def event_type(cls) -> str:
        return "users.events"  # Must match publish channel

# Check handler subscription
@subscribe(MyEvent, channel="users.events", ...)  # Explicit channel
```

#### 3. Consumer crashed before setup
```bash
# Check logs for startup errors
tail -f app.log

# Common startup errors:
# - Missing __version__ in strict mode
# - Missing idempotency policy
# - Redis connection failed
```

---

### Issue: Messages Stuck in PEL

**Symptoms**:
- Messages visible in XPENDING
- Not being processed
- Handler not executing

**Diagnosis**:
```bash
# Check PEL (Pending Entries List)
redis-cli XPENDING users.events my-service - + 10

# Output shows:
# - Message ID
# - Consumer name
# - Idle time (ms)
# - Delivery count
```

**Common Causes**:

#### 1. Consumer crashed mid-processing
**Automatic Fix**: Poison detector will reclaim after `poison_idle_ms` (default: 60s)

**Manual Fix**:
```bash
# Reclaim stuck message
redis-cli XCLAIM users.events my-service new-consumer 60000 <message-id>
```

#### 2. Poison message (exceeded max deliveries)
**Automatic Fix**: Routes to DLQ after `poison_max_deliveries` (default: 10)

**Manual Fix**:
```bash
# Check DLQ
redis-cli XRANGE users.events.dlq - +

# Process or delete
redis-cli XDEL users.events.dlq <message-id>
```

#### 3. Handler hanging indefinitely
```python
# Add timeout to handler
import asyncio

@subscribe(MyEvent, idempotency=...)
async def my_handler(event, ctx):
    try:
        async with asyncio.timeout(30):  # 30 second timeout
            await slow_operation()
    except TimeoutError:
        raise RetryableError("Handler timeout")
```

---

### Issue: Duplicate Processing

**Symptoms**:
- Same event processed multiple times
- Duplicate database entries
- Duplicate API calls

**Diagnosis**:
```bash
# Check idempotency store
redis-cli KEYS "evora:idempotency:*"

# Check specific event
redis-cli GET "evora:idempotency:my_handler:<event-id>"
```

**Common Causes**:

#### 1. Missing idempotency policy
```python
# ❌ Wrong (no idempotency)
@subscribe(MyEvent)
async def handler(event, ctx): ...

# ✅ Correct
@subscribe(
    MyEvent,
    idempotency=IdempotencyPolicy(mode="event_id", ttl_seconds=86400)
)
async def handler(event, ctx): ...
```

#### 2. Idempotency TTL too short
```python
# If TTL expires before retries complete, duplicates can occur
IdempotencyPolicy(
    mode="event_id",
    ttl_seconds=86400  # 24 hours - should be > max retry time
)
```

#### 3. Different event IDs for same logical event
```python
# ❌ Wrong - generates new ID each time
event = MyEvent(data=...)
await app.publish(event)  # New ID
await app.publish(event)  # Different ID - will process twice!

# ✅ Correct - reuse event ID
event_id = str(uuid.uuid4())
event = MyEvent(data=...)
await app.publish(event, event_id=event_id)  # Explicit ID
```

---

### Issue: High Redis Memory Usage

**Symptoms**:
- Redis memory growing
- Performance degradation

**Diagnosis**:
```bash
# Check memory usage
redis-cli INFO memory

# Check key counts
redis-cli INFO keyspace

# Check stream lengths
redis-cli XLEN users.events
redis-cli XLEN users.events.dlq
redis-cli ZCARD users.events.retry.z

# Check idempotency keys
redis-cli KEYS "evora:idempotency:*" | wc -l
```

**Common Causes**:

#### 1. Unbounded stream growth
```bash
# Trim old messages (keep last 1M)
redis-cli XTRIM users.events MAXLEN ~ 1000000

# Automate in production
redis-cli CONFIG SET stream-node-max-bytes 4096
```

#### 2. DLQ buildup
```bash
# Check DLQ size
redis-cli XLEN users.events.dlq

# Archive and clear
redis-cli XRANGE users.events.dlq - + COUNT 1000 > dlq_backup.txt
redis-cli XTRIM users.events.dlq MAXLEN 0
```

#### 3. Long idempotency TTL
```python
# Reduce if acceptable
IdempotencyPolicy(
    mode="event_id",
    ttl_seconds=3600  # 1 hour instead of 24
)
```

#### 4. Many scheduled retries
```bash
# Check retry queue
redis-cli ZCARD users.events.retry.z

# If huge, might have systemic failure
# Fix root cause, then clear
redis-cli DEL users.events.retry.z
```

---

### Issue: Retries Not Working

**Symptoms**:
- Handler raises RetryableError
- Message not retried
- Goes to DLQ immediately

**Diagnosis**:
```bash
# Check retry queue
redis-cli ZRANGE users.events.retry.z 0 -1 WITHSCORES

# Check if retry scheduler is running
# (Should see periodic ZRANGEBYSCORE in Redis logs)
redis-cli MONITOR | grep ZRANGEBYSCORE
```

**Common Causes**:

#### 1. max_attempts = 1
```python
@subscribe(
    MyEvent,
    retry="exponential",
    max_attempts=1,  # ❌ Only tries once!
    idempotency=...
)
```

#### 2. Wrong error type
```python
# ❌ Wrong - FatalError skips retry
raise FatalError("Database timeout")  # Goes to DLQ

# ✅ Correct - RetryableError triggers retry
raise RetryableError("Database timeout")  # Will retry
```

#### 3. Unhandled exception
```python
# ❌ Unhandled exception crashes consumer
async def handler(event, ctx):
    result = await api.call()  # Might raise Exception
    
# ✅ Wrap and classify
async def handler(event, ctx):
    try:
        result = await api.call()
    except Exception as e:
        raise RetryableError(f"API failed: {e}") from e
```

---

### Issue: Schema Validation Errors

**Symptoms**:
- Events published successfully
- Consumer fails with validation errors
- "Field required" or "Type mismatch"

**Diagnosis**:
```python
# Check event structure
from evora.core import Registry
registry = Registry()
registry.register(MyEvent)

# Try decoding manually
envelope, event = registry.decode(raw_bytes)
```

**Common Causes**:

#### 1. Schema mismatch
```python
# Publisher uses v1
class MyEventV1(Event):
    __version__ = 1
    data: MyData

# Consumer expects v2
class MyEventV2(Event):
    __version__ = 2
    data: MyNewData  # Different structure!

# Fix: Support both versions
app.registry.register(MyEventV1)
app.registry.register(MyEventV2)

@subscribe(MyEventV1, ...)
async def handle_v1(event, ctx): ...

@subscribe(MyEventV2, ...)
async def handle_v2(event, ctx): ...
```

#### 2. Missing required field
```python
# Use schema check CLI
evora schema check old.py:MyEvent new.py:MyEvent

# Will show breaking changes
```

---

### Issue: Consumer Not Starting

**Symptoms**:
- Application crashes on startup
- Error message in logs

**Common Startup Errors**:

#### 1. "Missing __version__ in strict mode"
```python
# ❌ Wrong
class MyEvent(Event):
    data: MyData

# ✅ Correct
class MyEvent(Event):
    __version__ = 1
    data: MyData
```

#### 2. "Missing idempotency policy in strict mode"
```python
# ❌ Wrong
@subscribe(MyEvent)
async def handler(event, ctx): ...

# ✅ Correct
@subscribe(
    MyEvent,
    idempotency=IdempotencyPolicy(mode="event_id", ttl_seconds=86400)
)
async def handler(event, ctx): ...
```

#### 3. Redis connection failed
```python
# Check Redis is running
redis-cli ping

# Check connection string
r = redis.Redis(
    host="localhost",  # Correct host?
    port=6379,         # Correct port?
    decode_responses=False
)

# Test connection
await r.ping()
```

---

## 🔍 Debug Mode

Enable debug logging:

```python
import logging
logging.basicConfig(level=logging.DEBUG)

# Shows:
# - Message consumption
# - Handler execution
# - Retry scheduling
# - Idempotency checks
```

---

## 📊 Monitoring Commands

### Stream Health
```bash
# Message count
redis-cli XLEN users.events

# Stream info
redis-cli XINFO STREAM users.events

# Consumer groups
redis-cli XINFO GROUPS users.events

# Consumers in group
redis-cli XINFO CONSUMERS users.events my-service
```

### PEL Monitoring
```bash
# Pending count
redis-cli XPENDING users.events my-service

# Detailed pending
redis-cli XPENDING users.events my-service - + 10

# Stuck messages (idle > 60s)
redis-cli XPENDING users.events my-service - + 10 | awk '$4 > 60000'
```

### Retry Queue
```bash
# Count
redis-cli ZCARD users.events.retry.z

# View upcoming retries
redis-cli ZRANGE users.events.retry.z 0 10 WITHSCORES

# Overdue retries
redis-cli ZRANGEBYSCORE users.events.retry.z -inf $(date +%s)000
```

### DLQ Monitoring
```bash
# Count
redis-cli XLEN users.events.dlq

# Recent failures
redis-cli XREVRANGE users.events.dlq + - COUNT 10

# Failures by reason
redis-cli XRANGE users.events.dlq - + | grep -o '"reason":"[^"]*"' | sort | uniq -c
```

---

## 🆘 Emergency Procedures

### Clear Stuck Consumer Group
```bash
# If consumer group is corrupted
redis-cli XGROUP DESTROY users.events my-service
redis-cli XGROUP CREATE users.events my-service 0 MKSTREAM

# Restart consumers
```

### Drain DLQ
```bash
# Archive
redis-cli XRANGE users.events.dlq - + > dlq_$(date +%Y%m%d).txt

# Clear
redis-cli DEL users.events.dlq
```

### Reset Idempotency
```bash
# Clear all idempotency keys (DANGEROUS!)
redis-cli KEYS "evora:idempotency:*" | xargs redis-cli DEL

# Clear specific handler
redis-cli KEYS "evora:idempotency:my_handler:*" | xargs redis-cli DEL
```

### Force Retry All
```bash
# Clear retry queue and republish
redis-cli ZRANGE users.events.retry.z 0 -1 | while read msg; do
    redis-cli XADD users.events "*" v "$msg"
done
redis-cli DEL users.events.retry.z
```

---

## 📞 Getting Help

If you're still stuck:

1. **Check logs**: Application and Redis logs
2. **Search issues**: [GitHub Issues](https://github.com/your-org/evora/issues)
3. **Ask community**: GitHub Discussions
4. **Report bug**: Open new issue with reproduction steps

Include in bug reports:
- Evora version
- Python version
- Redis version
- Configuration (sanitized)
- Error logs
- Steps to reproduce

---

**Still having issues?** Open an issue with the "help wanted" label!
