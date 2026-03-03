# Frequently Asked Questions (FAQ)

## General Questions

### What is Evora?

Evora is a production-grade event processing runtime for Python that **enforces reliability patterns at the framework level**. It makes it impossible to forget critical safeguards like idempotency, retry logic, and error handling.

### Why use Evora instead of building my own event system?

Building reliable event processing is harder than it looks. Common issues include:
- Duplicate message processing (missing idempotency)
- Lost messages on crashes (no durable retry)
- Stuck messages blocking processing (no poison detection)
- Inconsistent error handling across services
- No visibility into failures

Evora solves all of these problems out of the box.

### How does Evora compare to Celery?

| Feature | Evora | Celery |
|---------|-------|--------|
| **Idempotency** | Built-in, mandatory | Optional, manual |
| **Error Types** | Explicit (Retryable/Fatal) | Try/catch only |
| **Poison Detection** | Automatic | Manual intervention |
| **Type Safety** | Pydantic-based | Limited |
| **Strict Mode** | Enforced best practices | Optional |
| **DLQ** | Structured metadata | Basic retry counting |

Evora is specifically designed for event-driven microservices with strict reliability requirements.

### Is Evora production-ready?

The **Redis Streams broker** is production-ready as of v0.2.0. It includes:
- ✅ Durable retry queues
- ✅ Poison message detection
- ✅ Consumer group coordination
- ✅ Idempotency guarantees
- ✅ Structured DLQ

OpenTelemetry integration and additional observability features are coming in v0.3.0.

## Architecture Questions

### What's the difference between "app-level retry" and "poison detection"?

**App-level retry** (durable retry):
- Handler explicitly raises `RetryableError`
- Message goes to ZSET delay queue
- Retries with exponential backoff
- Goes to DLQ after max attempts

**Poison detection** (broker-level):
- Handler crashes or hangs (no error raised)
- Message sits in PEL unacknowledged
- Background checker detects idle messages
- Reclaims or routes to DLQ

They're complementary safety nets!

### How does idempotency work?

1. Before executing handler, check Redis: `idempotency:{handler}:{event_id}`
2. If key exists → skip processing (already done)
3. If key doesn't exist → execute handler
4. On success → set key with TTL

This ensures each event is processed **exactly once** per handler, even if:
- Message is redelivered
- Consumer crashes and restarts
- Multiple consumers process same partition

### What happens when a consumer crashes?

1. **Messages in flight**: Sit in Redis PEL (Pending Entries List)
2. **Poison checker** (every 10s): Scans PEL for idle messages
3. **If idle > 60s**: Reclaims message to active consumer
4. **If delivered > 10 times**: Routes to DLQ

Result: **Zero message loss, automatic recovery.**

### Can I use Evora without Redis?

Yes! Evora has a broker abstraction:
- **MemoryBroker**: In-memory (for testing)
- **RedisStreamsBroker**: Production-ready
- **KafkaBroker**: Coming in v0.4.0

You can also implement `BaseBroker` for your own backend.

## Configuration Questions

### What's "strict mode"?

Strict mode (`strict=True`) enforces:
1. **Mandatory idempotency**: Every handler must have `IdempotencyPolicy`
2. **Explicit versions**: Every event must declare `__version__`
3. **Startup validation**: Errors fail fast at startup, not runtime

Recommended for production to prevent common mistakes.

### How do I tune retry behavior?

```python
broker = RedisStreamsBroker(
    base_delay_ms=1000,    # Start with 1s
    max_delay_ms=60_000,   # Cap at 60s
)

@subscribe(
    MyEvent,
    retry="exponential",
    max_attempts=5,        # Try 5 times total
    idempotency=...
)
```

Retry delays: 1s → 2s → 4s → 8s → 16s (capped at max_delay_ms)

### How do I tune poison detection?

```python
broker = RedisStreamsBroker(
    poison_idle_ms=30_000,         # Reclaim after 30s
    poison_max_deliveries=5,       # DLQ after 5 deliveries
    poison_check_interval_s=5.0,   # Check PEL every 5s
)
```

**Trade-offs**:
- Lower `poison_idle_ms` = faster recovery, more XCLAIM overhead
- Higher `poison_max_deliveries` = more tolerance, longer stuck time

### How long should idempotency TTL be?

Depends on your redelivery window:

- **Payment processing**: 7 days (financial regulation)
- **Email sending**: 24 hours (typical retry window)
- **Analytics events**: 1 hour (low risk)

Rule of thumb: **2x your maximum expected retry time**.

## Error Handling Questions

### When should I use RetryableError vs FatalError?

**RetryableError** - Transient failures (will recover):
- Network timeouts
- Database connection errors
- Rate limits from external APIs
- Temporary service unavailability

