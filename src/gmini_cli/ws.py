"""Chat por WebSocket (``/api/v1/ws``) con el cliente síncrono de ``websockets``.

Una conexión sirve para varios turnos. Cada mensaje lleva un ``id`` y se
descartan los fragmentos con otro ``id``: tras cancelar una respuesta pueden
llegar restos que no deben mezclarse con la siguiente.
"""

from __future__ import annotations

import contextlib
import itertools
import json
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus, InvalidURI
from websockets.sync.client import ClientConnection, connect

from . import __version__
from .client import USER_AGENT
from .errors import ApiError, CliError, ConnectionFailure, parse_error_body
from .events import ChatEvent

PING_INTERVAL = 25.0
RECV_POLL = 0.25
MAX_FRAME = 8 * 1024 * 1024
IGNORED_TYPES = frozenset({"pong", "node.invoke", "node.registered"})
AUTH_CLOSE_CODE = 4401


class WsChatClient:
    """Sesión de chat sobre una conexión WebSocket persistente."""

    def __init__(
        self,
        ws_url: str,
        token: str | None,
        *,
        device_name: str,
        open_timeout: float = 10.0,
        debug: Callable[[str], None] | None = None,
        connector: Callable[..., ClientConnection] = connect,
    ) -> None:
        self.ws_url = ws_url
        self.token = token
        self.device_name = device_name
        self.open_timeout = open_timeout
        self.ready: dict[str, Any] = {}
        self._debug = debug
        self._connector = connector
        self._ws: ClientConnection | None = None
        self._stack: contextlib.ExitStack | None = None
        self._send_lock = threading.Lock()
        self._abort = threading.Event()
        self._closed = threading.Event()
        self._ids = itertools.count(1)
        self._pinger: threading.Thread | None = None

    # ── Conexión ────────────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._ws is not None

    def _log(self, text: str) -> None:
        if self._debug:
            self._debug(text)

    def connect(self) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        self._log(f"WS {self.ws_url}")
        stack = contextlib.ExitStack()
        try:
            # La conexión vive entre turnos; se entra a su contexto (lo que exigen
            # las versiones nuevas de websockets) y se sale en close().
            self._ws = stack.enter_context(
                self._connector(
                    self.ws_url,
                    additional_headers=headers,
                    user_agent_header=USER_AGENT,
                    open_timeout=self.open_timeout,
                    close_timeout=2,
                    max_size=MAX_FRAME,
                )
            )
            self._stack = stack
        except InvalidStatus as exc:
            # El núcleo cierra con 4401 antes de aceptar; uvicorn lo traduce a un 403.
            response = exc.response
            if response.status_code in (401, 403):
                raise ApiError(
                    401,
                    "invalid_token",
                    "El servidor rechazó la conexión WebSocket (token o host no válidos).",
                ) from exc
            try:
                body = json.loads(response.body or b"{}")
            except ValueError:
                body = None
            code, message = parse_error_body(response.status_code, body)
            raise ApiError(response.status_code, code, message) from exc
        except InvalidURI as exc:
            raise CliError(f"URL de WebSocket no válida: {self.ws_url}") from exc
        except (OSError, TimeoutError, InvalidHandshake) as exc:
            raise ConnectionFailure.unreachable(self.ws_url, str(exc)[:160]) from exc

        self._closed.clear()
        self._send(
            {"type": "hello", "client": "gmini-cli", "version": __version__, "device_name": self.device_name}
        )
        deadline = time.monotonic() + self.open_timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.close()
                raise ConnectionFailure.timeout(self.ws_url)
            frame = self._recv(remaining)
            if frame is None:
                continue
            kind = frame.get("type")
            if kind == "ready":
                self.ready = frame
                self._start_pinger()
                return frame
            if kind == "error":
                self.close()
                raise ApiError(
                    401 if frame.get("code") == "invalid_token" else 400,
                    str(frame.get("code") or "error"),
                    str(frame.get("message") or ""),
                )

    def close(self) -> None:
        self._closed.set()
        self._ws = None
        stack, self._stack = self._stack, None
        if stack is not None:
            # Cerrar nunca debe ocultar el error original.
            with contextlib.suppress(Exception):
                stack.close()

    def __enter__(self) -> WsChatClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _start_pinger(self) -> None:
        def loop() -> None:
            while not self._closed.wait(PING_INTERVAL):
                try:
                    self._send({"type": "ping"})
                except CliError:
                    return

        self._pinger = threading.Thread(target=loop, name="gmini-ws-ping", daemon=True)
        self._pinger.start()

    # ── E/S ─────────────────────────────────────────────────────────────

    def _send(self, frame: dict[str, Any]) -> None:
        ws = self._ws
        if ws is None:
            raise ConnectionFailure.closed(self.ws_url)
        with self._send_lock:
            try:
                ws.send(json.dumps(frame, ensure_ascii=False))
            except ConnectionClosed as exc:
                self.close()
                raise ConnectionFailure.closed(self.ws_url) from exc

    def _recv(self, timeout: float) -> dict[str, Any] | None:
        ws = self._ws
        if ws is None:
            raise ConnectionFailure.closed(self.ws_url)
        try:
            raw = ws.recv(timeout=timeout)
        except TimeoutError:
            return None
        except ConnectionClosed as exc:
            self.close()
            close = exc.rcvd
            if close is not None and close.code == AUTH_CLOSE_CODE:
                raise ApiError(401, "invalid_token", close.reason or "") from exc
            raise ConnectionFailure.closed(self.ws_url) from exc
        try:
            frame = json.loads(raw)
        except (TypeError, ValueError):
            self._log("WS: frame ignorado (no es JSON)")
            return None
        return frame if isinstance(frame, dict) else None

    # ── Chat ────────────────────────────────────────────────────────────

    def chat(self, text: str, session_id: str | None = None) -> Iterator[ChatEvent]:
        """Envía un mensaje y entrega eventos hasta ``done`` o ``error``."""
        if self._ws is None:
            self.connect()
        message_id = f"m{next(self._ids)}"
        self._abort.clear()
        frame: dict[str, Any] = {"type": "chat", "id": message_id, "text": text}
        if session_id:
            frame["session_id"] = session_id
        self._send(frame)
        while not self._abort.is_set():
            incoming = self._recv(RECV_POLL)
            if incoming is None:
                continue
            kind = str(incoming.pop("type", "") or "")
            if not kind or kind in IGNORED_TYPES:
                continue
            frame_id = incoming.get("id")
            if frame_id is not None and frame_id != message_id:
                continue
            event = ChatEvent(kind, incoming)
            yield event
            if event.is_terminal:
                return

    def cancel(self) -> None:
        """Pide al servidor que detenga la respuesta en curso y deja de esperarla."""
        self._abort.set()
        if self._ws is not None:
            with contextlib.suppress(CliError):
                self._send({"type": "cancel"})


def send_cancel(
    ws_url: str,
    token: str | None,
    *,
    timeout: float = 5.0,
    connector: Callable[..., ClientConnection] = connect,
) -> bool:
    """Pide al núcleo que detenga la respuesta en curso con una conexión corta.

    Con SSE cerrar la conexión no basta: el núcleo termina el turno igual. El
    frame ``cancel`` del WebSocket sí lo detiene. Devuelve ``True`` si se envió.
    """
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        with connector(
            ws_url,
            additional_headers=headers,
            user_agent_header=USER_AGENT,
            open_timeout=timeout,
            close_timeout=1,
        ) as ws:
            ws.send(json.dumps({"type": "cancel"}))
            return True
    except (OSError, TimeoutError, InvalidHandshake, ConnectionClosed):
        return False
