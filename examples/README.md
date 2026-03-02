# Evora Examples

This directory contains practical examples demonstrating Evora's reliability features.

---

## Examples Overview

### 1. `memory_ex.py`
Basic in-memory broker example showing event publishing and consumption.

**Run:**
```bash
python examples/memory_ex.py
```

### 2. `redis_streams_idemp.py`
Redis Streams with idempotency demonstration.

**Requirements:**
```bash
# Start Redis
docker run -d -p 6379:6379 redis:7-alpine

# Run example
python examples/redis_streams_idemp.py
```

### 3. `redis_poison_demo.py` ⭐️ NEW
**Comprehensive poison message detection & handling demo.**

---

## Poison Message Demo

### Prerequisites

1. **Start Redis:**
```bash
docker run -d -p 6379:6379 redis:7-alpine
```

2. **Install dependencies:**
```bash
pip install redis pydantic anyio
```

### Demo Scenarios

#### Scenario 1: Normal Processing ✅
```bash
# Terminal 1: Start consumer
python examples/redis_poison_demo.py --consumer

# Terminal 2: Send events
python examples/redis_poison_demo.py 1
```

**Expected:** Messages processed successfully, ACKed immediately.

---

#### Scenario 2: Retryable Error (App-Level Retry) 🔄
```bash
# Terminal 1: Consumer running
python examples/redis_poison_demo.py --consumer

# Terminal 2: Send retryable event
python examples/redis_poison_demo.py 2
```

**Expected:**
- Handler fails 2 times → `schedule_retry()` to ZSET
- Retry scheduler republishes after delay
- Handler succeeds on attempt 3 → ACK
- **No poison detection** (properly handled by app layer)

**Watch for:**
```
🔄 Simulating retryable failure (attempt 1/2)
🔄 Simulating retryable failure (attempt 2/2)
✅ Successfully processed user_id=3, action=purchase
```

---

#### Scenario 3: Crash + Poison Detection 💥
```bash
# Terminal 1: Start consumer
python examples/redis_poison_demo.py --consumer

# Terminal 2: Send crash-triggering event
python examples/redis_poison_demo.py 3
```

**Expected:**
1. Consumer **crashes** (exits) when processing user_id=99
2. Message enters PEL (unacknowledged)
3. Restart consumer: `python examples/redis_poison_demo.py --consumer`
4. After ~10 seconds: poison checker detects idle message
5. `XCLAIM` reclaims message
6. Message reprocessed (crashes again)
7. After 5 deliveries → **DLQ routing**

**Watch for:**
```
💥 CRASH SIMULATION for user_id=99
[poison-checker] Reclaiming idle message: 1234567890-0
💥 CRASH SIMULATION for user_id=99 (delivery 2)
...
[poison-checker] Max deliveries reached, routing to DLQ
```

**Verify DLQ:**
```bash
python examples/redis_poison_demo.py dlq
```

---

#### Scenario 4: Hang + Poison Detection ⏳
```bash
# Terminal 1: Start consumer
python examples/redis_poison_demo.py --consumer

# Terminal 2: Send hang-triggering event
python examples/redis_poison_demo.py 4
```

**Expected:**
1. Handler **hangs** (never returns) for user_id=88
2. Message stays in PEL (no ACK, no error)
3. After ~10 seconds: poison checker detects idle message
4. `XCLAIM` moves to another consumer (or same consumer)
5. Message reprocessed (hangs again)
6. After 5 deliveries → **DLQ routing**

**Watch for:**
```
⏳ HANG SIMULATION for user_id=88
[poison-checker] Reclaiming idle message after 10000ms
⏳ HANG SIMULATION for user_id=88 (delivery 2)
...
[poison-checker] Max deliveries reached, routing to DLQ
```

---

### DLQ Inspection

```bash
python examples/redis_poison_demo.py dlq
```

**Output:**
```
📊 DLQ size: 2 messages

🔍 Recent DLQ messages:

  Message ID: 1234567890-1
    v: <original event bytes>
    reason: poison_message
    delivery_count: 5
    idle_ms: 12000
    original_channel: users.events
    original_msg_id: 1234567890-0
```

---

### Configuration Tuning

Edit the broker config in `redis_poison_demo.py`:

