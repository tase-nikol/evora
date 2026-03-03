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

## Roadmap

### ✅ Completed (v0.1.0 - v0.2.0)

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
-   ✅ **Schema governance CLI**
    -   Schema extraction from Pydantic models
    -   Compatibility checking
    -   Breaking change detection
    -   Version enforcement

**Current Status:** 🎯 **Redis backend is production-ready**

### 🚧 In Progress (v0.3.0)
-   Rabbit broker implementation
-   OpenTelemetry tracing integration
-   Prometheus metrics export
-   Structured logging

### 📋 Planned (v0.4.0+)

-   Outbox pattern implementation
-   Transactional publishing
-   Admin CLI tools
-   Message replay utilities
-   Kafka broker implementation
-   Performance benchmarking suite
-   Example microservice architecture
-   Chaos engineering scenarios
-   Enhanced documentation
