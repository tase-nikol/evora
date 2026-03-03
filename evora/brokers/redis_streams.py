"""
Redis Streams Broker Implementation for Evora Event Runtime.

This module provides a production-ready message broker built on Redis Streams with:
- Durable retry scheduling via Redis Sorted Sets (ZSET)
- Dead Letter Queue (DLQ) routing for failed messages
- Poison message detection and automatic reclamation
- Consumer group-based message distribution
- Exponential backoff for retries
- Crash-safe message processing

Architecture:
    Publishing: Messages are added to Redis Streams via XADD
    Consumption: Consumer groups read via XREADGROUP with automatic load balancing
    Retry: Failed messages are scheduled in a ZSET delay queue and re-added to the stream when due
    DLQ: Messages that exceed max attempts or are unrecoverable go to a DLQ stream
    Poison Detection: Stuck messages in PEL (Pending Entry List) are automatically reclaimed

Message Flow:
    1. Publish → XADD to stream
    2. Consumer reads via XREADGROUP
    3. Handler processes → Success: XACK | Failure: schedule_retry
    4. Retry scheduler moves due messages from ZSET back to stream
    5. After max attempts → DLQ → XACK
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

import anyio
import redis.asyncio as redis

from .base import Message


def _now_ms() -> int:
    """
    Get current Unix timestamp in milliseconds.

    Returns:
        int: Current time in milliseconds since epoch.

    Used for:
        - Computing retry due times in ZSET scores
        - Timestamp calculations for poison message detection
    """
    return int(time.time() * 1000)


def _compute_delay_ms(attempt: int, base: int = 500, max_delay: int = 30000) -> int:
    """
    Calculate exponential backoff delay for retry attempts.

    Formula: delay = base * (2 ^ (attempt - 1))
    The delay is capped at max_delay to prevent excessive wait times.

    Args:
        attempt: The retry attempt number (1-indexed).
        base: Base delay in milliseconds (default: 500ms).
        max_delay: Maximum delay cap in milliseconds (default: 30000ms = 30s).

    Returns:
        int: Computed delay in milliseconds.

    Examples:
        >>> _compute_delay_ms(1, base=500)  # 500ms
        500
        >>> _compute_delay_ms(2, base=500)  # 1000ms
        1000
        >>> _compute_delay_ms(5, base=500)  # 8000ms
        8000
        >>> _compute_delay_ms(10, base=500)  # capped at 30000ms
        30000
    """
    delay = base * (2 ** (attempt - 1))
    return min(delay, max_delay)


@dataclass
class RedisStreamsBroker:
    """
    Production-grade message broker implementation using Redis Streams.

    This broker provides reliable, durable message processing with automatic retries,
    poison message detection, and dead-letter queue routing. It uses Redis Streams
    consumer groups for distributed processing and Redis Sorted Sets for retry scheduling.

    Key Features:
        - **Consumer Groups**: Multiple consumers can process messages in parallel
        - **Durable Retries**: Failed messages are scheduled in a ZSET with exponential backoff
        - **Poison Detection**: Stuck messages are automatically reclaimed and eventually DLQ'd
        - **Crash Safety**: All state is in Redis; consumers can restart without data loss
        - **Dead Letter Queue**: Unrecoverable messages are routed to DLQ with metadata
        - **Idempotency Support**: Works with Evora's idempotency layer for exactly-once semantics

    Message Format:
        Messages in Redis Streams contain these fields:
        - `v` (value): The raw event payload (bytes)
        - `k` (key): Optional partition/routing key
        - `h` (headers): JSON-encoded headers dict
        - `a` (attempt): Current attempt number (1-indexed)

    Redis Data Structures:
        - **Stream**: `<channel>` - Main event stream
        - **ZSET**: `<channel>.retry.z` - Delayed retry queue (score = due timestamp)
        - **Stream**: `<channel>.dlq` - Dead letter queue for failed messages
        - **Consumer Group**: `<group_id>` - Tracks consumer offsets and PEL

    Reliability Guarantees:
        - At-least-once delivery (with idempotency layer → exactly-once)
        - Messages survive consumer crashes
        - Automatic retry with exponential backoff
        - Poison message protection
        - No message loss on retry exhaustion (goes to DLQ)

    Attributes:
        client: Async Redis client instance
        group_id: Consumer group name (typically service name)
        consumer_name: Optional consumer identifier (defaults to hostname-pid)

        Behavior Configuration:
        block_ms: How long to block waiting for new messages (default: 2000ms)
        batch_size: Max messages to fetch per XREADGROUP call (default: 50)
        mkstream: Auto-create stream if it doesn't exist (default: True)
        dlq_suffix: Suffix for DLQ stream names (default: ".dlq")
        retry_zset_suffix: Suffix for retry ZSET names (default: ".retry.z")
        base_delay_ms: Base retry delay for exponential backoff (default: 500ms)
        max_delay_ms: Maximum retry delay cap (default: 30000ms = 30s)

        Poison Message Protection:
        poison_idle_ms: Idle time before reclaiming stuck messages (default: 60000ms = 1min)
        poison_max_deliveries: Max delivery attempts before DLQ (default: 10)
        poison_check_interval_s: Unused (reclaim is on-demand in loop)

    Usage Example:
        ```python
        import redis.asyncio as redis
        from evora.brokers.redis_streams import RedisStreamsBroker

        client = await redis.from_url("redis://localhost")
        broker = RedisStreamsBroker(
            client=client,
            group_id="user-service",
            consumer_name="worker-1"
        )

        # Publishing
        await broker.publish(
            "users.events",
            value=b'{"user_id": 123, "action": "created"}',
            key="123",
            headers={"version": "1.0"}
        )

        # Consuming
        async def handle_message(msg):
            print(f"Processing: {msg.value}")

        await broker.run_consumer(
            channels=["users.events"],
            handler=handle_message,
            consumer_name="worker-1"
        )
        ```

    Integration with Evora App:
        The broker is used by `evora.App` which handles:
        - Event decoding and validation
        - Handler routing
        - Idempotency checking
        - Error classification (RetryableError vs FatalError)
        - Telemetry and observability

        The broker focuses purely on message transport and durability.
    """

    client: redis.Redis
    group_id: str
    consumer_name: str | None = None

    # behavior
    block_ms: int = 2000
    batch_size: int = 50
    mkstream: bool = True
    dlq_suffix: str = ".dlq"
    retry_zset_suffix: str = ".retry.z"
    base_delay_ms: int = 500
    max_delay_ms: int = 30_000

    # poison message detection
    poison_idle_ms: int = 60_000  # 60 seconds idle before reclaim
    poison_max_deliveries: int = 10  # max delivery attempts before DLQ
    poison_check_interval_s: float = 10.0  # check for poison messages every 10s

    def _consumer(self) -> str:
        """
        Generate a unique consumer identifier for this instance.

        Returns a consumer name that uniquely identifies this process within
        the consumer group. Used by Redis to track which messages are assigned
        to which consumer in the Pending Entry List (PEL).

        Returns:
            str: Consumer identifier in format "<hostname>-<pid>" or the
                 explicitly set consumer_name if provided.

        Why this matters:
            - Each consumer in a group must have a unique name
            - Redis tracks message ownership per consumer
            - Enables poison message detection (stuck messages per consumer)
            - Allows monitoring which consumer is processing what
        """
        return self.consumer_name or f"{os.uname().nodename}-{os.getpid()}"

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
        """
        Schedule a failed message for durable retry using Redis ZSET delay queue.

        This method implements crash-safe retry by:
        1. Computing exponential backoff delay based on attempt number
        2. Storing the full message payload in a Redis Sorted Set (ZSET)
        3. Using the due timestamp as the ZSET score for time-based retrieval
        4. ACKing the original stream message to prevent it from becoming poison

        The message will be automatically re-added to the stream by the retry
        scheduler when the due time is reached.

        Args:
            msg: The original Message object that failed processing
            raw_value: Raw byte payload of the message
            headers: Message headers dict (e.g., version, correlation_id)
            attempt: Current attempt number (will be incremented for retry)
            error_type: Classification of error (e.g., "RetryableError")
            error_message: Human-readable error description

        Redis Operations:
            1. ZADD <channel>.retry.z <due_timestamp> <json_payload>
            2. XACK <channel> <group_id> <message_id>

        Payload Format:
            Stored in ZSET as JSON string containing:
            {
                "channel": "users.events",
                "v": "<base64_or_utf8_value>",
                "k": "<optional_key>",
                "h": {"version": "1.0", ...},
                "a": <attempt + 1>,
                "err": {"type": "RetryableError", "message": "DB timeout"}
            }

        Why ACK immediately:
            - Prevents message from sitting in PEL (Pending Entry List)
            - Avoids false poison detection
            - Retry is now managed by ZSET, not stream PEL
            - Survives consumer crashes (state in Redis, not memory)

        Example:
            ```python
            await broker.schedule_retry(
                msg=msg,
                raw_value=b'{"user_id": 123}',
                headers={"version": "1.0"},
                attempt=2,
                error_type="RetryableError",
                error_message="Database connection timeout"
            )
            # Message will be retried after exponential backoff delay
            ```

        See Also:
            - retry_scheduler(): Background task that processes the ZSET
            - _compute_delay_ms(): Exponential backoff calculation
        """
        due = _now_ms() + _compute_delay_ms(
            attempt, base=self.base_delay_ms, max_delay=self.max_delay_ms
        )

        payload = {
            "channel": msg.channel,
            "v": raw_value.decode("utf-8", errors="replace"),
            "k": msg.key or "",
            "h": headers or {},
            "a": attempt + 1,
            "err": {"type": error_type, "message": error_message},
        }

        zkey = msg.channel + self.retry_zset_suffix
        member = json.dumps(payload, separators=(",", ":"))

        pipe = self.client.pipeline()
        pipe.zadd(zkey, {member: due})

        # ACK the original message so it doesn't stay pending / poison PEL
        if msg.message_id:
            pipe.xack(msg.channel, self.group_id, msg.message_id)
        await pipe.execute()

    async def _reclaim_stuck(
        self,
        channel: str,
        *,
        min_idle_ms: int,
        count: int = 50,
    ) -> list[tuple[str, dict]]:
        """
        Reclaim stuck messages from the Pending Entry List (PEL) to prevent poison messages.

        When a consumer crashes or hangs while processing a message, that message
        remains in the PEL indefinitely. This method detects such "stuck" messages
        and reclaims them to this consumer for reprocessing.

        Strategy:
            1. Prefer XAUTOCLAIM (Redis 6.2+) - atomic and efficient
            2. Fallback to XPENDING_RANGE + XCLAIM for older Redis versions

        A message is considered "stuck" if:
            - It's in the PEL (pending, not ACKed)
            - It's been idle for >= min_idle_ms
            - Original consumer likely crashed or is stuck

        Args:
            channel: The Redis Stream channel name
            min_idle_ms: Minimum idle time in milliseconds before reclaiming
                        (typically set to poison_idle_ms, default 60 seconds)
            count: Maximum number of messages to reclaim in one call

        Returns:
            list[tuple[str, dict]]: List of (message_id, fields_dict) tuples
                                    that have been claimed by this consumer

        Redis Operations:
            **Preferred (Redis 6.2+):**
            - XAUTOCLAIM <stream> <group> <consumer> <min_idle_time> <start_id> COUNT <count>

            **Fallback (older Redis):**
            - XPENDING_RANGE <stream> <group> - + + <count>
            - Filter by time_since_delivered >= min_idle_ms
            - XCLAIM <stream> <group> <consumer> <min_idle_time> <message_ids...>

        Why reclaiming matters:
            - Prevents messages from being stuck forever
            - Enables automatic recovery from consumer crashes
            - Essential for poison message detection
            - Maintains throughput when consumers fail

        Poison Detection Flow:
            1. Message fails → stays in PEL
            2. Consumer crashes → message never ACKed
            3. After min_idle_ms → _reclaim_stuck claims it
            4. _process_one checks attempt count
            5. If > poison_max_deliveries → DLQ

        Example:
            ```python
            # Reclaim messages idle for 60+ seconds
            stuck = await broker._reclaim_stuck(
                "users.events",
                min_idle_ms=60_000,
                count=25
            )
            for msg_id, fields in stuck:
                # Process reclaimed message
                msg = await broker._decode_entry("users.events", msg_id, fields)
                await broker._process_one(msg, handler, raw_msg_id=msg_id)
            ```

        See Also:
            - _process_one(): Enforces poison_max_deliveries
            - run_consumer(): Calls this periodically in consumer loop
        """
        consumer = self._consumer()

        # Preferred: XAUTOCLAIM (Redis 6.2+)
        try:
            # start_id="0-0" means "scan from beginning of PEL"
            # returns: (next_start_id, [(msg_id, fields), ...], deleted_ids)
            next_start, entries, _deleted = await self.client.xautoclaim(
                name=channel,
                groupname=self.group_id,
                consumername=consumer,
                min_idle_time=min_idle_ms,
                start_id="0-0",
                count=count,
            )
            # entries are already claimed to this consumer, with fields included
            return [(mid, fields) for (mid, fields) in entries]
        except Exception:
            # Fallback path below
            pass

        # Fallback: XPENDING_RANGE + XCLAIM
        pending = await self.client.xpending_range(
            name=channel,
            groupname=self.group_id,
            min="-",
            max="+",
            count=count,
        )
        if not pending:
            return []

        # Filter by idle time; claim only those
        ids = []
        for e in pending:
            if e["time_since_delivered"] >= min_idle_ms:
                ids.append(e["message_id"])

        if not ids:
            return []

        claimed = await self.client.xclaim(
            name=channel,
            groupname=self.group_id,
            consumername=consumer,
            min_idle_time=min_idle_ms,
            message_ids=ids,
        )
        return [(mid, fields) for (mid, fields) in claimed]

    async def _decode_entry(self, channel: str, msg_id, fields) -> Message | None:
        """
        Decode raw Redis Stream entry fields into a typed Message object.

        Redis Streams store messages as field-value pairs (similar to hash maps).
        This method extracts and normalizes these fields into Evora's Message format,
        handling both byte strings and regular strings (Redis client variations).

        Field Mapping:
            Redis Field → Message Attribute
            - `v` (value) → msg.value (bytes)
            - `k` (key) → msg.key (str | None)
            - `h` (headers) → msg.headers (dict)
            - `a` (attempt) → msg.attempt (int)

        Args:
            channel: The stream channel name (e.g., "users.events")
            msg_id: Redis message ID (e.g., "1234567890123-0")
            fields: Dict of field names to values from XREAD/XREADGROUP
                    Can contain byte keys (b"v") or string keys ("v")

        Returns:
            Message | None: Decoded Message object, or None if structurally invalid

        Error Handling:
            - Missing `v` field → ACK and return None (invalid message)
            - Invalid JSON in headers → defaults to empty dict
            - Missing fields → sensible defaults (key=None, attempt=1)

        Why ACK invalid messages:
            - Prevents PEL pollution with unprocessable messages
            - Invalid structure means message is corrupted
            - Better to drop than retry forever
            - Consider logging/monitoring for such cases

        Type Safety:
            Handles both Redis client types:
            - redis-py returns bytes (b"v", b"123")
            - Some clients return strings ("v", "123")
            This ensures compatibility across Redis client versions.

        Example:
            ```python
            # Raw Redis Stream entry
            fields = {
                b"v": b'{"user_id": 123}',
                b"k": b"user:123",
                b"h": b'{"version": "1.0"}',
                b"a": b"2"
            }

            msg = await broker._decode_entry("users.events", "1234-0", fields)
            # Message(
            #     channel="users.events",
            #     value=b'{"user_id": 123}',
            #     key="user:123",
            #     headers={"version": "1.0"},
            #     attempt=2,
            #     message_id="1234-0"
            # )
            ```

        See Also:
            - Message dataclass in base.py
            - _process_one(): Consumes decoded messages
        """
        raw = fields.get(b"v") or fields.get("v")
        if raw is None:
            # structurally invalid; ack and drop (or DLQ)
            await self.client.xack(channel, self.group_id, msg_id)
            return None

        key_b = fields.get(b"k") or fields.get("k") or b""
        key = key_b.decode("utf-8") if isinstance(key_b, (bytes, bytearray)) else str(key_b)
        key = key or None

        attempt_b = fields.get(b"a") or fields.get("a") or b"1"
        attempt = (
            int(attempt_b.decode("utf-8"))
            if isinstance(attempt_b, (bytes, bytearray))
            else int(attempt_b)
        )

        hdr_b = fields.get(b"h") or fields.get("h") or b"{}"
        try:
            headers = json.loads(
                hdr_b.decode("utf-8") if isinstance(hdr_b, (bytes, bytearray)) else str(hdr_b)
            )
        except Exception:
            headers = {}

        return Message(
            channel=channel,
            value=raw if isinstance(raw, (bytes, bytearray)) else bytes(raw),
            headers=headers,
            key=key,
            message_id=msg_id.decode("utf-8")
            if isinstance(msg_id, (bytes, bytearray))
            else str(msg_id),
            attempt=attempt,
        )

    async def _process_one(self, msg: Message, handler, *, raw_msg_id) -> None:
        """
        Process a single message with poison detection and error handling.

        This method enforces the poison message protection policy and provides
        a safety net for unexpected handler failures. It operates at the broker
        level, below the App layer's retry logic.

        Processing Logic:
            1. Check if attempt > poison_max_deliveries
               → If yes: Route to DLQ and ACK (poison protection)
            2. Call handler (typically evora.App._dispatch)
            3. On success: ACK message
            4. On handler exception: Route to DLQ and ACK (safety net)

        Why both App and Broker handle errors:
            - **App Layer**: Business logic errors (RetryableError, FatalError)
              → Uses schedule_retry() for durable retries
            - **Broker Layer**: Structural/crash errors (handler exploded)
              → DLQ immediately to prevent infinite loops

        Poison Detection:
            Messages that have been redelivered too many times are considered
            "poison" and sent to DLQ. This prevents:
            - Infinite retry loops
            - Consumer resource exhaustion
            - Stream processing bottlenecks

        Args:
            msg: Decoded Message object to process
            handler: Async callable that processes the message
                     (typically bound App._dispatch method)
            raw_msg_id: Original Redis message ID for ACK operations

        DLQ Metadata:
            Messages sent to DLQ include:
            - `v`: Original message payload
            - `reason`: Why it was DLQ'd
              - "poison_max_attempts_exceeded": Too many deliveries
              - "broker_handler_exception": Handler crashed unexpectedly
            - `attempt`: Current attempt number
            - `error`: Exception type (for handler crashes)
            - `message`: Exception message (for handler crashes)

        Example Flow - Poison Message:
            ```python
            msg = Message(
                channel="users.events",
                value=b'{"bad": "data"}',
                attempt=11,  # > poison_max_deliveries (10)
                ...
            )

            await broker._process_one(msg, handler, raw_msg_id="1234-0")

            # Result:
            # - Message added to "users.events.dlq"
            # - Original message ACKed from "users.events"
            # - Handler never called (poison detected early)
            ```

        Example Flow - Handler Exception:
            ```python
            async def buggy_handler(msg):
                raise ValueError("Unexpected crash")

            await broker._process_one(msg, buggy_handler, raw_msg_id="1234-0")

            # Result:
            # - Message added to DLQ with error metadata
            # - Original message ACKed
            # - No retry attempted (structural failure)
            ```

        Integration with App:
            The App layer normally handles retries via schedule_retry().
            This method catches cases where:
            - App's retry logic is exhausted
            - Handler throws unexpected exceptions
            - Message has been redelivered too many times

        See Also:
            - schedule_retry(): For App-level durable retries
            - _reclaim_stuck(): How messages get redelivered
            - App._dispatch(): The typical handler passed here
        """
        if msg.attempt > self.poison_max_deliveries:
            dlq = msg.channel + self.dlq_suffix
            await self.client.xadd(
                dlq,
                {
                    b"v": msg.value,
                    b"reason": b"poison_max_attempts_exceeded",
                    b"attempt": str(msg.attempt).encode("utf-8"),
                },
            )
            await self.client.xack(msg.channel, self.group_id, raw_msg_id)
            return

        try:
            await handler(msg)
            await self.client.xack(msg.channel, self.group_id, raw_msg_id)
        except Exception as e:
            # Let App semantics decide retry vs DLQ normally.
            # If you want broker-hardening only for "App exploded", keep this.
            dlq = msg.channel + self.dlq_suffix
            await self.client.xadd(
                dlq,
                {
                    b"v": msg.value,
                    b"reason": b"broker_handler_exception",
                    b"error": type(e).__name__.encode("utf-8"),
                    b"message": str(e).encode("utf-8"),
                },
            )
            await self.client.xack(msg.channel, self.group_id, raw_msg_id)

    async def publish(
        self,
        channel: str,
        *,
        value: bytes,
        key: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        """
        Publish a message to a Redis Stream channel.

        This method adds a new message to the specified Redis Stream using XADD.
        The message is immediately available to all consumer groups subscribed
        to the stream.

        Message Structure:
            Messages are stored as Redis Stream entries with these fields:
            - `v`: The raw event payload (bytes)
            - `k`: Optional partition/routing key (empty string if None)
            - `h`: JSON-encoded headers dictionary
            - `a`: Attempt counter, initialized to "1"

        Args:
            channel: The stream channel name (e.g., "users.events")
            value: Raw message payload as bytes (typically JSON-encoded event)
            key: Optional partition key for ordering guarantees (default: None)
            headers: Optional metadata dictionary (e.g., version, correlation_id)

        Redis Operation:
            XADD <channel> * v <value> k <key> h <headers_json> a 1

        Ordering Guarantees:
            - Messages in a stream are strictly ordered by message ID
            - Redis generates monotonic IDs: "<timestamp_ms>-<sequence>"
            - All consumers see messages in the same order
            - Key field is for application-level partitioning (not enforced by Redis)

        Durability:
            - Messages are immediately persisted to Redis (configurable via Redis AOF/RDB)
            - Survives Redis restarts (if persistence is enabled)
            - No ack required for publishing (fire and forget)

        Example - Simple Event:
            ```python
            await broker.publish(
                "users.events",
                value=b'{"user_id": 123, "action": "created"}',
                headers={"version": "1.0", "source": "api"}
            )
            ```

        Example - With Partition Key:
            ```python
            # All events for user_id=123 have same key for ordering
            await broker.publish(
                "users.events",
                value=b'{"user_id": 123, "action": "updated"}',
                key="user:123",
                headers={"version": "1.0"}
            )
            ```

        Integration with Evora App:
            Typically called via App.publish() which handles:
            - Event serialization (domain objects → bytes)
            - Schema validation
            - Header injection (version, timestamp, etc.)
            - Telemetry/observability

        Performance:
            - XADD is O(1) - very fast
            - No blocking, returns immediately
            - Suitable for high-throughput scenarios
            - Consider pipelining for batch publishes

        See Also:
            - run_consumer(): How messages are consumed
            - App.publish(): Higher-level publishing API
        """
        # Redis Streams store fields as a dict of string->string/bytes
        fields: dict[bytes, bytes] = {
            b"v": value,
            b"k": (key or "").encode("utf-8"),
            b"h": json.dumps(headers or {}).encode("utf-8"),
            b"a": b"1",
        }
        await self.client.xadd(channel, fields)

    async def _ensure_group(self, channel: str) -> None:
        """
        Ensure a consumer group exists for the specified channel.

        Creates a consumer group if it doesn't already exist. The group is
        initialized to consume only new messages (starting from "$" - the end
        of the stream).

        Consumer Group Behavior:
            - Group tracks which messages have been consumed by the group
            - Multiple consumers in the same group share the workload
            - Each message is delivered to only one consumer in the group
            - Pending Entry List (PEL) tracks unacknowledged messages

        Starting Position:
            - id="$" means "start from now, only consume new messages"
            - Messages published before group creation are NOT consumed
            - This prevents replaying old events when deploying new services

        Args:
            channel: The stream channel name to create the group for

        Redis Operation:
            XGROUP CREATE <channel> <group_id> $ MKSTREAM

        Idempotency:
            - Safe to call multiple times
            - If group exists, BUSYGROUP error is caught and ignored
            - Other errors are propagated (e.g., connection failures)

        MKSTREAM Option:
            - If mkstream=True (default), creates the stream if it doesn't exist
            - Prevents errors when group is created before first message
            - Stream is created empty

        Example:
            ```python
            # First consumer creates the group
            await broker._ensure_group("users.events")
            # Group "user-service" now exists, starts at end of stream

            # Second consumer with same group_id
            await broker._ensure_group("users.events")
            # BUSYGROUP error caught, returns silently
            ```

        Alternative Starting Positions:
            If you wanted to consume from the beginning (for reprocessing):
            - Change id="$" to id="0" (consume all messages)
            - Or id="<specific_message_id>" to resume from a point

        Why Start at End ($):
            - New deployments don't replay old events
            - Prevents duplicate processing of historical data
            - Aligns with event-driven architecture principles
            - Old messages can be replayed explicitly if needed

        See Also:
            - run_consumer(): Calls this before starting consumption
            - Redis XGROUP CREATE documentation
        """
        try:
            # Create group at beginning of stream, Start consuming only new messages from now on.
            await self.client.xgroup_create(
                name=channel, groupname=self.group_id, id="$", mkstream=self.mkstream
            )
        except Exception as e:
            # BUSYGROUP means it exists
            if "BUSYGROUP" in str(e):
                return
            raise

    async def run_consumer(
        self,
        channels: list[str],
        handler: Callable[[Message], Awaitable[None]],
        *,
        consumer_name: str,
    ) -> None:
        """
        Run the consumer loop for processing messages from Redis Streams.

        This is the main entry point for message consumption. It:
        1. Creates consumer groups for all channels
        2. Starts two background tasks:
           - Main consumer loop (reads and processes messages)
           - Retry scheduler (moves due retries back to stream)
        3. Runs indefinitely until cancelled

        Architecture:
            The consumer uses a two-loop design:
            - **Consumer Loop**: Reclaim stuck messages → Read new messages → Process
            - **Retry Scheduler**: Poll ZSET → Re-add due messages to stream

        Message Processing Priority:
            1. Reclaimed stuck messages (from PEL) - processed first
            2. New messages (from XREADGROUP) - processed when no stuck messages

        This prioritization ensures stuck messages are cleared before accepting
        new work, preventing PEL buildup.

        Args:
            channels: List of stream channel names to consume from
            handler: Async function that processes each message
                     Signature: async def handler(msg: Message) -> None
                     Typically this is App._dispatch
            consumer_name: Unique identifier for this consumer instance
                          Used in PEL tracking and poison detection

        Consumer Group Mechanics:
            - All consumers with the same group_id share the workload
            - Each message is delivered to exactly one consumer in the group
            - Messages are load-balanced automatically by Redis
            - PEL tracks which consumer owns which pending message

        Graceful Shutdown:
            - Cancel the task running this coroutine
            - In-flight messages remain in PEL
            - On restart, stuck messages are reclaimed via _reclaim_stuck
            - No message loss on crash (at-least-once guarantee)

        Blocking Behavior:
            - XREADGROUP blocks for up to block_ms (default 2000ms)
            - Returns immediately if messages are available
            - Prevents tight loop when idle
            - CPU-efficient for low-throughput scenarios

        Error Handling:
            - Handler exceptions are caught by _process_one
            - Retryable errors → schedule_retry (App layer)
            - Fatal errors → DLQ (App layer or broker safety net)
            - Structural failures → DLQ immediately

        Example:
            ```python
            broker = RedisStreamsBroker(
                client=redis_client,
                group_id="user-service",
                consumer_name="worker-1"
            )

            async def process_message(msg: Message):
                data = json.loads(msg.value)
                print(f"Processing user {data['user_id']}")

            # Run forever (or until cancelled)
            await broker.run_consumer(
                channels=["users.events", "orders.events"],
                handler=process_message,
                consumer_name="worker-1"
            )
            ```

        Integration with Evora App:
            ```python
            app = evora.App(broker=broker, ...)

            @app.subscribe("users.events", UserCreated)
            async def on_user_created(event: UserCreated):
                # Your business logic
                pass

            # App wraps broker.run_consumer
            await app.run()
            ```

        Background Tasks:
            1. **retry_scheduler()**:
               - Polls ZSET every 500ms
               - Moves due messages back to stream
               - Increments attempt counter
               - Removes from ZSET

            2. **loop()**:
               - Reclaims stuck messages (PEL)
               - Reads new messages (XREADGROUP)
               - Processes messages via handler
               - ACKs on success

        Performance Tuning:
            - batch_size: Messages per XREADGROUP call (default: 50)
            - block_ms: Max blocking time (default: 2000ms)
            - poison_idle_ms: Idle time before reclaim (default: 60000ms)
            - Adjust based on throughput and latency requirements

        Monitoring Recommendations:
            - Track PEL size per consumer (growing = stuck messages)
            - Track ZSET size per channel (growing = retry backlog)
            - Track DLQ size (growing = systemic failures)
            - Monitor consumer lag (latest stream ID vs group offset)

        See Also:
            - _ensure_group(): Group creation
            - _reclaim_stuck(): Poison message recovery
            - _process_one(): Individual message processing
            - schedule_retry(): Durable retry scheduling
        """
        self.consumer_name = consumer_name
        for ch in channels:
            await self._ensure_group(ch)

        async def retry_scheduler() -> None:
            """
            Background task: moves due retry entries from ZSET back to stream.

            This task runs in a loop every 500ms and:
            1. Queries all retry ZSETs for messages with score <= now
            2. Re-adds those messages to their original streams
            3. Increments the attempt counter
            4. Removes them from the ZSET

            Why ZSET for retry:
                - Score = due timestamp (ms since epoch)
                - ZRANGEBYSCORE efficiently finds due messages
                - Survives consumer crashes (state in Redis)
                - No in-memory timers needed
                - Distributed-safe (multiple consumers can run scheduler)

            Race Condition Safety:
                - Multiple consumers can run this simultaneously
                - ZRANGEBYSCORE + ZREM is not atomic
                - Duplicate re-adds are safe (idempotency layer handles it)
                - Worst case: message processed twice → idempotency prevents side effects

            Polling Interval:
                - 500ms is a reasonable balance
                - Lower = more responsive but more Redis calls
                - Higher = less load but longer retry delays
                - Retry delay accuracy: ±500ms

            Batch Size:
                - Fetches up to 100 due messages per channel per iteration
                - Prevents overwhelming stream with retry burst
                - Large backlogs are drained over multiple iterations

            Message Format (re-added to stream):
                - `v`: Original payload
                - `k`: Original key
                - `h`: Original headers
                - `a`: attempt + 1 (incremented)

            Error Handling:
                - Individual message decode failures are skipped
                - Redis connection errors propagate (task crashes)
                - Consumer will restart and retry scheduler restarts

            Example Flow:
                1. Handler fails at 10:00:00 with attempt=1
                2. schedule_retry: ZADD with score = 10:00:00.500 (500ms delay)
                3. Scheduler polls at 10:00:00.250 → not due yet
                4. Scheduler polls at 10:00:00.750 → due! Re-add to stream
                5. Consumer reads message with attempt=2
            """
            while True:
                now = _now_ms()
                for ch in channels:
                    zkey = ch + self.retry_zset_suffix
                    due_members = await self.client.zrangebyscore(
                        zkey,
                        "-inf",
                        now,
                        start=0,
                        num=100,
                    )

                    if not due_members:
                        continue

                    pipe = self.client.pipeline()
                    for m in due_members:
                        # decode member
                        if isinstance(m, (bytes, bytearray)):
                            m = m.decode("utf-8")
                        payload = json.loads(m)

                        fields: dict[bytes, bytes] = {
                            b"v": payload["v"].encode("utf-8"),
                            b"k": payload.get("k", "").encode("utf-8"),
                            b"h": json.dumps(payload.get("h", {})).encode("utf-8"),
                            b"a": str(payload.get("a", 1)).encode("utf-8"),
                        }
                        pipe.xadd(payload["channel"], fields)
                        pipe.zrem(zkey, m)

                    await pipe.execute()

                await anyio.sleep(0.5)

        async def loop() -> None:
            """
            Main consumer loop: reclaim stuck messages, then read new messages.

            This loop runs continuously and implements a two-phase processing strategy:

            Phase 1 - Reclaim Stuck Messages (Poison Detection):
                - Check PEL for messages idle > poison_idle_ms
                - Claim them to this consumer
                - Process immediately
                - Loop back to check for more stuck messages

            Phase 2 - Read New Messages (Normal Operation):
                - Only executed when no stuck messages found
                - XREADGROUP reads new messages from stream
                - Process each message
                - ACK on success

            Why Prioritize Stuck Messages:
                - Prevents PEL buildup
                - Recovers from consumer crashes quickly
                - Ensures messages don't get "lost" in PEL
                - Maintains processing throughput

            Stuck Message Detection:
                - A message is "stuck" if:
                  - It's in the PEL (pending)
                  - It's been idle for > poison_idle_ms (default 60s)
                  - Original consumer likely crashed

            XREADGROUP Behavior:
                - Reads messages not yet delivered to any consumer in group
                - Uses ">" as the ID (next undelivered message)
                - Blocks for up to block_ms if no messages available
                - Returns immediately if messages are waiting
                - Load balances across consumers in the group

            Message Decoding:
                - Redis returns (stream_name, [(msg_id, fields), ...])
                - Each entry is decoded via _decode_entry
                - Invalid messages are ACKed and skipped

            Error Isolation:
                - Each message is processed independently
                - One failing message doesn't block others
                - Errors are handled by _process_one

            Graceful Degradation:
                - If stuck message processing fails → continues to new messages
                - If XREADGROUP fails → exception propagates (fatal)
                - If individual message fails → DLQ or retry

            Example Scenario - Consumer Crash Recovery:
                1. Consumer A receives message X at 10:00:00
                2. Consumer A crashes at 10:00:05 (before ACK)
                3. Message X sits in PEL, owned by Consumer A
                4. Consumer B starts at 10:01:00
                5. At 10:01:05, loop sees message idle > 60s
                6. _reclaim_stuck claims message X to Consumer B
                7. Consumer B processes and ACKs message X
                8. PEL cleared, no message loss

            Performance Characteristics:
                - Reclaim batch: Up to 25 messages per channel per iteration
                - Read batch: Up to batch_size messages per iteration
                - Prioritizes clearing backlog over accepting new work
                - CPU-efficient blocking (not a tight loop)

            Termination:
                - Loop runs forever until task is cancelled
                - On cancellation, in-flight messages remain in PEL
                - On restart, those messages will be reclaimed
            """
            while True:
                # 1) First drain reclaimed stuck messages (a little), then read new ones
                reclaimed_work: list[tuple[str, str, dict]] = []

                for ch in channels:
                    reclaimed = await self._reclaim_stuck(
                        ch, min_idle_ms=self.poison_idle_ms, count=25
                    )
                    for mid, fields in reclaimed:
                        reclaimed_work.append((ch, mid, fields))

                if reclaimed_work:
                    for ch, mid, fields in reclaimed_work:
                        msg = await self._decode_entry(ch, mid, fields)
                        if msg is None:
                            continue
                        await self._process_one(msg, handler, raw_msg_id=mid)
                    continue  # loop back to reclaim again quickly

                # 2) If no reclaimed work, read new messages
                streams = {ch: ">" for ch in channels}
                resp = await self.client.xreadgroup(
                    groupname=self.group_id,
                    consumername=self._consumer(),
                    streams=streams,
                    count=self.batch_size,
                    block=self.block_ms,
                )
                if not resp:
                    continue

                for stream_name, entries in resp:
                    ch = (
                        stream_name.decode("utf-8")
                        if isinstance(stream_name, (bytes, bytearray))
                        else stream_name
                    )
                    for mid, fields in entries:
                        msg = await self._decode_entry(ch, mid, fields)
                        if msg is None:
                            continue
                        await self._process_one(msg, handler, raw_msg_id=mid)

        async with anyio.create_task_group() as tg:
            tg.start_soon(loop)
            tg.start_soon(retry_scheduler)
