# Evora Visual Architecture Guide

This document provides visual diagrams to understand Evora's architecture and message flows.

---

## 📐 System Architecture Overview

```
┌────────────────────────────────────────────────────────────────────┐
│                         EVORA RUNTIME                               │
├────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐ │
│  │                   APPLICATION LAYER                           │ │
│  │                   (evora/app.py)                             │ │
│  │                                                               │ │
│  │  • Event decoding/validation (CloudEvents envelope)          │ │
│  │  • Handler registry & dispatch                               │ │
│  │  • Error classification (Retryable/Fatal/Contract)           │ │
│  │  • Retry policy enforcement                                  │ │
│  │  • Idempotency gate                                          │ │
│  │  • DLQ routing                                               │ │
│  │  • Telemetry hooks                                           │ │
│  │                                                               │ │
│  └───────────────────────┬──────────────────────────────────────┘ │
│                          │                                         │
│                          ▼                                         │
│  ┌──────────────────────────────────────────────────────────────┐ │
│  │                    BROKER LAYER                               │ │
│  │                (evora/brokers/redis_streams.py)              │ │
│  │                                                               │ │
│  │  Publishing:                                                  │ │
│  │    • XADD (append to stream)                                 │ │
│  │                                                               │ │
│  │  Consumption:                                                 │ │
│  │    • XREADGROUP (consumer group coordination)                │ │
│  │    • XACK on success                                         │ │
│  │                                                               │ │
│  │  Durable Retry:                                              │ │
│  │    • ZADD (schedule retry in delay queue)                    │ │
│  │    • Background scheduler (ZRANGEBYSCORE → XADD)             │ │
│  │                                                               │ │
│  │  Poison Detection:                                           │ │
│  │    • XPENDING (scan for idle messages)                       │ │
│  │    • XCLAIM (reclaim stuck messages)                         │ │
│  │    • DLQ routing (exceeded max deliveries)                   │ │
│  │                                                               │ │
│  └───────────────────────┬──────────────────────────────────────┘ │
│                          │                                         │
│                          ▼                                         │
│  ┌──────────────────────────────────────────────────────────────┐ │
│  │                   STORAGE LAYER                               │ │
│  │                      (Redis)                                  │ │
│  │                                                               │ │
│  │  • Streams: Event log (XADD/XREADGROUP)                      │ │
│  │  • ZSET: Retry delay queue (sorted by due time)              │ │
│  │  • SET: Idempotency keys (event_id → processed)              │ │
│  │  • PEL: Pending entries (Redis Streams native)               │ │
│  │  • DLQ: Failed messages (separate stream)                    │ │
│  │                                                               │ │
│  └──────────────────────────────────────────────────────────────┘ │
│                                                                     │
└────────────────────────────────────────────────────────────────────┘
```

---

## 🔄 Complete Message Lifecycle

```
┌─────────────┐
│  Publisher  │
└──────┬──────┘
       │ publish(event)
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│  XADD users.events                                           │
│  {                                                           │
│    "v": "<event json>",                                      │
│    "k": "user-123",                                          │
│    "h": "{}",                                                │
│    "a": "1"  ← attempt counter                               │
│  }                                                           │
└──────────┬───────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────────┐
│  Consumer: XREADGROUP                                        │
│  (group_id="email-service", consumer="worker-1")             │
└──────────┬───────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────────┐
│  App Layer: Decode Envelope                                  │
│  • Parse CloudEvents envelope                                │
│  • Validate schema (Pydantic)                                │
│  • Extract event ID                                          │
└──────────┬───────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────────┐
│  Idempotency Gate                                            │
│  • Check: evora:idempotency:{handler}:{event_id}             │
│  • If seen → Skip (already processed)                        │
│  • If new → Continue                                         │
└──────────┬───────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────────┐
│  Handler Execution                                           │
│  await send_welcome_email(event, ctx)                        │
└──────────┬───────────────────────────────────────────────────┘
           │
     ┌─────┴─────┐
     │           │
     ▼           ▼
┌─────────┐  ┌──────────────────────────────────────────┐
│ SUCCESS │  │  ERROR                                    │
└────┬────┘  └──────┬───────────────────────────────────┘
     │              │
     │              ▼
     │         ┌─────────────────────────────────────┐
     │         │  Classify Error:                    │
     │         │  • RetryableError                   │
     │         │  • FatalError                       │
     │         │  • ContractError                    │
     │         │  • Unknown (treat as retryable)     │
     │         └──────┬──────────────────────────────┘
     │                │
     │         ┌──────┴──────┐
     │         │             │
     │         ▼             ▼
     │    ┌────────────┐  ┌──────────────┐
     │    │ Retryable  │  │ Fatal/       │
     │    │ + attempts │  │ Contract     │
     │    │ < max      │  │              │
     │    └─────┬──────┘  └──────┬───────┘
     │          │                │
     │          ▼                ▼
     │    ┌─────────────────────────────────────┐
     │    │  schedule_retry():                  │
     │    │  • Compute backoff (exponential)    │
     │    │  • ZADD users.events.retry.z {      │
     │    │      score: now + delay,            │
     │    │      member: {event, attempt+1}     │
     │    │    }                                 │
     │    │  • XACK (clear from PEL)            │
     │    └─────────────────────────────────────┘
     │                                          │
     │                                          ▼
     │                                    ┌──────────────────┐
     │                                    │  DLQ Routing:    │
     │                                    │  • XADD          │
     │                                    │    users.events  │
     │                                    │    .dlq          │
     │                                    │  • XACK          │
     │                                    └──────────────────┘
     │
     ▼
┌──────────────────────────────────────────────────────────────┐
│  Mark Seen (Idempotency):                                    │
│  • SET evora:idempotency:{handler}:{event_id} "processed"    │
│  • EXPIRE 86400  (24 hours)                                  │
└──────────┬───────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────────┐
│  XACK users.events {group} {msg_id}                          │
│  (Remove from PEL - processing complete)                     │
└──────────────────────────────────────────────────────────────┘
```

