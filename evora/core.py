from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, ClassVar, Dict, Optional, Type, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T", bound="Event")


class Envelope(BaseModel):
    specversion: str = "1.0"
    id: str
    type: str
    source: str
    time: str
    subject: Optional[str] = None
    datacontenttype: str = "application/json"
    dataschema: str
    traceparent: Optional[str] = None
    data: Dict[str, Any]
    meta: Dict[str, Any] = Field(default_factory=dict)

    # {
    #     "specversion": "1.0",
    #     "id": "uuid",
    #     "type": "UserCreated",
    #     "source": "users-service",
    #     "time": "2026-02-26T12:34:56Z",
    #     "subject": "user:123",
    #     "datacontenttype": "application/json",
    #     "dataschema": "eventsdk://UserCreated/2",
    #     "traceparent": "00-....",
    #     "data": {"user_id": 123, "email": "a@b.com"},
    #     "meta": {
    #         "schema_version": 2,
    #         "partition_key": "123",
    #         "attempt": 1
    #     }
    # }

class Event(BaseModel):
    """
    Base typed event contract.

    - __event_name__ determines routing / type.
    - __version__ used in dataschema + migrations later.
    """
    __event_name__: ClassVar[Optional[str]] = None
    __version__: ClassVar[int]  # REQUIRED: subclasses must set

    @classmethod
    def event_type(cls) -> str:
        return cls.__event_name__ or cls.__name__

    @classmethod
    def schema_uri(cls) -> str:
        return f"evora://{cls.event_type()}/{cls.__version__}"

    def to_envelope(
        self,
        *,
        source: str,
        subject: str | None = None,
        traceparent: str | None = None,
        meta: dict[str, Any] | None = None,
        event_id: str | None = None,
    ) -> Envelope:
        now = datetime.now(timezone.utc).isoformat()
        return Envelope(
            id=event_id or str(uuid.uuid4()),
            type=self.event_type(),
            source=source,
            time=now,
            subject=subject,
            dataschema=self.schema_uri(),
            traceparent=traceparent,
            data=self.model_dump(),
            meta={"schema_version": self.__version__, **(meta or {})},
        )


class Registry:
    def __init__(self) -> None:
        self._by_type: dict[str, Type[Event]] = {}

    def register(self, event_cls: Type[Event]) -> None:
        self._by_type[event_cls.event_type()] = event_cls

    def resolve(self, event_type: str) -> Type[Event]:
        if event_type not in self._by_type:
            raise KeyError(f"Unregistered event type: {event_type}")
        return self._by_type[event_type]

    def decode(self, raw: bytes) -> tuple[Envelope, Event]:
        envelope = Envelope.model_validate_json(raw)
        cls = self.resolve(envelope.type)
        event = cls.model_validate(envelope.data)
        return envelope, event

    def encode(self, envelope: Envelope) -> bytes:
        return envelope.model_dump_json().encode("utf-8")