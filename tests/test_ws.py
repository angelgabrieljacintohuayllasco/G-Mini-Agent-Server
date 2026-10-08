from __future__ import annotations

import json
import time
from typing import Any

import pytest

from gmini_cli.client import ws_url_for
from gmini_cli.errors import ApiError
from gmini_cli.ws import WsChatClient, send_cancel
from mock_server import ServerThread


def wait_for(predicate: Any, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def ws_client(server: ServerThread) -> WsChatClient:
    client = WsChatClient(ws_url_for(server.url), server.core.session_token, device_name="pruebas")
    yield client
    client.close()


def test_handshake_and_chat(ws_client: WsChatClient, server: ServerThread) -> None:
    ready = ws_client.connect()
    assert ready["agent_name"] == "G-Mini"
    hello = server.core.ws_frames[0]
    assert hello["type"] == "hello"
    assert hello["client"] == "gmini-cli"
    assert hello["device_name"] == "pruebas"

    events = list(ws_client.chat("crea una acción"))
    assert events[-1].type == "done"
    assert events[-1].data["reply"] == "Listo, te aviso a las 18:00."
    assert any(e.type == "action" and e.data["action"] == "schedule_create_job" for e in events)
    chat_frame = server.core.ws_frames[-1]
    assert chat_frame["type"] == "chat"
    assert all(e.data.get("id") == chat_frame["id"] for e in events)


def test_connect_lazily_and_reuse_connection(ws_client: WsChatClient, server: ServerThread) -> None:
    first = list(ws_client.chat("hola"))
    second = list(ws_client.chat("hola"))
    assert first[-1].type == second[-1].type == "done"
    assert [f["type"] for f in server.core.ws_frames].count("hello") == 1


def test_invalid_token_is_rejected(server: ServerThread) -> None:
    client = WsChatClient(ws_url_for(server.url), "token-falso", device_name="pruebas")
    with pytest.raises(ApiError) as caught:
        client.connect()
    assert caught.value.code == "invalid_token"


def test_cancel_stops_the_answer(ws_client: WsChatClient, server: ServerThread) -> None:
    events = ws_client.chat("respuesta lenta")
    first = next(events)
    while first.type != "chunk":
        first = next(events)
    ws_client.cancel()
    assert list(events) == []
    assert wait_for(server.core.cancelled.is_set)
    assert {"type": "cancel"} in server.core.ws_frames


def test_send_cancel_helper(server: ServerThread) -> None:
    assert send_cancel(ws_url_for(server.url), server.core.session_token)
    assert wait_for(lambda: {"type": "cancel"} in server.core.ws_frames)
    assert not send_cancel("ws://127.0.0.1:9/api/v1/ws", "x", timeout=1)


class FakeConnection:
    """Conexión mínima para probar el filtrado por ``id`` sin red."""

    def __init__(self, frames: list[dict[str, Any]]) -> None:
        self.incoming = [json.dumps(frame) for frame in frames]
        self.sent: list[dict[str, Any]] = []

    def send(self, data: str) -> None:
        self.sent.append(json.loads(data))

    def recv(self, timeout: float | None = None) -> str:
        if not self.incoming:
            raise TimeoutError
        return self.incoming.pop(0)

    def close(self) -> None:
        pass

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def test_frames_with_another_id_are_ignored() -> None:
    fake = FakeConnection(
        [
            {"type": "ready", "agent_name": "G-Mini", "protocol": 1, "session_id": "s"},
            {"type": "chunk", "id": "m-viejo", "text": "resto de una respuesta cancelada"},
            {"type": "pong"},
            {"type": "state", "status": "thinking", "emotion": "neutral"},
            {"type": "chunk", "id": "m1", "text": "Hola"},
            {"type": "done", "id": "m1", "reply": "Hola", "session_id": "s"},
        ]
    )
    client = WsChatClient("ws://fake/api/v1/ws", "t", device_name="x", connector=lambda *a, **k: fake)
    events = list(client.chat("hola"))
    assert [e.type for e in events] == ["state", "chunk", "done"]
    assert events[1].text == "Hola"
    assert fake.sent[0]["type"] == "hello"
    assert fake.sent[1] == {"type": "chat", "id": "m1", "text": "hola"}
    client.close()


def test_busy_error_without_id_ends_the_turn() -> None:
    fake = FakeConnection(
        [
            {"type": "ready", "agent_name": "G-Mini", "protocol": 1},
            {"type": "error", "code": "busy", "message": "Ya hay una respuesta en curso."},
        ]
    )
    client = WsChatClient("ws://fake/api/v1/ws", "t", device_name="x", connector=lambda *a, **k: fake)
    events = list(client.chat("hola"))
    assert events[-1].type == "error"
    assert events[-1].data["code"] == "busy"
    client.close()
