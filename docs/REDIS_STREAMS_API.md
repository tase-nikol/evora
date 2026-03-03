# Redis Streams Broker - API Reference

## Table of Contents
- [RedisStreamsBroker Class](#redisstreambroker-class)
- [Public Methods](#public-methods)
- [Configuration Parameters](#configuration-parameters)
- [Helper Functions](#helper-functions)
- [Message Format](#message-format)
- [Error Handling](#error-handling)

---

## RedisStreamsBroker Class

```python
@dataclass
class RedisStreamsBroker:
    """Production-grade message broker using Redis Streams."""
```

### Constructor Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `client` | `redis.Redis` | Required | Async Redis client instance |
| `group_id` | `str` | Required | Consumer group name (typically service name) |
| `consumer_name` | `str \| None` | `None` | Unique consumer identifier (auto-generated if None) |
| `block_ms` | `int` | `2000` | XREADGROUP blocking time in milliseconds |
| `batch_size` | `int` | `50` | Maximum messages per XREADGROUP call |
| `mkstream` | `bool` | `True` | Auto-create stream if it doesn't exist |
| `dlq_suffix` | `str` | `".dlq"` | Suffix for DLQ stream names |
| `retry_zset_suffix` | `str` | `".retry.z"` | Suffix for retry ZSET names |
| `base_delay_ms` | `int` | `500` | Base retry delay (exponential backoff) |
| `max_delay_ms` | `int` | `30000` | Maximum retry delay cap (30 seconds) |
| `poison_idle_ms` | `int` | `60000` | Idle time before reclaiming stuck messages (60s) |
| `poison_max_deliveries` | `int` | `10` | Max delivery attempts before DLQ |
| `poison_check_interval_s` | `float` | `10.0` | Unused (kept for compatibility) |

### Example

```python
import redis.asyncio as redis
from evora.brokers.redis_streams import RedisStreamsBroker

redis_client = await redis.from_url("redis://localhost:6379")

broker = RedisStreamsBroker(
    client=redis_client,
    group_id="user-service",
    consumer_name="worker-1",
    batch_size=100,
    base_delay_ms=1000,
    poison_max_deliveries=5
)
```

---

## Public Methods

### `publish()`

Publish a message to a Redis Stream channel.

```python
async def publish(
    self,
    channel: str,
    *,
    value: bytes,
    key: str | None = None,
    headers: dict[str, str] | None = None,
) -> None:
```

**Parameters**:
- `channel` (str): Stream channel name (e.g., "users.events")
- `value` (bytes): Raw message payload (typically JSON-encoded)
- `key` (str | None): Optional partition key for ordering
- `headers` (dict[str, str] | None): Optional metadata dictionary

**Redis Operation**: `XADD <channel> * v <value> k <key> h <headers_json> a 1`

**Example**:
```python
await broker.publish(
    "users.events",
    value=b'{"user_id": 123, "action": "created"}',
    key="user:123",
    headers={"version": "1.0", "source": "api"}
)
```

**Returns**: None

**Raises**: Redis connection errors

---

### `run_consumer()`

Run the consumer loop for processing messages.

```python
async def run_consumer(
    self,
    channels: list[str],
    handler: Callable[[Message], Awaitable[None]],
    *,
    consumer_name: str,
) -> None:
```

**Parameters**:
- `channels` (list[str]): List of stream channel names to consume from
- `handler` (Callable): Async function that processes each message
  - Signature: `async def handler(msg: Message) -> None`
- `consumer_name` (str): Unique identifier for this consumer instance

**Behavior**:
- Runs indefinitely until cancelled
- Spawns two background tasks:
  1. Consumer loop (reclaim stuck + read new messages)
  2. Retry scheduler (move due retries back to stream)

**Example**:
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

**Returns**: Does not return (runs forever)

**Raises**: Redis connection errors, cancellation errors

**Graceful Shutdown**:
```python
consumer_task = asyncio.create_task(broker.run_consumer(...))

# Later...
consumer_task.cancel()
try:
    await consumer_task
except asyncio.CancelledError:
    pass
```

---

### `schedule_retry()`

Schedule a failed message for durable retry.

```python
async def schedule_retry(
    self,
    *,
    msg: Message,
    raw_value: bytes,
    headers: dict[str, str],
    attempt: int,
    error_type: str,
    error_message: str,
) -> None:
```

**Parameters**:
- `msg` (Message): Original Message object that failed
- `raw_value` (bytes): Raw byte payload
- `headers` (dict[str, str]): Message headers
- `attempt` (int): Current attempt number (will be incremented)
- `error_type` (str): Error classification (e.g., "RetryableError")
- `error_message` (str): Human-readable error description

**Redis Operations**:
1. `ZADD <channel>.retry.z <due_timestamp> <json_payload>`
2. `XACK <channel> <group_id> <message_id>`

**Delay Calculation**: Exponential backoff
- Attempt 1: 500ms
- Attempt 2: 1000ms
- Attempt 3: 2000ms
- Attempt N: `base_delay * (2 ^ (N-1))` capped at `max_delay_ms`

**Example**:
```python
await broker.schedule_retry(
    msg=msg,
    raw_value=b'{"user_id": 123}',
    headers={"version": "1.0"},
    attempt=2,
    error_type="RetryableError",
    error_message="Database timeout"
)
```

**Returns**: None

**Raises**: Redis connection errors

---

## Private Methods (Internal Use)

### `_consumer()`

Generate unique consumer identifier.

```python
def _consumer(self) -> str:
```

**Returns**: Consumer name in format `<hostname>-<pid>` or explicitly set name

---

### `_reclaim_stuck()`

Reclaim stuck messages from PEL.

```python
async def _reclaim_stuck(
    self,
    channel: str,
    *,
    min_idle_ms: int,
    count: int = 50,
) -> list[tuple[str, dict]]:
```

**Parameters**:
- `channel` (str): Stream channel name
- `min_idle_ms` (int): Minimum idle time before reclaiming
- `count` (int): Maximum messages to reclaim

**Returns**: List of (message_id, fields) tuples

**Redis Operations**: `XAUTOCLAIM` (preferred) or `XPENDING_RANGE` + `XCLAIM` (fallback)

---

### `_decode_entry()`

Decode Redis Stream entry into Message object.

```python
async def _decode_entry(
    self, 
    channel: str, 
    msg_id, 
    fields
) -> Message | None:
```

**Parameters**:
- `channel` (str): Stream channel name
- `msg_id`: Redis message ID
- `fields`: Dict of field names to values

**Returns**: `Message` object or `None` if invalid

**Handles**: Both byte and string keys from different Redis clients

---

### `_process_one()`

Process a single message with poison detection.

```python
async def _process_one(
    self, 
    msg: Message, 
    handler, 
    *, 
    raw_msg_id
) -> None:
```

**Parameters**:
- `msg` (Message): Decoded message
- `handler`: Async callable to process message
- `raw_msg_id`: Original Redis message ID

**Behavior**:
1. Check if `attempt > poison_max_deliveries` → DLQ
2. Call handler
3. Success → ACK
4. Handler exception → DLQ (safety net)

---

### `_ensure_group()`

Ensure consumer group exists for channel.

```python
async def _ensure_group(self, channel: str) -> None:
```

**Parameters**:
- `channel` (str): Stream channel name

**Redis Operation**: `XGROUP CREATE <channel> <group_id> $ MKSTREAM`

**Idempotent**: Safe to call multiple times

---

## Helper Functions

### `_now_ms()`

Get current Unix timestamp in milliseconds.

```python
def _now_ms() -> int:
```

**Returns**: Current time in milliseconds since epoch

**Example**:
```python
>>> _now_ms()
1709377200500
```

---

### `_compute_delay_ms()`

Calculate exponential backoff delay.

```python
def _compute_delay_ms(
    attempt: int, 
    base: int = 500, 
    max_delay: int = 30000
) -> int:
```

**Parameters**:
- `attempt` (int): Retry attempt number (1-indexed)
- `base` (int): Base delay in milliseconds
- `max_delay` (int): Maximum delay cap

**Formula**: `delay = base * (2 ^ (attempt - 1))`

**Returns**: Computed delay in milliseconds (capped at `max_delay`)

**Examples**:
```python
>>> _compute_delay_ms(1)
500
>>> _compute_delay_ms(2)
1000
>>> _compute_delay_ms(5)
8000
>>> _compute_delay_ms(10)
30000  # capped
```

---

## Message Format

### Message Class

```python
@dataclass
class Message:
    channel: str
    value: bytes
    headers: dict[str, str]
    key: str | None
    message_id: str
    attempt: int
```

**Attributes**:
- `channel`: Stream channel name (e.g., "users.events")
- `value`: Raw message payload (bytes)
- `headers`: Metadata dictionary
- `key`: Optional partition key
- `message_id`: Redis message ID (e.g., "1709377200500-0")
- `attempt`: Current attempt number (1-indexed)

**Example**:
```python
msg = Message(
    channel="users.events",
    value=b'{"user_id": 123}',
    headers={"version": "1.0"},
    key="user:123",
    message_id="1709377200500-0",
    attempt=2
)
```

---

### Redis Stream Entry Format

Messages are stored in Redis Streams with these fields:

| Field | Type | Description |
|-------|------|-------------|
| `v` | bytes | Raw event payload |
| `k` | bytes | Partition key (empty if None) |
| `h` | bytes | JSON-encoded headers |
| `a` | bytes | Attempt counter (string representation) |

**Example Redis Entry**:
```
1709377200500-0:
  v: '{"user_id": 123, "action": "created"}'
  k: 'user:123'
  h: '{"version": "1.0", "source": "api"}'
  a: '1'
```

---

### Retry Queue Entry Format

Messages in retry ZSET are stored as JSON strings:

```json
{
  "channel": "users.events",
  "v": "{\"user_id\": 123}",
  "k": "user:123",
  "h": {"version": "1.0"},
  "a": 2,
  "err": {
    "type": "RetryableError",
    "message": "Database timeout"
  }
}
```

**ZSET Score**: Due timestamp in milliseconds

---

### DLQ Entry Format

Messages in DLQ streams contain:

| Field | Description |
|-------|-------------|
| `v` | Original payload |
| `reason` | Why DLQ'd ("poison_max_attempts_exceeded", "broker_handler_exception") |
| `attempt` | Final attempt number |
| `error` | Exception type (for handler crashes) |
| `message` | Exception message (for handler crashes) |

**Example**:
```
1709377260500-0:
  v: '{"user_id": 123}'
  reason: 'poison_max_attempts_exceeded'
  attempt: '11'
```

---

## Error Handling

### RetryableError

Indicates a transient failure that should be retried.

```python
from evora.errors import RetryableError

raise RetryableError("Database connection timeout")
```

**Behavior**:
- Message scheduled for retry with exponential backoff
- Original message ACKed from stream
- Retry state persists in Redis ZSET
- After `poison_max_deliveries` attempts → DLQ

---

### FatalError

Indicates a permanent failure that should not be retried.

```python
from evora.errors import FatalError

raise FatalError("Invalid email format")
```

**Behavior**:
- Message immediately sent to DLQ
- Original message ACKed from stream
- No retry attempted

---

### Unhandled Exceptions

Any exception not caught by handler triggers broker safety net:

```python
raise ValueError("Unexpected crash")
```

**Behavior**:
- Message sent to DLQ with exception details
- Original message ACKed
- Error logged with full traceback

**DLQ Entry**:
```
reason: 'broker_handler_exception'
error: 'ValueError'
message: 'Unexpected crash'
```

---

## Redis Data Structures

### Main Stream: `<channel>`

**Type**: Redis Stream

**Purpose**: Primary event channel

**Example**: `users.events`

**Operations**:
- `XADD`: Publish messages
- `XREADGROUP`: Consume messages
- `XACK`: Acknowledge processed messages
- `XPENDING`: View pending messages (PEL)

---

### Retry Queue: `<channel>.retry.z`

**Type**: Redis Sorted Set (ZSET)

**Purpose**: Delayed retry queue

**Score**: Due timestamp in milliseconds

**Example**: `users.events.retry.z`

**Operations**:
- `ZADD`: Schedule retry
- `ZRANGEBYSCORE`: Find due messages
- `ZREM`: Remove after re-adding to stream
- `ZCARD`: Count pending retries

---

### DLQ Stream: `<channel>.dlq`

**Type**: Redis Stream

**Purpose**: Dead letter queue for failed messages

**Example**: `users.events.dlq`

**Operations**:
- `XADD`: Send failed messages
- `XREAD`: Inspect DLQ
- `XLEN`: Count DLQ size
- `XDEL`: Remove after manual replay

---

### Consumer Group: `<group_id>`

**Type**: Redis Streams Consumer Group

**Purpose**: Track consumer offsets and pending messages

**Example**: `user-service`

**Operations**:
- `XGROUP CREATE`: Create group
- `XINFO GROUPS`: View group info
- `XINFO CONSUMERS`: View consumers in group
- `XPENDING`: View PEL

---

## Performance Characteristics

### Throughput

- **Single Consumer**: 10,000-50,000 msg/s (depends on handler complexity)
- **Multiple Consumers**: Linear scaling with consumer count
- **Batch Processing**: Up to `batch_size` messages per iteration

### Latency

- **Normal Processing**: Sub-millisecond to milliseconds (depends on handler)
- **Retry Delay Accuracy**: ±500ms (scheduler polling interval)
- **Poison Detection**: Within `poison_idle_ms` (default 60s)

### Resource Usage

- **CPU**: Low (blocking reads, not tight loops)
- **Memory**: Minimal (no in-memory buffers)
- **Redis Memory**: ~1KB per message in stream + retry queue
- **Network**: Batch reads reduce round trips

---

## Best Practices

### 1. Unique Consumer Names

```python
# ✅ Good
consumer_name = f"{socket.gethostname()}-{os.getpid()}"

# ❌ Bad
consumer_name = "worker"  # Same for all instances
```

### 2. Idempotent Handlers

```python
# ✅ Good
async def handler(msg: Message):
    if await already_processed(msg.message_id):
        return
    await process(msg)
    await mark_processed(msg.message_id)

# ❌ Bad
async def handler(msg: Message):
    await process(msg)  # Duplicate processing possible
```

### 3. Error Classification

```python
# ✅ Good
try:
    await process(msg)
except ConnectionError:
    raise RetryableError("Transient failure")
except ValueError:
    raise FatalError("Invalid data")

# ❌ Bad
try:
    await process(msg)
except Exception:
    raise  # Unclear retry behavior
```

### 4. Stream Trimming

```python
# ✅ Good - Periodic trimming
await redis_client.xtrim("users.events", maxlen=1_000_000, approximate=True)

# ❌ Bad - Unbounded growth
# Stream grows indefinitely
```

### 5. Monitoring

```python
# ✅ Good - Track key metrics
pel_size = await redis_client.xpending("users.events", "user-service")
retry_depth = await redis_client.zcard("users.events.retry.z")
dlq_size = await redis_client.xlen("users.events.dlq")

# Alert on anomalies
if pel_size["pending"] > 1000:
    logger.warning("High PEL size")
```

---

## Type Signatures

For type checking with mypy/pyright:

```python
from typing import Protocol, Awaitable

class MessageHandler(Protocol):
    async def __call__(self, msg: Message) -> None: ...

class RedisStreamsBroker:
    async def publish(
        self,
        channel: str,
        *,
        value: bytes,
        key: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None: ...
    
    async def run_consumer(
        self,
        channels: list[str],
        handler: MessageHandler,
        *,
        consumer_name: str,
    ) -> None: ...
```

---

## See Also

- [Complete Guide](./REDIS_STREAMS_BROKER.md) - Full documentation
- [Quick Start](./REDIS_STREAMS_QUICKSTART.md) - 5-minute tutorial
- [Redis Streams Documentation](https://redis.io/docs/data-types/streams/)
- [Redis Consumer Groups](https://redis.io/docs/data-types/streams-tutorial/#consumer-groups)