```python
broker = RedisStreamsBroker(
    client=r,
    group_id="poison-example",
    
    # Adjust these for your needs
    poison_idle_ms=10_000,        # 10s (demo) vs 60s (production)
    poison_max_deliveries=5,       # 5 (demo) vs 10 (production)
    poison_check_interval_s=5.0,   # 5s (demo) vs 10s (production)
)
```

**Low latency:**
```python
poison_idle_ms=5_000          # 5 seconds
poison_check_interval_s=2.0   # Check every 2s
```

**High volume:**
```python
poison_idle_ms=120_000        # 2 minutes
poison_max_deliveries=20      # More tolerance
```

---

## Redis Commands for Debugging

### View Consumer Group
```bash
redis-cli XINFO GROUPS users.events
```

### View Pending Entries List (PEL)
```bash
# Summary
redis-cli XPENDING users.events poison-example

# Detailed
redis-cli XPENDING users.events poison-example - + 10
```

### View Stream Messages
```bash
redis-cli XRANGE users.events - + COUNT 10
```

### View DLQ
```bash
redis-cli XLEN users.events.dlq
redis-cli XRANGE users.events.dlq - + COUNT 10
```

### View Retry Queue (ZSET)
```bash
redis-cli ZRANGE users.events.retry.z 0 -1 WITHSCORES
```

### Clear Everything (Reset Demo)
```bash
redis-cli DEL users.events users.events.dlq users.events.retry.z
redis-cli DEL "evora:idempotency:*"
```

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                      Message Flow                            │
└─────────────────────────────────────────────────────────────┘

1. Publish
   └─ XADD users.events

2. Consumer
   ├─ XREADGROUP
   ├─ Handler
   └─ Success → XACK
   └─ Retryable → schedule_retry (ZADD) + XACK
   └─ Crash → No ACK (enters PEL)

3. Retry Scheduler (background)
   ├─ ZRANGEBYSCORE (find due retries)
   └─ XADD (republish)

4. Poison Checker (background)
   ├─ XPENDING (scan PEL)
   ├─ delivery_count < max → XCLAIM (reclaim)
   └─ delivery_count >= max → DLQ + XACK

5. DLQ
   └─ XADD users.events.dlq (with metadata)
```

---

## Observability

### Watch Consumer Logs
```bash
python examples/redis_poison_demo.py --consumer 2>&1 | tee consumer.log
```

### Monitor Redis
```bash
redis-cli MONITOR | grep "users.events"
```

### Track Metrics (Future)
- `evora.poison.reclaimed` - XCLAIM count
- `evora.poison.dlq` - DLQ routing count
- `evora.pel.depth` - Current PEL size
- `evora.retry.scheduled` - Retry queue additions

---

## Production Checklist

Before deploying poison detection:

- [ ] Set appropriate `poison_idle_ms` (60s+ recommended)
- [ ] Set `poison_max_deliveries` (10+ recommended)
- [ ] Monitor DLQ depth with alerts
- [ ] Set up PEL size monitoring
- [ ] Configure structured logging
- [ ] Add metrics/tracing
- [ ] Test crash recovery scenarios
- [ ] Document DLQ replay procedures

---

## Next Steps

1. ✅ Run all 4 scenarios
2. ✅ Inspect DLQ messages
3. ✅ Tune configuration for your workload
4. 📖 Read: `docs/POISON_MESSAGE_HANDLING.md`
5. 🚀 Deploy to production

---

## Troubleshooting

### Consumer not processing messages
```bash
# Check consumer group exists
redis-cli XINFO GROUPS users.events

# Create manually if needed
redis-cli XGROUP CREATE users.events poison-example $ MKSTREAM
```

### Messages stuck in PEL
```bash
# View PEL
redis-cli XPENDING users.events poison-example - + 10

# Manual reclaim (if needed)
redis-cli XCLAIM users.events poison-example my-consumer 60000 <msg_id>
```

### DLQ growing too fast
- Check for infinite crash loops
- Review handler error handling
- Lower `poison_max_deliveries` temporarily
- Add circuit breakers

---

## Questions?

See full documentation:
- `docs/ARCHITECTURE.md` - System overview
- `docs/POISON_MESSAGE_HANDLING.md` - Deep dive on poison detection
- `evora/brokers/redis_streams.py` - Implementation details
