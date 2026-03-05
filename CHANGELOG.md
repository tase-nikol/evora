# Changelog

All notable changes to Evora will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-03-04

### Added
- **Schema Governance CLI**: Full schema extraction, compatibility checking, and breaking change detection
  - `evora schema check` command for comparing event schemas
  - `evora schema export` command for exporting normalized schemas
  - Automatic version bump enforcement
  - Event type consistency validation
- Comprehensive schema compatibility rules (field additions, removals, type changes)
- Schema diff reporting in text and JSON formats

### Changed
- Enhanced CLI with schema management subcommands
- Improved error messages for schema violations

### Fixed
- Schema compatibility edge cases with optional fields
- Type compatibility checking for union types

## [0.1.0] - 2026-02-28

### Added
- **Core Runtime**: Production-grade event processing framework
  - Strict mode enforcement for reliability patterns
  - Mandatory idempotency policies
  - Explicit event versioning
- **Error Classification**: RetryableError, FatalError, ContractError
- **Redis Streams Broker**: Full production implementation
  - Consumer groups with XREADGROUP
  - Automatic consumer registration
  - Configurable batch processing
- **Durable Retry System**: ZSET-based delay queue
  - Exponential backoff (500ms → 30s)
  - Crash-safe retry scheduling
  - Background retry scheduler
- **Poison Message Detection**: Automatic stuck message recovery
  - XPENDING scanning every 10s
  - XCLAIM for message reclaim
  - Delivery count enforcement
  - Automatic DLQ routing after max deliveries
- **Idempotency Store**: Redis-backed deduplication
  - Event ID-based tracking
  - TTL-based cleanup
  - Per-handler scoping
- **Structured DLQ**: Comprehensive failure metadata
  - Reason classification
  - Delivery count tracking
  - Timestamp and error details
  - Original message preservation
- **Telemetry Hooks**: Ready for OpenTelemetry integration
  - Event lifecycle hooks
  - Handler execution tracking
  - Error tracking hooks
- **Memory Broker**: In-memory implementation for testing
- **CloudEvents-style Envelopes**: Standard event format
- **Type-safe Event Contracts**: Pydantic-based validation

### Documentation
- Comprehensive README with quick start guide
- Architecture documentation
- Implementation status tracking
- Executive summary for stakeholders
- Poison message handling guide
- Redis Streams quickstart
- API reference documentation
- Visual architecture diagrams
- Quick reference guide
- Multiple working examples

### Examples
- Redis Streams with idempotency example
- Poison message detection demo
- Memory broker example

### Testing
- Integration tests for Redis broker
- Schema CLI tests
- Schema diff tests
- Telemetry tests

## [Unreleased]

### Planned for v0.3.0
- Rabbit broker implementation
- OpenTelemetry tracing integration
- Prometheus metrics export
- Structured logging with correlation IDs

### Planned for v0.4.0+
- Outbox pattern for transactional publishing
- Kafka broker implementation
- Admin CLI tools
- Message replay utilities
- Performance benchmarking suite
- Enhanced monitoring dashboards

---

[0.2.0]: https://github.com/tase-nikol/evora/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/your-org/evora/releases/tag/v0.1.0
