### Packages

-   `eventsdk.core`

    -   `Event` base class (Pydantic)

    -   envelope types

    -   registry (type → model)

    -   serializer/deserializer

    -   schema/version manager

-   `eventsdk.app`

    -   `subscribe()` decorator

    -   handler registry

    -   runner / lifecycle

    -   middleware pipeline

-   `eventsdk.brokers`

    -   `BaseBroker` interface

    -   `kafka`, `rabbitmq`, `redisstreams` implementations

-   `eventsdk.runtime`

    -   retries, backoff, DLQ routing

    -   idempotency store interface

    -   outbox publisher

    -   concurrency control

-   `eventsdk.observability`

    -   OpenTelemetry hooks, metrics, logs

-   `eventsdk.contrib`

    -   postgres outbox, redis idempotency, etc.

Key principle: **Brokers only move bytes.** Core handles *events*.

Then the SDK pipeline does:

**consume → deserialize → validate → idempotency → handler → ack/nack → retry/DLQ**

Then the SDK pipeline does:

**consume → deserialize → validate → idempotency → handler → ack/nack → retry/DLQ**

****
## Roadmap

### ✅ Completed (Weeks 1-4)

-   ✅ Reliability enforcement refactor
-   ✅ Error classification (Retryable/Fatal/Contract)
-   ✅ Idempotency mandatory in strict mode
-   ✅ Telemetry hooks
-   ✅ Redis Streams backend with consumer groups
-   ✅ Redis idempotency store
-   ✅ Durable retry (ZSET delay queue)
-   ✅ DLQ routing with metadata
-   ✅ **Poison message detection & handling**
    -   XPENDING scanning
    -   XCLAIM reclaim logic
    -   Delivery count enforcement
    -   Background PEL monitoring

**Current Status:** 🎯 **Redis backend is production-ready**

### 🚧 In Progress (Weeks 5-6)

-   Schema governance CLI
-   Compatibility checks
-   Breaking change detection

### 📋 Planned (Weeks 7-8)

-   Outbox implementation
-   Transactional publishing

### 🔮 Future (Weeks 9-12)

-   OpenTelemetry integration (tracing, metrics)
-   Structured logging
-   Admin CLI tools
-   Example microservice
-   Chaos test scenarios
-   Documentation finalization
-   OSS launch
