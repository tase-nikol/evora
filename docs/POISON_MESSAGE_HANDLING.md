# Poison Message Detection & Handling

## Overview

The Redis Streams broker now includes **comprehensive poison message protection** that prevents messages from getting stuck indefinitely in the Pending Entries List (PEL).

This is a critical reliability feature that handles scenarios where:
- Consumer crashes mid-processing
- Network failures leave messages unacknowledged
- Bugs cause infinite processing loops
- Messages repeatedly fail processing despite retries

---

## 🔍 How It Works

### Three-Layer Defense

```
┌─────────────────────────────────────────────────────────────┐
│                    Message Lifecycle                        │
└─────────────────────────────────────────────────────────────┘

1. Normal Processing
   ├─ XREADGROUP → Message delivered
   ├─ Handler executes
   └─ XACK → Success

2. Retryable Failure (App Layer)
   ├─ Handler raises RetryableError
   ├─ schedule_retry() → ZSET delay queue
   ├─ XACK original message
   └─ Retry scheduler re-publishes after delay

3. Poison Message Detection (Broker Layer)
   ├─ Background scanner checks XPENDING
   ├─ Identifies idle messages (>60s by default)
   ├─ Tracks delivery count
   └─ Takes action:
       ├─ delivery_count < 10 → XCLAIM (reclaim)
       └─ delivery_count >= 10 → DLQ + XACK
```

---

## 🚨 What Makes a Message "Poisonous"

A message becomes poisonous when:

1. **Idle too long**: Sits in PEL for `poison_idle_ms` (default: 60 seconds)
2. **Delivered too many times**: Exceeds `poison_max_deliveries` (default: 10)

### Why This Matters

Without poison detection:
- Crashed consumers leave messages in PEL forever
- Messages can block consumer group progress
- No visibility into stuck processing
- Manual intervention required

With poison detection:
- Automatic reclaim of idle messages
- Enforced delivery count limits
- Structured DLQ routing with metadata
- Self-healing consumer groups

---

## ⚙️ Configuration

```python
broker = RedisStreamsBroker(
    client=redis_client,
    group_id="my-service",
    
    # Poison message configuration
    poison_idle_ms=60_000,          # 60 seconds before reclaim
    poison_max_deliveries=10,        # max deliveries before DLQ
    poison_check_interval_s=10.0,    # scan PEL every 10 seconds
)
```

### Configuration Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `poison_idle_ms` | 60,000 | Milliseconds a message can be idle in PEL before reclaim |
| `poison_max_deliveries` | 10 | Maximum delivery attempts before DLQ routing |
| `poison_check_interval_s` | 10.0 | How often to scan XPENDING (seconds) |

### Tuning Guidance

**Low-latency systems:**
```python
poison_idle_ms=10_000          # 10 seconds
poison_check_interval_s=5.0    # Check every 5s
```

**High-volume systems:**
```python
poison_idle_ms=120_000         # 2 minutes
poison_max_deliveries=20       # More delivery tolerance
```

**Strict reliability:**
```python
poison_idle_ms=30_000          # 30 seconds
poison_max_deliveries=5        # Fail fast
```

---

## 🔄 Reclaim Flow (XCLAIM)

When a message is idle but under delivery limit:

```
1. XPENDING returns idle message
   ├─ msg_id: 1234567890-0
   ├─ idle_ms: 65000
   └─ delivery_count: 3

2. Check threshold
   └─ 65000 > 60000 ✓
   └─ 3 < 10 ✓

3. XCLAIM
   ├─ Transfers ownership to this consumer
   ├─ Resets idle timer
   └─ Message re-enters processing loop

4. Handler gets retry
   └─ Can succeed or fail again
```

**Key behavior:**
- Ownership transfers to **current consumer** scanning PEL
- Idle time resets to 0
- Delivery count increments
- Message re-enters normal XREADGROUP flow

---

## 💀 DLQ Routing (Max Deliveries)

When delivery count exceeds limit:

```
1. XPENDING returns poison message
   ├─ msg_id: 1234567890-0
   ├─ idle_ms: 70000
   └─ delivery_count: 10

2. Check threshold
   └─ 10 >= 10 ✓ (POISON!)

3. Route to DLQ
   ├─ XADD <channel>.dlq
   │   ├─ v: <original payload>
   │   ├─ reason: "poison_message"
   │   ├─ delivery_count: "10"
   │   ├─ idle_ms: "70000"
   │   ├─ original_channel: "users.events"
   │   └─ original_msg_id: "1234567890-0"
   └─ XACK original message

4. PEL cleared
   └─ Consumer group can progress
```

### DLQ Message Format

```json
{
  "v": "<original event bytes>",
  "reason": "poison_message",
  "delivery_count": "10",
  "idle_ms": "70000",
  "original_channel": "users.events",
  "original_msg_id": "1234567890-0"
}
```

This provides **full forensic metadata** for debugging.

---

## 🏗 Background Task Architecture

The broker runs **three parallel tasks**:

### 1. Main Consumer Loop
- Calls `XREADGROUP` to fetch messages
- Dispatches to handler
- ACKs on success

