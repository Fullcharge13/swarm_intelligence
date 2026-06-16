"""Async message bus for direct agent-to-agent communication.

Agents subscribe to topics and post messages.  The bus is in-process and
async-native (anyio).  For distributed deployments this can be swapped for
a Redis pub/sub or NATS backend without changing the agent API.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine


MessageHandler = Callable[["Message"], Coroutine[Any, Any, None]]


@dataclass
class Message:
    topic: str
    payload: Any
    sender: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    reply_to: str | None = None  # topic agents should reply on


class MessageBus:
    """Simple async publish-subscribe bus.

    Usage::

        bus = MessageBus()

        async def on_help(msg):
            print(f"{msg.sender} asks: {msg.payload}")

        bus.subscribe("help", on_help)
        await bus.publish("help", "Who has the result for task-3?", sender="agent-1")
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[MessageHandler]] = {}
        self._history: list[Message] = []

    def subscribe(self, topic: str, handler: MessageHandler) -> None:
        self._subscribers.setdefault(topic, []).append(handler)

    def unsubscribe(self, topic: str, handler: MessageHandler) -> None:
        if topic in self._subscribers:
            self._subscribers[topic] = [h for h in self._subscribers[topic] if h != handler]

    async def publish(self, topic: str, payload: Any, *, sender: str, reply_to: str | None = None) -> int:
        """Dispatch message to all subscribers; returns subscriber count."""
        msg = Message(topic=topic, payload=payload, sender=sender, reply_to=reply_to)
        self._history.append(msg)
        handlers = list(self._subscribers.get(topic, []) + self._subscribers.get("*", []))
        if handlers:
            await asyncio.gather(*[h(msg) for h in handlers])
        return len(handlers)

    def recent(self, n: int = 20) -> list[Message]:
        return self._history[-n:]
