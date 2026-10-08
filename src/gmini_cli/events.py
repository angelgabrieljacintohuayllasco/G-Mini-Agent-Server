"""Eventos de una respuesta del agente, comunes a SSE y WebSocket.

Formato de la API remota v1: ``start``, ``chunk``, ``action``,
``action_result``, ``state``, ``notice``, ``approval``, ``done`` y ``error``
(por WebSocket además ``ready``, ``notify`` y ``pong``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .sse import SSEEvent

TERMINAL_TYPES = frozenset({"done", "error"})

STATUS_LABELS = {
    "idle": "inactivo",
    "listening": "escuchando",
    "thinking": "pensando",
    "acting": "actuando",
    "speaking": "hablando",
}


def action_name(data: dict[str, Any]) -> str:
    """Nombre de la acción: ``action`` en el contrato; ``type`` en borradores previos."""
    value = data.get("action") or data.get("type")
    return str(value) if value else "?"


@dataclass
class ChatEvent:
    type: str
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_sse(cls, sse: SSEEvent) -> ChatEvent:
        if not sse.data:
            return cls(sse.event, {})
        try:
            parsed = json.loads(sse.data)
        except ValueError:
            parsed = {"text": sse.data}
        if not isinstance(parsed, dict):
            parsed = {"value": parsed}
        return cls(sse.event, parsed)

    @property
    def is_terminal(self) -> bool:
        return self.type in TERMINAL_TYPES

    @property
    def text(self) -> str:
        value = self.data.get("text")
        return value if isinstance(value, str) else ""

    @property
    def session_id(self) -> str | None:
        value = self.data.get("session_id")
        return value if isinstance(value, str) and value else None


@dataclass
class ChatResult:
    session_id: str | None = None
    reply: str = ""
    actions: list[dict[str, Any]] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    approval_pending: bool = False
    approval_summary: str = ""
    finished: bool = False
    error: dict[str, Any] | None = None

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "session_id": self.session_id,
            "reply": self.reply,
            "actions": self.actions,
            "notices": self.notices,
            "approval_pending": self.approval_pending,
        }
        if self.error:
            payload["error"] = self.error
        return payload


class ChatAccumulator:
    """Reconstruye la respuesta completa a partir de los eventos."""

    def __init__(self) -> None:
        self.result = ChatResult()
        self._chunks: list[str] = []

    @property
    def partial_reply(self) -> str:
        return "".join(self._chunks)

    def feed(self, event: ChatEvent) -> None:
        kind, data = event.type, event.data
        if event.session_id:
            self.result.session_id = event.session_id
        if kind == "chunk":
            self._chunks.append(event.text)
        elif kind == "action":
            entry: dict[str, Any] = {"action": action_name(data), "params": data.get("params") or {}}
            if data.get("id") is not None:
                entry["id"] = data["id"]
            self.result.actions.append(entry)
        elif kind == "action_result":
            self._attach_result(data)
        elif kind == "notice":
            if event.text:
                self.result.notices.append(event.text)
        elif kind == "approval":
            self.result.approval_pending = bool(data.get("pending"))
            self.result.approval_summary = str(data.get("summary") or "")
        elif kind == "done":
            self._finish(data)
        elif kind == "error":
            self.result.error = {
                "code": str(data.get("code") or "error"),
                "message": str(data.get("message") or ""),
            }
            self.result.reply = self.result.reply or self.partial_reply
            self.result.finished = True

    def _finish(self, data: dict[str, Any]) -> None:
        reply = data.get("reply")
        self.result.reply = reply if isinstance(reply, str) else self.partial_reply
        actions = data.get("actions")
        if isinstance(actions, list) and actions:
            self.result.actions = [a for a in actions if isinstance(a, dict)]
        notices = data.get("notices")
        if isinstance(notices, list) and notices:
            self.result.notices = [str(n) for n in notices]
        if data.get("approval_pending"):
            self.result.approval_pending = True
        error = data.get("error")
        if isinstance(error, dict):
            self.result.error = {
                "code": str(error.get("code") or "error"),
                "message": str(error.get("message") or ""),
            }
        self.result.finished = True

    def _attach_result(self, data: dict[str, Any]) -> None:
        outcome = {"success": data.get("success"), "message": data.get("message")}
        name = action_name(data)
        for action in reversed(self.result.actions):
            if "success" in action:
                continue
            if data.get("id") is not None and action.get("id") is not None:
                matches = action.get("id") == data.get("id")
            else:
                matches = action.get("action") == name
            if matches:
                action.update(outcome)
                return
        self.result.actions.append({"action": name, **outcome})


def events_from_reply(payload: dict[str, Any]) -> list[ChatEvent]:
    """Convierte una respuesta sin streaming en la secuencia de eventos equivalente."""
    session_id = payload.get("session_id")
    reply = payload.get("reply") if isinstance(payload.get("reply"), str) else ""
    events = [ChatEvent("start", {"session_id": session_id})]
    for action in payload.get("actions") or []:
        if isinstance(action, dict):
            events.append(
                ChatEvent(
                    "action_result",
                    {
                        "action": action_name(action),
                        "success": action.get("success"),
                        "message": action.get("message"),
                    },
                )
            )
    for notice in payload.get("notices") or []:
        events.append(ChatEvent("notice", {"kind": "system", "text": str(notice)}))
    if payload.get("approval_pending"):
        events.append(ChatEvent("approval", {"pending": True, "summary": "", "kind": "approval"}))
    if reply:
        events.append(ChatEvent("chunk", {"text": reply}))
    error = payload.get("error")
    if isinstance(error, dict) and not reply:
        events.append(ChatEvent("error", {"code": error.get("code"), "message": error.get("message")}))
    else:
        events.append(ChatEvent("done", dict(payload)))
    return events
