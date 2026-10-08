from __future__ import annotations

import base64
import socket
from pathlib import Path

import pytest

from gmini_cli import client as client_module
from gmini_cli.client import ApiClient, as_items, build_attachment, ws_url_for
from gmini_cli.errors import EXIT_AUTH, EXIT_BUSY, EXIT_CONNECTION, ApiError, CliError, ConnectionFailure
from mock_server import SESSION_ID, MockCore, ServerThread


@pytest.fixture
def api(server: ServerThread) -> ApiClient:
    with ApiClient(server.url, server.core.session_token, timeout=10) as client:
        yield client


def test_health_and_me(api: ApiClient) -> None:
    health = api.health()
    assert health["protocol"] == 1
    assert health["mode"] == "server"
    me = api.me()
    assert me["kind"] == "session"
    assert me["agent"]["name"] == "G-Mini"


def test_chat_without_stream(api: ApiClient, core: MockCore) -> None:
    reply = api.chat("hola")
    assert reply["session_id"] == SESSION_ID
    assert reply["reply"].startswith("Hola, soy G-Mini")
    assert core.chat_bodies[-1]["stream"] is False


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_chat_stream_event_order(api: ApiClient, core: MockCore, newline: str) -> None:
    core.sse_newline = newline
    events = list(api.chat_stream("crea una acción"))
    kinds = [e.type for e in events]
    assert kinds[0] == "start"
    assert kinds[-1] == "done"
    assert {"action", "action_result", "notice", "state", "chunk"} <= set(kinds)
    action = next(e for e in events if e.type == "action")
    assert action.data["action"] == "schedule_create_job"
    assert events[-1].data["reply"] == "Listo, te aviso a las 18:00."


def test_chat_stream_sends_attachments(api: ApiClient, core: MockCore, tmp_path: Path) -> None:
    note = tmp_path / "nota.txt"
    note.write_text("comprar pan", encoding="utf-8")
    list(api.chat_stream("hola", attachments=[build_attachment(note)]))
    sent = core.chat_bodies[-1]["attachments"][0]
    assert sent["name"] == "nota.txt"
    assert sent["mime_type"] == "text/plain"
    assert base64.b64decode(sent["data_base64"]) == b"comprar pan"


def test_busy_maps_to_retryable_error(api: ApiClient, core: MockCore) -> None:
    core.busy = True
    with pytest.raises(ApiError) as caught:
        list(api.chat_stream("hola"))
    assert caught.value.code == "busy"
    assert caught.value.exit_code == EXIT_BUSY


def test_invalid_token(server: ServerThread) -> None:
    with ApiClient(server.url, "token-falso") as client, pytest.raises(ApiError) as caught:
        client.me()
    assert caught.value.code == "invalid_token"
    assert caught.value.exit_code == EXIT_AUTH


def test_missing_scope_for_device_tokens(server: ServerThread) -> None:
    token, _ = server.core.issue("cli", scopes=["chat"])
    with ApiClient(server.url, token) as client, pytest.raises(ApiError) as caught:
        client.devices()
    assert caught.value.code == "missing_scope"


def test_tasks_lifecycle(api: ApiClient) -> None:
    created = api.create_task({"prompt": "Resume el correo", "schedule": {"interval_seconds": 3600}})
    assert created["status"] == "scheduled"
    task_id = created["task_id"]
    assert [t["task_id"] for t in api.list_tasks()] == [task_id]
    assert api.list_tasks(status="done") == []
    assert api.get_task(task_id)["prompt"] == "Resume el correo"
    assert api.cancel_task(task_id)["status"] == "cancelled"
    with pytest.raises(ApiError) as caught:
        api.get_task("tsk_inexistente")
    assert caught.value.code == "not_found"


def test_task_validation_error(api: ApiClient) -> None:
    with pytest.raises(ApiError) as caught:
        api.create_task({"prompt": "x", "schedule": {"interval_seconds": 30}})
    assert caught.value.code == "validation_error"
    assert "60" in caught.value.message


def test_voice(api: ApiClient, core: MockCore) -> None:
    audio, content_type = api.tts("Hola")
    assert audio[:4] == b"RIFF"
    assert content_type == "audio/wav"
    result = api.stt(audio)
    assert result["text"].startswith("transcripción de prueba")
    assert core.stt_bodies[-1] == audio


def test_approvals_and_state(api: ApiClient, core: MockCore) -> None:
    with pytest.raises(ApiError) as caught:
        api.resolve_approval(True)
    assert caught.value.code == "not_found"
    core.approval_pending = True
    assert api.agent_state()["approval_pending"] is True
    assert api.resolve_approval(False)["status"] == "rejected"
    assert core.approvals == [False]


def test_sessions(api: ApiClient) -> None:
    sessions = api.sessions()
    assert sessions[0]["id"] == SESSION_ID
    messages = api.session_messages(SESSION_ID, limit=1)
    assert len(messages) == 1


def test_unknown_route_is_reported(api: ApiClient) -> None:
    with pytest.raises(ApiError) as caught:
        api.request("GET", "/no-existe")
    assert caught.value.code == "route_not_found"


def test_connection_refused() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with (
        ApiClient(f"http://127.0.0.1:{port}", timeout=3) as client,
        pytest.raises(ConnectionFailure) as caught,
    ):
        client.health()
    assert caught.value.exit_code == EXIT_CONNECTION


def test_ws_url_for() -> None:
    assert ws_url_for("http://100.71.131.70:8765") == "ws://100.71.131.70:8765/api/v1/ws"
    assert ws_url_for("https://gmini.example.com/base") == "wss://gmini.example.com/base/api/v1/ws"


def test_as_items_accepts_several_shapes() -> None:
    assert as_items({"items": [{"a": 1}, "x"]}) == [{"a": 1}]
    assert as_items([{"a": 1}]) == [{"a": 1}]
    assert as_items({"devices": [{"id": "d"}]}, "devices") == [{"id": "d"}]
    assert as_items("nada") == []


def test_attachment_size_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    big = tmp_path / "grande.bin"
    big.write_bytes(b"0" * 32)
    monkeypatch.setattr(client_module, "MAX_ATTACHMENT_BYTES", 16)
    with pytest.raises(CliError, match="pesa"):
        build_attachment(big)
    with pytest.raises(CliError, match="No se puede leer"):
        build_attachment(tmp_path / "no-existe.txt")