**FatalError** - Permanent failures (won't recover):
- Invalid data format
- Business rule violations
- Authentication failures
- Missing required resources

**ContractError** - Schema/protocol violations:
- Unexpected event version
- Missing required fields
- Type mismatches

### What if I don't want to retry at all?

```python
@subscribe(
    MyEvent,
    retry="exponential",
    max_attempts=1,  # Only try once
    dlq=True,
    idempotency=...
)
```

Or just raise `FatalError` to skip retries and go straight to DLQ.

### How do I replay messages from DLQ?

Currently manual (admin CLI coming in v0.4.0):

```python
# Read from DLQ
messages = await redis.xrange(f"{channel}.dlq", "-", "+")

# Fix the issue (deploy new code, fix data, etc.)

# Re-publish to main channel
for msg_id, msg_data in messages:
    await redis.xadd(channel, msg_data)
    await redis.xdel(f"{channel}.dlq", msg_id)
```

## Performance Questions

### What's the throughput?

**Single consumer**: 5,000-10,000 messages/second (handler-dependent)

**Bottleneck**: Your handler execution time, not Evora.

**Scaling**: Add more consumers (linear scaling with Redis Streams consumer groups)

### What's the latency?

**Normal path**: 1-5ms (XREADGROUP → handler → XACK)

**Retry path**: Configured delay (500ms, 1s, 2s, 4s, ...)

**Poison detection**: Up to `poison_check_interval_s` + `poison_idle_ms`

### How much Redis memory is needed?

- **Pending messages**: ~1KB per message
- **Retry queue**: ~100 bytes per scheduled retry
- **Idempotency keys**: ~50 bytes per processed event (TTL-based cleanup)

Example: 10,000 pending messages = ~10MB RAM

### Can I use Redis Cluster?

Yes, with caveats:
- Streams must be on same slot (use hash tags: `{service}:channel`)
- Idempotency store keys should use same hash tag
- Retry ZSETs should use same hash tag

Coming in v0.3.0: Built-in Redis Cluster support with proper hash tag handling.

## Schema Questions

### How do I evolve event schemas?

Use the schema governance CLI:

```bash
# Check compatibility
evora schema check events_v1.py:OrderEvent events_v2.py:OrderEvent
```

**Safe changes** (backward-compatible):
- Adding optional fields
- Adding new enum values
- Widening types (int → float)

**Breaking changes** (require version bump):
- Removing fields
- Making optional fields required
- Changing field types
- Renaming fields

### Do I need to bump __version__ for every change?

Only for **breaking changes**:
- Changes that would break existing consumers
- Schema incompatibilities

**Don't bump** for:
- Adding optional fields (backward-compatible)
- Internal implementation changes
- Documentation updates

### Can multiple versions coexist?

Yes! Pattern:

```python
class OrderEventV1(Event):
    __version__ = 1
    # ...

class OrderEventV2(Event):
    __version__ = 2
    # ...

# Register both
app.registry.register(OrderEventV1)
app.registry.register(OrderEventV2)

# Consumers can handle either
@subscribe(OrderEventV1, ...)
async def handle_v1(event, ctx): ...

@subscribe(OrderEventV2, ...)
async def handle_v2(event, ctx): ...
```

## Deployment Questions

### How do I deploy multiple consumers?

Just run multiple instances:

```bash
# Server 1
python app.py

# Server 2
python app.py

# Server 3
python app.py
```

Redis consumer groups handle coordination automatically. Each consumer gets a unique `consumer_name` (hostname-PID by default).

### How do I do zero-downtime deploys?

1. Deploy new version alongside old
2. New consumers join consumer group
3. Stop old consumers gracefully
4. Old consumers finish in-flight messages
5. New consumers take over

PEL ensures no message loss during transition.

### How do I monitor Evora in production?

Currently (v0.2.0):
- Redis CLI: Monitor streams, PEL, retry queues
- Application logs: Handler execution
- Redis monitoring tools: Memory, throughput

Coming in v0.3.0:
- OpenTelemetry tracing
- Prometheus metrics
- Structured logging

### What's the recommended consumer-to-Redis ratio?

**General rule**: 1 Redis instance can handle 10-50 consumers depending on:
- Message throughput
- Message size
- Handler execution time
- Redis hardware

Use Redis monitoring to watch for:
- CPU saturation
- Memory pressure
- Network bandwidth

For high scale (>50 consumers), consider Redis Cluster.

## Troubleshooting

### Messages aren't being consumed

**Check**:
1. Is Redis running? `redis-cli ping`
2. Is consumer running? Check logs
3. Is consumer group created? `redis-cli XINFO GROUPS <channel>`
4. Are messages in stream? `redis-cli XLEN <channel>`

### Messages are stuck in PEL

**Poison checker will handle this automatically** (every 10s).

**Manual check**:
```bash
redis-cli XPENDING <channel> <group> - + 10
```

### Idempotency not working

**Check**:
1. Same `event.id` being reused?
2. TTL expired? (Check `ttl_seconds`)
3. Redis connection working?
4. Handler name consistent?

### High Redis memory usage

**Causes**:
- Many pending messages (normal)
- Long idempotency TTL (adjust TTL)
- DLQ buildup (need to process or archive)

**Solutions**:
- Process DLQ messages
- Lower idempotency TTL if acceptable
- Archive old streams: `XTRIM <channel> MAXLEN ~ 1000000`

## Contributing

### How can I contribute?

See [CONTRIBUTING.md](CONTRIBUTING.md)!

Areas we need help:
- Documentation improvements
- Bug reports
- Feature requests
- Code contributions
- Example applications

### Is there a roadmap?

Yes! See [ARCHITECTURE.md](docs/ARCHITECTURE.md#roadmap) and [CHANGELOG.md](CHANGELOG.md).

---

**Didn't find your answer?** [Open an issue](https://github.com/tase-nikol/evora/issues) with the "question" label!
