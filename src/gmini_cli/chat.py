"""Turnos de chat por SSE o WebSocket, con cancelación por Ctrl+C."""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

from .client import ApiClient
from .console import Output
from .errors import ApiError, CliError, UsageError
from .events import ChatEvent, ChatResult
from .stream import EventPump, TurnRenderer
from .ws import WsChatClient, send_cancel

TRANSPORTS = ("sse", "ws")


class ChatSession:
    """Envía mensajes al agente por el transporte elegido."""

    def __init__(self, client: ApiClient, *, transport: str, device_name: str, out: Output) -> None:
        if transport not in TRANSPORTS:
            raise UsageError(f"Transporte desconocido: {transport}", hint="Usa sse o ws.")
        self.client = client
        self.transport = transport
        self.device_name = device_name
        self.out = out
        self._ws: WsChatClient | None = None

    def _ws_client(self) -> WsChatClient:
        if self._ws is None:
            self._ws = WsChatClient(
                self.client.ws_url,
                self.client.token,
                device_name=self.device_name,
                debug=self.out.debug if self.out.verbose else None,
            )
        return self._ws

    def events(self, message: str, attachments: list[dict[str, Any]] | None = None) -> Iterator[ChatEvent]:
        if self.transport == "ws":
            if attachments:
                raise UsageError("Los adjuntos solo se pueden enviar por SSE.", hint="Quita --transport ws.")
            return self._ws_client().chat(message)
        return self.client.chat_stream(message, attachments=attachments)

    def cancel(self) -> None:
        if self.transport == "ws" and self._ws is not None and self._ws.connected:
            self._ws.cancel()
            return
        # Con SSE el núcleo sigue con el turno aunque se corte la conexión.
        if not send_cancel(self.client.ws_url, self.client.token, timeout=3.0):
            self.out.debug("No se pudo enviar la cancelación por WebSocket.")

    def close(self) -> None:
        if self._ws is not None:
            self._ws.close()
            self._ws = None


def run_turn(
    session: ChatSession,
    renderer: TurnRenderer,
    message: str,
    attachments: list[dict[str, Any]] | None = None,
) -> tuple[ChatResult, bool]:
    """Ejecuta un turno completo. Devuelve ``(resultado, interrumpido)``."""
    pump = EventPump(session.events(message, attachments))
    try:
        pump.run(renderer.on_event)
    except KeyboardInterrupt:
        renderer.interrupted()
        session.cancel()
        pump.wait(2.0)
        return renderer.result, True
    except BaseException:
        renderer.finish()
        raise
    return renderer.result, False


def wait_until_idle(client: ApiClient, *, timeout: float, poll: float = 1.0) -> bool:
    """Espera a que el agente quede libre. ``True`` si terminó a tiempo."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            state = client.agent_state()
        except (ApiError, CliError):
            return False
        if not state.get("busy"):
            return True
        time.sleep(poll)
    return False
