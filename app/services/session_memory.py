"""Temporary in-memory session state for contract chat."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.models.chat import OrderLineItem


@dataclass
class SessionState:
    session_id: str
    business_id: str
    messages: list[dict[str, str]] = field(default_factory=list)
    cart_items: list[OrderLineItem] = field(default_factory=list)
    handoff_active: bool = False
    awaiting_fulfillment_preference: bool = False
    last_active: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class SessionMemoryStore:
    """In-memory state store with inactivity expiry."""

    def __init__(self, ttl_hours: int = 2) -> None:
        self.ttl = timedelta(hours=ttl_hours)
        self._sessions: dict[str, SessionState] = {}

    def get_or_create(self, session_id: str, business_id: str) -> SessionState:
        self.expire_old()
        state = self._sessions.get(session_id)
        if state is None or state.business_id != business_id:
            state = SessionState(session_id=session_id, business_id=business_id)
            self._sessions[session_id] = state
        state.last_active = datetime.now(timezone.utc)
        return state

    def append_turn(self, state: SessionState, customer_message: str, assistant_reply: str) -> None:
        state.messages.append({"role": "customer", "text": customer_message})
        state.messages.append({"role": "assistant", "text": assistant_reply})
        state.messages = state.messages[-20:]
        state.last_active = datetime.now(timezone.utc)

    def clear(self) -> None:
        self._sessions.clear()

    def expire_old(self) -> None:
        now = datetime.now(timezone.utc)
        expired = [
            session_id
            for session_id, state in self._sessions.items()
            if now - state.last_active > self.ttl
        ]
        for session_id in expired:
            self._sessions.pop(session_id, None)