---

## 🔄 Retry Scheduler Flow

```
Background Task (runs every 500ms):

┌──────────────────────────────────────────────────────────┐
│  Retry Scheduler                                         │
│                                                          │
│  1. ZRANGEBYSCORE users.events.retry.z                  │
│     min="-inf" max="{current_timestamp}"                │
│     → Returns due retry entries                         │
│                                                          │
│  2. For each due entry:                                 │
│     {                                                    │
│       "channel": "users.events",                        │
│       "v": "<event json>",                              │
│       "k": "user-123",                                  │
│       "h": {},                                          │
│       "a": 2  ← incremented attempt                     │
│     }                                                    │
│                                                          │
│  3. XADD users.events (republish)                       │
│     {                                                    │
│       "v": "<event json>",                              │
│       "k": "user-123",                                  │
│       "h": "{}",                                        │
│       "a": "2"  ← now attempt 2                         │
│     }                                                    │
│                                                          │
│  4. ZREM users.events.retry.z {entry}                   │
│     (Remove from delay queue)                           │
│                                                          │
└──────────────────────────────────────────────────────────┘

Timeline:

t=0s    Handler fails (attempt 1)
        → ZADD retry.z (score: now + 500ms)
        
t=0.5s  Retry scheduler runs
        → XADD (republish as attempt 2)
        → ZREM (clear from delay queue)
        
t=0.5s  Consumer reads attempt 2
        Handler fails again
        → ZADD retry.z (score: now + 1000ms)
        
t=1.5s  Retry scheduler runs
        → XADD (republish as attempt 3)
        
...continues with exponential backoff...
```

---

## 💀 Poison Message Detection Flow

```
Background Task (runs every 10 seconds):

┌──────────────────────────────────────────────────────────────┐
│  Poison Checker                                              │
│                                                              │
│  1. XPENDING users.events email-service - + 100             │
│     → Returns pending entries with metadata:                │
│     [                                                        │
│       {                                                      │
│         "message_id": "1234567890-0",                       │
│         "consumer": "worker-1-crashed",                     │
│         "time_since_delivered": 65000,  ← 65 seconds!       │
│         "times_delivered": 3            ← delivered 3x      │
│       },                                                     │
│       ...                                                    │
│     ]                                                        │
│                                                              │
│  2. For each pending entry:                                 │
│                                                              │
│     if time_since_delivered < poison_idle_ms (60s):         │
│         continue  // Not idle long enough                   │
│                                                              │
│     if times_delivered >= poison_max_deliveries (10):       │
│         ┌─────────────────────────────────────────┐         │
│         │  POISON! Route to DLQ:                  │         │
│         │                                          │         │
│         │  XADD users.events.dlq {                │         │
│         │    "v": "<original event>",             │         │
│         │    "reason": "poison_message",          │         │
│         │    "delivery_count": "10",              │         │
│         │    "idle_ms": "65000",                  │         │
│         │    "original_channel": "users.events",  │         │
│         │    "original_msg_id": "1234567890-0"    │         │
│         │  }                                       │         │
│         │                                          │         │
│         │  XACK users.events email-service        │         │
│         │       1234567890-0                       │         │
│         │  (Clear from PEL)                        │         │
│         └─────────────────────────────────────────┘         │
│                                                              │
│     else:  // Idle but under delivery limit                 │
│         ┌─────────────────────────────────────────┐         │
│         │  RECLAIM for retry:                     │         │
│         │                                          │         │
│         │  XCLAIM users.events email-service      │         │
│         │         worker-2-active                  │         │
│         │         60000                            │         │
│         │         1234567890-0                     │         │
│         │                                          │         │
│         │  → Transfers ownership to active worker │         │
│         │  → Resets idle timer                    │         │
│         │  → Increments delivery count            │         │
│         │  → Message re-enters XREADGROUP loop    │         │
│         └─────────────────────────────────────────┘         │
│                                                              │
└──────────────────────────────────────────────────────────────┘

Scenario Timeline:

t=0s    Consumer processes message
        → Crashes before XACK
        → Message enters PEL

t=0-60s Poison checker runs every 10s
        → Sees message but idle < 60s
        → No action

t=65s   Poison checker runs
        → idle_ms = 65000 (> 60000)
        → delivery_count = 1 (< 10)
        → XCLAIM to active consumer

t=66s   New consumer gets message
        → Crashes again
        → Back to PEL

...repeat...

t=600s  Poison checker runs
        → delivery_count = 10 (>= 10)
        → POISON DETECTED!
        → Route to DLQ
        → XACK (clear PEL)
```