### 2. Retry Scheduler
- Polls ZSET delay queues every 500ms
- Re-publishes due retries via `XADD`
- Removes from ZSET

### 3. Poison Checker (NEW)
- Scans `XPENDING` every `poison_check_interval_s`
- For each idle message:
  - Under limit → `XCLAIM`
  - Over limit → DLQ + `XACK`
- Never crashes main loop (exception handling)

```python
async with anyio.create_task_group() as tg:
    tg.start_soon(loop)              # Main consumer
    tg.start_soon(retry_scheduler)   # Retry queue processor
    tg.start_soon(poison_checker)    # PEL scanner
```

All three run concurrently, independently, crash-safely.

---

## 🔒 Reliability Properties

With poison detection enabled, you now have:

| Property | Status |
|----------|--------|
| Crash-safe retry | ✅ |
| Durable delay queue | ✅ |
| Idempotency enforcement | ✅ |
| Automatic reclaim of idle messages | ✅ |
| Delivery count enforcement | ✅ |
| Structured DLQ metadata | ✅ |
| Consumer group self-healing | ✅ |
| PEL monitoring | ✅ |
| Protection against stuck messages | ✅ |

---

## 📊 Operational Visibility

### Monitoring Poison Messages

You can inspect PEL manually:

```bash
# View pending summary
redis-cli XPENDING users.events my-service

# View detailed pending entries
redis-cli XPENDING users.events my-service - + 100

# Check DLQ
redis-cli XLEN users.events.dlq
redis-cli XRANGE users.events.dlq - + COUNT 10
```

### Expected Metrics (Future)

When observability is integrated:

- `evora.poison.reclaimed` - Count of XCLAIM operations
- `evora.poison.dlq` - Count of poison → DLQ
- `evora.pel.size` - Current PEL depth per channel
- `evora.pel.max_idle` - Longest idle message in PEL

---

## 🧪 Testing Poison Detection

### Simulate Crash

```python
# In your handler
async def handle_user_event(event: UserEvent, ctx: Context):
    if event.user_id == 999:
        import sys
        sys.exit(1)  # Hard crash - no ACK sent
```

**Expected behavior:**
1. Message enters PEL
2. After 60s, poison checker detects idle message
3. `XCLAIM` transfers to active consumer
4. Message reprocessed
5. After 10 deliveries → DLQ

### Simulate Poison

```python
async def handle_user_event(event: UserEvent, ctx: Context):
    if event.user_id == 999:
        # Don't ACK, hang forever
        await asyncio.sleep(999999)
```

**Expected behavior:**
1. Consumer hangs on this message
2. After 60s, poison checker reclaims
3. Next consumer gets it
4. Repeat until delivery_count = 10
5. DLQ routing

---

## 🎯 Comparison: App Retry vs Poison Detection

### App-Level Retry (schedule_retry)
- **Purpose**: Transient failures (DB timeout, rate limit)
- **Mechanism**: ZSET delay queue
- **Scope**: Retryable/unknown errors
- **ACK timing**: Immediate (after scheduling retry)
- **Max attempts**: Configured per handler (default: 5)

### Broker-Level Poison Detection
- **Purpose**: Stuck/crashed processing
- **Mechanism**: XPENDING scan + XCLAIM
- **Scope**: All PEL messages (any failure mode)
- **ACK timing**: After DLQ routing
- **Max attempts**: Global delivery limit (default: 10)

**They work together:**
- App retry handles expected failures gracefully
- Poison detection handles unexpected failures (crashes, hangs)
- Both prevent infinite loops
- Both route to DLQ eventually

---

## 🚀 Production Readiness

### What You Have Now

✅ Durable retry with exponential backoff  
✅ Idempotency enforcement  
✅ Consumer group coordination  
✅ Poison message detection  
✅ Automatic PEL cleanup  
✅ Delivery count limits  
✅ DLQ with forensic metadata  
✅ Multi-consumer safety (XCLAIM)  

### What's Still Missing

🔲 **Observability**: Metrics, tracing, structured logs  
🔲 **Schema governance**: Breaking change detection  
🔲 **Outbox pattern**: Transactional publishing  
🔲 **Admin tooling**: PEL inspection, manual reclaim  

---

## 📖 Next Steps

1. **Deploy with defaults**: Start with safe 60s/10-delivery limits
2. **Monitor DLQ**: Set up alerts on DLQ depth
3. **Tune thresholds**: Adjust based on your workload
4. **Add observability**: Instrument poison checker with metrics
5. **Build admin tools**: CLI for PEL inspection

---

## 🎓 Deep Dive: Redis XPENDING

The poison checker relies on `XPENDING_RANGE`:

```python
pending = await client.xpending_range(
    name="users.events",
    groupname="my-service",
    min="-",
    max="+",
    count=100,
)
```

**Returns:**
```python
[
    {
        "message_id": "1234567890-0",
        "consumer": "worker-1-12345",
        "time_since_delivered": 65000,  # milliseconds
        "times_delivered": 3
    },
    # ...
]
```

**Key fields:**
- `time_since_delivered`: Idle duration
- `times_delivered`: How many times this message was delivered

We use these to make reclaim/DLQ decisions.
 