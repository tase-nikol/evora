class EvoraError(Exception):
    """Base class for SDK errors."""


class ContractError(EvoraError):
    """Invalid envelope, unknown event type, schema mismatch, validation issues."""


class RetryableError(EvoraError):
    """Transient failure; safe to retry."""


class FatalError(EvoraError):
    """Permanent failure; do not retry."""


class IdempotencyConflict(EvoraError):
    """Idempotency store failed or inconsistent."""