---

## 🏢 Multi-Consumer Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                      Redis Streams                           │
│                   Stream: users.events                       │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  1234567890-0  {"v": "...", "k": "user-1", ...}        │ │
│  │  1234567890-1  {"v": "...", "k": "user-2", ...}        │ │
│  │  1234567890-2  {"v": "...", "k": "user-3", ...}        │ │
│  └────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
                           │
       ┌───────────────────┼───────────────────┐
       │                   │                   │
       ▼                   ▼                   ▼
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│ Consumer A  │    │ Consumer B  │    │ Consumer C  │
│ (worker-1)  │    │ (worker-2)  │    │ (worker-3)  │
└─────────────┘    └─────────────┘    └─────────────┘
       │                   │                   │
       │  All in same consumer group:          │
       │  "email-service"                      │
       │                                       │
       │  Load balancing:                      │
       │  • Redis assigns messages to          │
       │    different consumers                │
       │  • Each message delivered to          │
       │    ONE consumer                       │
       │  • PEL tracks per-consumer            │
       │                                       │
       └───────────────────┬───────────────────┘
                           │
                           ▼
              ┌────────────────────────┐
              │  If Consumer B crashes:│
              │  • Messages in its PEL │
              │  • Poison checker      │
              │    detects idle        │
              │  • XCLAIM to A or C    │
              └────────────────────────┘


Multiple Services (Different Consumer Groups):

┌──────────────────────────────────────────────────────────────┐
│                      Redis Streams                           │
│                   Stream: users.events                       │
└──────────────────────────────────────────────────────────────┘
                           │
       ┌───────────────────┼───────────────────┐
       │                   │                   │
       ▼                   ▼                   ▼
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│ Email       │    │ Analytics   │    │ Billing     │
│ Service     │    │ Service     │    │ Service     │
│             │    │             │    │             │
│ group_id:   │    │ group_id:   │    │ group_id:   │
│ "email-svc" │    │ "analytics" │    │ "billing"   │
└─────────────┘    └─────────────┘    └─────────────┘

Each service:
• Receives ALL messages (fan-out)
• Independent PEL
• Independent consumer group offset
• Independent poison detection
```

---

## 📦 Redis Data Structures

```
Redis Key Space:

┌────────────────────────────────────────────────────────────┐
│  Streams (Event Log)                                       │
├────────────────────────────────────────────────────────────┤
│  users.events                                              │
│    • Main event stream                                     │
│    • Type: Stream                                          │
│    • Entries: XADD, read via XREADGROUP                    │
│                                                            │
│  users.events.dlq                                          │
│    • Dead letter queue                                     │
│    • Type: Stream                                          │
│    • Contains failed messages with metadata                │
└────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────┐
│  ZSET (Retry Delay Queue)                                  │
├────────────────────────────────────────────────────────────┤
│  users.events.retry.z                                      │
│    • Sorted by due timestamp                               │
│    • Member: JSON {event, attempt, error}                  │
│    • Score: Unix timestamp (ms) when due                   │
│                                                            │
│  Example:                                                  │
│    Score: 1709287850500  (due time)                        │
│    Member: {                                               │
│      "channel": "users.events",                            │
│      "v": "<event json>",                                  │
│      "a": 2,                                               │
│      "err": {"type": "TimeoutError", ...}                  │
│    }                                                       │
└────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────┐
│  SET (Idempotency Keys)                                    │
├────────────────────────────────────────────────────────────┤
│  evora:idempotency:{handler}:{event_id}                    │
│    • Type: String                                          │
│    • Value: "processed" or timestamp                       │
│    • TTL: 86400 seconds (24h)                              │
│                                                            │
│  Example:                                                  │
│    Key: evora:idempotency:send_email:evt_123               │
│    Value: "processed"                                      │
│    TTL: 82000 seconds remaining                            │
└────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────┐
│  PEL (Pending Entries List) - Native Redis Streams         │
├────────────────────────────────────────────────────────────┤
│  Automatically maintained by Redis                         │
│  • Tracks unacknowledged messages per consumer group       │
│  • Includes:                                               │
│    - message_id                                            │
│    - consumer name                                         │
│    - idle time (ms since delivered)                        │
│    - delivery count                                        │
│  • Accessed via: XPENDING                                  │
│  • Cleared via: XACK                                       │
└────────────────────────────────────────────────────────────┘
```

---

## 🎯 Error Classification Decision Tree

```
Handler throws exception
         │
         ▼
    ┌─────────────────┐
    │ Error Type?     │
    └────────┬────────┘
             │
    ┌────────┴────────────────────┐
    │                             │
    ▼                             ▼
┌─────────────────┐      ┌──────────────────┐
│ RetryableError  │      │  FatalError or   │
│                 │      │  ContractError   │
└────────┬────────┘      └────────┬─────────┘
         │                        │
         ▼                        │
    ┌─────────────┐               │
    │ attempt <   │               │
    │ max_attempts?│              │
    └────────┬────┘               │
             │                    │
       ┌─────┴─────┐              │
       │           │              │
       ▼           ▼              │
    ┌────┐      ┌────┐            │
    │YES │      │ NO │            │
    └─┬──┘      └─┬──┘            │
      │           │               │
      ▼           │               │
┌──────────────┐  │               │
│schedule_retry│  │               │
│• ZADD retry.z│  │               │
│• XACK        │  │               │
└──────────────┘  │               │
                  │               │
                  └───────┬───────┘
                          │
                          ▼
                   ┌─────────────┐
                   │ DLQ Routing │
                   │• XADD .dlq  │
                   │• XACK       │
                   └─────────────┘

Unknown errors: Treated as Retryable
```

---

## 🔄 Idempotency Flow

```
Message arrives
     │
     ▼
┌─────────────────────────────────────────┐
│ Check idempotency:                      │
│ GET evora:idempotency:{handler}:{id}    │
└───────────┬─────────────────────────────┘
            │
    ┌───────┴────────┐
    │                │
    ▼                ▼
┌────────┐      ┌────────┐
│ EXISTS │      │  NULL  │
└───┬────┘      └───┬────┘
    │               │
    ▼               ▼
┌────────────┐  ┌─────────────────┐
│ Skip       │  │ Execute handler │
│ (already   │  └────────┬────────┘
│ processed) │           │
└────────────┘           ▼
                  ┌──────────────────────────┐
                  │ On success:              │
                  │ SET evora:idempotency:   │
                  │     {handler}:{id}       │
                  │     "processed"          │
                  │ EXPIRE 86400             │
                  └──────────────────────────┘

Scenario: Duplicate Delivery

t=0s    Message 1 arrives (id: evt_123)
        → Check: GET evora:idempotency:handler:evt_123
        → Result: NULL
        → Execute handler
        → Success: SET evora:idempotency:handler:evt_123 "processed"

t=5s    Message 1 arrives AGAIN (redelivery)
        → Check: GET evora:idempotency:handler:evt_123
        → Result: "processed"
        → Skip handler
        → XACK immediately

Result: Handler executed exactly once
```

---

## 📊 Capacity Planning

```
Throughput Calculation:

Messages per second = (Consumers × Handler_rate)

Example:
  • 3 consumers
  • Each handles 100 msg/sec
  • Total: 300 msg/sec

Redis Capacity:

Memory per pending message:
  • Stream entry: ~500 bytes
  • PEL entry: ~200 bytes
  • Idempotency key: ~100 bytes
  • Total: ~800 bytes

For 1 million pending messages:
  800 bytes × 1M = 800 MB

Retry queue:
  • ZSET member: ~1 KB per entry
  • 10,000 retries = 10 MB

DLQ:
  • Same as stream entries
  • Grows with failures
```

---

This visual guide should help you understand how Evora works at a glance!

For more details:
- `README.md` - Feature overview
- `docs/ARCHITECTURE.md` - Technical deep dive
- `docs/POISON_MESSAGE_HANDLING.md` - Poison detection details
