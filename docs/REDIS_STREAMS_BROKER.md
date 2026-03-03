# Redis Streams Broker - Complete Guide

## Table of Contents
1. [What You've Built](#what-youve-built)
2. [Architecture Overview](#architecture-overview)
3. [Key Features](#key-features)
4. [How to Use It](#how-to-use-it)
5. [What It Offers](#what-it-offers)
6. [Message Lifecycle](#message-lifecycle)
7. [Configuration](#configuration)
8. [Production Considerations](#production-considerations)
9. [Monitoring & Observability](#monitoring--observability)
10. [Comparison with Other Solutions](#comparison-with-other-solutions)

---

## What You've Built

You've built a **production-grade, durable message broker** for the Evora event runtime using Redis Streams. This is not a simple pub/sub system or a thin Kafka wrapper—it's a complete reliability infrastructure for event-driven applications.

### Core Capabilities

- ✅ **Durable Message Processing**: Messages survive consumer crashes
- ✅ **Automatic Retry with Exponential Backoff**: Failed messages are retried intelligently
- ✅ **Poison Message Protection**: Stuck messages are detected and handled automatically
- ✅ **Dead Letter Queue (DLQ)**: Unrecoverable messages are routed with full metadata
- ✅ **Consumer Groups**: Multiple workers process messages in parallel
- ✅ **Crash-Safe Retry Scheduling**: Retry state persists in Redis (not in-memory)
- ✅ **At-Least-Once Delivery**: With idempotency layer → exactly-once semantics

---

## Architecture Overview

### High-Level Design

```
┌─────────────┐
│  Publisher  │
└──────┬──────┘
       │ XADD
       ▼
┌─────────────────────────────────┐
│   Redis Stream (Main Channel)   │
│   users.events                  │
└──────────┬──────────────────────┘
           │ XREADGROUP
           ▼
┌─────────────────────────┐
│   Consumer Group        │
│   (Multiple Workers)    │
└──────┬──────────────────┘
       │
       ├─── ✅ Success → XACK
       │
       ├─── ⚠️  Retryable Error
       │         │
       │         ▼
       │    ┌──────────────────┐
       │    │  ZSET Retry Queue│ (score = due time)
       │    │  users.events    │
       │    │  .retry.z        │
       │    └────────┬─────────┘
       │             │
       │             │ Retry Scheduler
       │             │ (Background Task)
       │             │
       │             ▼
       │         XADD (attempt++)
       │             │
       │             └──────┐
       │                    │
       └─── ❌ Fatal Error / Max Attempts
                    │
                    ▼
            ┌──────────────────┐
            │  DLQ Stream      │
            │  users.events.dlq│
            └──────────────────┘
```

### Three-Layer Architecture

#### 1. **Application Layer** (`evora.App`)
- Event decoding & validation
- Handler routing
- Idempotency checking
- Error classification (RetryableError vs FatalError)
- Telemetry hooks

#### 2. **Broker Layer** (`RedisStreamsBroker`)
- Message transport (publish/consume)
- Durable retry scheduling (ZSET delay queue)
- Poison message detection (PEL monitoring)
- DLQ routing
- Consumer group management

#### 3. **Storage Layer** (Redis)
- **Streams**: Main event channels
- **ZSETs**: Delayed retry queues (score = due timestamp)
- **Consumer Groups**: Offset tracking & PEL
- **DLQ Streams**: Failed message storage

---

## Key Features

### 1. Durable Retry Scheduling

**Problem**: In-memory retry timers don't survive crashes.

**Solution**: Failed messages are stored in a Redis Sorted Set (ZSET) with the due timestamp as the score.

```python
# Message fails at attempt 1
await broker.schedule_retry(
    msg=msg,
    raw_value=b'{"user_id": 123}',
    headers={"version": "1.0"},
    attempt=1,
    error_type="RetryableError",
    error_message="DB timeout"
)

# ZADD users.events.retry.z <timestamp+500ms> <payload>
# Original message is ACKed from stream
# Consumer can crash—retry state is in Redis
```

**Background scheduler** (runs every 500ms):
```python
# Polls ZSET for due messages
due_messages = ZRANGEBYSCORE users.events.retry.z -inf <now>

# Re-adds them to stream with incremented attempt
XADD users.events * v <payload> a 2 ...
ZREM users.events.retry.z <payload>
```

**Exponential backoff**:
- Attempt 1: 500ms
- Attempt 2: 1000ms
- Attempt 3: 2000ms
- Attempt 4: 4000ms
- Attempt 5: 8000ms
- Capped at 30 seconds

### 2. Poison Message Protection

**Problem**: If a consumer crashes mid-processing, the message sits in the Pending Entry List (PEL) forever.

**Solution**: Automatic detection and reclamation of stuck messages.

```python
# Consumer loop periodically checks PEL
stuck = await broker._reclaim_stuck(
    channel="users.events",
    min_idle_ms=60_000,  # 60 seconds
    count=25
)

# Reclaimed messages are processed immediately
# If attempt > poison_max_deliveries (10) → DLQ
```

**Detection Strategy**:
- Uses `XAUTOCLAIM` (Redis 6.2+) or fallback to `XPENDING_RANGE` + `XCLAIM`
- Messages idle > 60 seconds are reclaimed
- After 10 delivery attempts → moved to DLQ

### 3. Dead Letter Queue (DLQ)

**Messages go to DLQ when**:
- Handler raises `FatalError`
- Max retry attempts exceeded
- Poison message threshold exceeded
- Handler crashes unexpectedly

**DLQ Metadata**:
```python
{
    "v": b'<original_payload>',
    "reason": "poison_max_attempts_exceeded",
    "attempt": 11,
    "error": "RetryableError",
    "message": "Database connection timeout"
}
```

**Stream name**: `<channel>.dlq` (e.g., `users.events.dlq`)

### 4. Consumer Groups

**Load Balancing**:
- Multiple consumers share workload
- Each message delivered to exactly one consumer
- Redis handles distribution automatically

**Fault Tolerance**:
- If Consumer A crashes, Consumer B reclaims its pending messages
- No message loss
- No duplicate processing (with idempotency layer)

---

## How to Use It

### Installation

```bash
pip install redis anyio
```

### Basic Setup

```python
import redis.asyncio as redis
from evora.brokers.redis_streams import RedisStreamsBroker
from evora import App

# 1. Create Redis client
redis_client = await redis.from_url("redis://localhost:6379")

# 2. Create broker
broker = RedisStreamsBroker(
    client=redis_client,
    group_id="user-service",  # Consumer group name
    consumer_name="worker-1"  # Unique worker identifier
)

# 3. Create Evora app
app = App(
    broker=broker,
    idempotency_policy=...,
    strict_mode=True
)
```

### Publishing Events

```python
# Simple publish
await broker.publish(
    "users.events",
    value=b'{"user_id": 123, "action": "created"}',
    headers={"version": "1.0"}
)

# With partition key (for ordering)
await broker.publish(
    "users.events",
    value=b'{"user_id": 123, "action": "updated"}',
    key="user:123",  # All events for user 123 have same key
    headers={"version": "1.0"}
)
```

### Consuming Events

```python
@app.subscribe("users.events", UserCreated)
async def on_user_created(event: UserCreated):
    """
    Handler logic here.
    Raises:
        - RetryableError: Message will be retried with exponential backoff
        - FatalError: Message goes to DLQ immediately
    """
    print(f"User created: {event.user_id}")

# Run consumer
await app.run()
```

### Low-Level Consumer (Direct Broker Usage)

```python
async def handle_message(msg: Message):
    data = json.loads(msg.value)
    print(f"Processing: {data}")

await broker.run_consumer(
    channels=["users.events", "orders.events"],
    handler=handle_message,
    consumer_name="worker-1"
)
```

---

## What It Offers

### For Developers

#### 1. **Reliability Without Complexity**
- No Kafka clusters to manage
- No Zookeeper coordination
- Single Redis instance for dev
- Redis Cluster for production scale

#### 2. **Predictable Error Handling**
```python
@app.subscribe("orders.events", OrderCreated)
async def process_order(event: OrderCreated):
    try:
        await payment_service.charge(event.order_id)
    except PaymentTimeout:
        # Automatic retry with exponential backoff
        raise RetryableError("Payment service timeout")
    except InvalidCard:
        # Immediate DLQ, no retry
        raise FatalError("Invalid payment method")
```

#### 3. **Exactly-Once Semantics**
```python
@app.subscribe("users.events", UserCreated)
@idempotent(key=lambda e: e.user_id)
async def send_welcome_email(event: UserCreated):
    # Even if message is redelivered, email sent only once
    await email_service.send(event.email, "Welcome!")
```

#### 4. **Observability Built-In**
```python
# Track PEL size (stuck messages)
pel = await redis_client.xpending("users.events", "user-service")

# Track retry queue size
retry_count = await redis_client.zcard("users.events.retry.z")

# Track DLQ size
dlq_len = await redis_client.xlen("users.events.dlq")
```

### For Operations

#### 1. **Zero Data Loss**
- All state in Redis (persisted with AOF/RDB)
- Consumer crashes → messages in PEL
- PEL messages automatically reclaimed
- Retry state survives crashes

#### 2. **Horizontal Scalability**
```bash
# Add more consumers to same group
docker run app --consumer-name worker-1
docker run app --consumer-name worker-2
docker run app --consumer-name worker-3

# Redis load-balances automatically
# Each message processed by exactly one worker
```

#### 3. **Resource Efficiency**
- Blocking reads (not tight loops)
- CPU-efficient idle consumers
- Batch processing (configurable)
- Exponential backoff prevents thundering herd

#### 4. **Operational Commands**

**Inspect PEL**:
```bash
redis-cli XPENDING users.events user-service
```

**Inspect Retry Queue**:
```bash
redis-cli ZCARD users.events.retry.z
redis-cli ZRANGE users.events.retry.z 0 -1 WITHSCORES
```

**Inspect DLQ**:
```bash
redis-cli XLEN users.events.dlq
redis-cli XREAD COUNT 10 STREAMS users.events.dlq 0
```

**Replay DLQ Messages** (manual recovery):
```python
async def replay_dlq():
    msgs = await redis_client.xread({"users.events.dlq": "0"}, count=100)
    for stream, entries in msgs:
        for msg_id, fields in entries:
            # Re-publish to main stream
            await broker.publish(
                "users.events",
                value=fields[b"v"],
                headers={}
            )
            # Delete from DLQ
            await redis_client.xdel("users.events.dlq", msg_id)
```

---

## Message Lifecycle

### Normal Flow (Success)

```
1. Publish → XADD users.events
2. Consumer Group reads → XREADGROUP
3. Handler processes successfully
4. ACK → XACK users.events
5. Message removed from PEL
```

### Retry Flow (Transient Failure)

```
1. Publish → XADD users.events (attempt=1)
2. Consumer reads → XREADGROUP
3. Handler raises RetryableError
4. schedule_retry:
   - ZADD users.events.retry.z <due_time> <payload>
   - XACK users.events (remove from PEL)
5. Retry Scheduler (background):
   - ZRANGEBYSCORE finds due messages
   - XADD users.events (attempt=2)
   - ZREM users.events.retry.z
6. Consumer reads attempt=2
7. Handler succeeds
8. XACK users.events
```

### DLQ Flow (Permanent Failure)

```
1. Message fails 5 times (attempt=5)
2. Handler raises FatalError OR max_attempts reached
3. XADD users.events.dlq with metadata
4. XACK users.events (remove from PEL)
5. Manual inspection/replay from DLQ
```

### Poison Message Flow (Consumer Crash)

```
1. Consumer A receives message
2. Consumer A crashes before ACK
3. Message sits in PEL, owned by Consumer A
4. 60 seconds pass (poison_idle_ms)
5. Consumer B runs _reclaim_stuck
6. XAUTOCLAIM or XCLAIM message to Consumer B
7. Consumer B processes message
8. If attempt > 10 → DLQ
9. Else → normal retry flow
```

---

## Configuration

### Broker Configuration

```python
broker = RedisStreamsBroker(
    client=redis_client,
    group_id="user-service",
    consumer_name="worker-1",
    
    # Behavior
    block_ms=2000,           # XREADGROUP blocking time
    batch_size=50,            # Messages per read
    mkstream=True,            # Auto-create streams
    dlq_suffix=".dlq",        # DLQ stream suffix
    retry_zset_suffix=".retry.z",  # Retry ZSET suffix
    
    # Retry backoff
    base_delay_ms=500,        # Initial retry delay
    max_delay_ms=30_000,      # Max retry delay (30s)
    
    # Poison detection
    poison_idle_ms=60_000,    # 60s idle → reclaim
    poison_max_deliveries=10, # 10 attempts → DLQ
)
```

### Redis Persistence

**Development**:
```redis
# redis.conf
appendonly no
save ""
```

**Production**:
```redis
# redis.conf
appendonly yes
appendfsync everysec
save 900 1
save 300 10
save 60 10000
```

---

## Production Considerations

### 1. Consumer Naming

**Important**: Each consumer in a group must have a unique name.

```python
# Good: Unique per instance
consumer_name = f"{socket.gethostname()}-{os.getpid()}"

# Good: Kubernetes pod name
consumer_name = os.environ.get("HOSTNAME", "worker-1")

# Bad: Same name for multiple instances
consumer_name = "worker"  # All instances claim same name!
```

### 2. Stream Trimming

Redis Streams grow indefinitely. Trim them periodically:

```python
# Keep last 1M messages
await redis_client.xtrim("users.events", maxlen=1_000_000, approximate=True)

# Or keep messages from last 7 days
# (requires manual timestamp-based logic)
```

### 3. Redis Cluster

For production scale, use Redis Cluster:

```python
from redis.asyncio.cluster import RedisCluster

redis_client = RedisCluster.from_url("redis://cluster-host:6379")
broker = RedisStreamsBroker(client=redis_client, ...)
```

**Note**: All related keys (stream, retry ZSET, DLQ) must be on same node. Use hash tags:

```python
# Ensures stream, retry ZSET, and DLQ on same shard
channel = "{users}.events"
# Results in:
# - {users}.events (stream)
# - {users}.events.retry.z (ZSET)
# - {users}.events.dlq (stream)
```

### 4. Monitoring

**Key Metrics**:

```python
# Consumer lag
info = await redis_client.xinfo_groups("users.events")
for group in info:
    lag = group["lag"]  # Messages not yet consumed
    pel_count = group["pending"]  # Messages in PEL

# Retry queue depth
retry_depth = await redis_client.zcard("users.events.retry.z")

# DLQ size
dlq_size = await redis_client.xlen("users.events.dlq")

# Stream growth rate
stream_len = await redis_client.xlen("users.events")
```

**Alerts**:
- PEL size growing → consumers stuck or slow
- Retry queue growing → systemic failures
- DLQ growing → permanent failures (needs investigation)
- Consumer lag growing → add more consumers

### 5. Graceful Shutdown

```python
import signal
import asyncio

consumer_task = None

async def shutdown():
    if consumer_task:
        consumer_task.cancel()
        try:
            await consumer_task
        except asyncio.CancelledError:
            pass
    await redis_client.close()

signal.signal(signal.SIGTERM, lambda s, f: asyncio.create_task(shutdown()))
```

### 6. Error Handling

```python
while True:
    try:
        await broker.run_consumer(
            channels=["users.events"],
            handler=handle_message,
            consumer_name="worker-1"
        )
    except redis.ConnectionError:
        logger.error("Redis connection lost, retrying in 5s")
        await asyncio.sleep(5)
    except Exception as e:
        logger.exception("Fatal error in consumer")
        raise
```

---

## Monitoring & Observability

### Built-In Redis Commands

```bash
# Consumer group info
redis-cli XINFO GROUPS users.events

# Consumer info
redis-cli XINFO CONSUMERS users.events user-service

# PEL inspection
redis-cli XPENDING users.events user-service - + 10

# Stream length
redis-cli XLEN users.events

# Retry queue size
redis-cli ZCARD users.events.retry.z

# DLQ inspection
redis-cli XLEN users.events.dlq
redis-cli XRANGE users.events.dlq - + COUNT 10
```

### Prometheus Metrics (Example)

```python
from prometheus_client import Counter, Gauge, Histogram

messages_processed = Counter(
    "evora_messages_processed_total",
    "Total messages processed",
    ["channel", "status"]  # success, retry, dlq
)

pel_size = Gauge(
    "evora_pel_size",
    "Messages in Pending Entry List",
    ["channel", "group"]
)

retry_queue_size = Gauge(
    "evora_retry_queue_size",
    "Messages in retry queue",
    ["channel"]
)

processing_duration = Histogram(
    "evora_processing_duration_seconds",
    "Message processing duration",
    ["channel"]
)
```

### Grafana Dashboard

**Panels**:
1. **Throughput**: Messages/sec per channel
2. **PEL Size**: Track stuck messages
3. **Retry Queue Depth**: Backlog of retries
4. **DLQ Growth Rate**: Permanent failures
5. **Consumer Lag**: Messages not yet consumed
6. **Processing Latency**: P50, P95, P99

---

## Comparison with Other Solutions

### vs. Kafka

| Feature | Evora + Redis Streams | Kafka |
|---------|----------------------|-------|
| **Setup Complexity** | Single Redis instance | Kafka + Zookeeper cluster |
| **Operational Overhead** | Low | High |
| **Throughput** | 10K-100K msg/s | 100K-1M+ msg/s |
| **Durability** | Redis persistence (AOF/RDB) | Log-based, replicated |
| **Consumer Groups** | ✅ Built-in | ✅ Built-in |
| **Retries** | ✅ Durable (ZSET) | Manual (Kafka Streams or custom) |
| **Poison Detection** | ✅ Automatic | Manual |
| **DLQ** | ✅ Automatic | Manual (separate topic) |
| **Exactly-Once** | With idempotency layer | With transactions (complex) |
| **Best For** | Microservices, event sourcing | High-throughput data pipelines |

### vs. RabbitMQ

| Feature | Evora + Redis Streams | RabbitMQ |
|---------|----------------------|----------|
| **Setup Complexity** | Low | Medium |
| **Message Ordering** | ✅ Strict (per stream) | ✅ Strict (per queue) |
| **Consumer Groups** | ✅ | ✅ (competing consumers) |
| **Retries** | ✅ Durable (ZSET) | ✅ (DLX + TTL) |
| **Poison Detection** | ✅ Automatic | ✅ (max redeliveries) |
| **Replay** | ✅ (historical messages) | ❌ (consumed = deleted) |
| **Performance** | Higher throughput | Lower throughput |
| **Best For** | Event-driven microservices | Task queues, RPC |

### vs. AWS SQS/SNS

| Feature | Evora + Redis Streams | AWS SQS/SNS |
|---------|----------------------|-------------|
| **Cost** | Redis hosting only | Per-request pricing |
| **Latency** | Sub-millisecond | 10-100ms |
| **Consumer Groups** | ✅ | ❌ (SNS fanout) |
| **Ordering** | ✅ Strict | ✅ (FIFO queues, limited throughput) |
| **Retries** | ✅ Exponential backoff | ✅ Exponential backoff |
| **DLQ** | ✅ | ✅ |
| **Replay** | ✅ | ❌ |
| **Best For** | Self-hosted, low-latency | Serverless, AWS-native |

---

## Summary

### What You Have Now

A **production-ready event processing runtime** that provides:

1. ✅ **Reliability**: Messages survive crashes, durable retries, automatic poison detection
2. ✅ **Scalability**: Consumer groups, horizontal scaling, efficient resource usage
3. ✅ **Observability**: PEL tracking, retry queue monitoring, DLQ inspection
4. ✅ **Developer Experience**: Simple API, predictable error handling, typed events
5. ✅ **Operational Simplicity**: Single Redis instance (dev), Redis Cluster (prod), no Kafka/Zookeeper

### What's Next (Future Enhancements)

1. **Schema Governance**: CLI tools for schema compatibility checks
2. **OpenTelemetry Integration**: Distributed tracing, structured logging
3. **Outbox Pattern**: Transactional event publishing
4. **Stream Archival**: Automatic cold storage for old messages
5. **Multi-Region**: Active-active Redis replication

### This Is Production-Grade

You've built something that rivals commercial event platforms in reliability, while being simpler to operate and reason about. It's no longer a demo—it's a **real reliability runtime**.

---

**Questions? Issues? Improvements?**  
File an issue or contribute at: https://github.com/yourusername/evora
