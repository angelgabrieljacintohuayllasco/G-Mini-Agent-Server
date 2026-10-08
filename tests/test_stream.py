from __future__ import annotations

from collections.abc import Iterator

import pytest

from gmini_cli.chat import run_turn
from gmini_cli.console import Output
from gmini_cli.events import ChatEvent
from gmini_cli.stream import EventPump, TurnRenderer, error_from_event


def script() -> list[ChatEvent]:
    return [
        ChatEvent("start", {"session_id": "s1"}),
        ChatEvent("chunk", {"text": "Voy a revisar. "}),
        ChatEvent("action", {"action": "web_search", "params": {}, "id": "a1"}),
        ChatEvent(
            "action_result", {"action": "web_search", "success": True, "message": "3 resultados", "id": "a1"}
        ),
        ChatEvent("chunk", {"text": "Listo."}),
        ChatEvent("done", {"session_id": "s1", "reply": "Listo."}),
    ]


def test_event_pump_delivers_in_order() -> None:
    received: list[str] = []
    EventPump(iter(script())).run(lambda e: received.append(e.type))
    assert received == ["start", "chunk", "action", "action_result", "chunk", "done"]


def test_event_pump_reraises_worker_errors() -> None:
    def broken() -> Iterator[ChatEvent]:
        yield ChatEvent("start", {})
        raise ValueError("se cayó la red")

    with pytest.raises(ValueError, match="se cayó"):
        EventPump(broken()).run(lambda e: None)


def test_live_renderer_streams_text_and_progress(capsys: pytest.CaptureFixture[str]) -> None:
    renderer = TurnRenderer(Output(plain=True), "G-Mini", show_prefix=True, live=True)
    for event in script():
        renderer.on_event(event)
    captured = capsys.readouterr()
    assert captured.out == "G-Mini> Voy a revisar. \nListo.\n"
    assert "[acción] web_search" in captured.err
    assert "[ok] 3 resultados" in captured.err
    assert renderer.result.reply == "Listo."


def test_non_live_renderer_prints_only_the_conclusion(capsys: pytest.CaptureFixture[str]) -> None:
    renderer = TurnRenderer(Output(plain=True), "G-Mini", show_prefix=False, live=False)
    for event in script():
        renderer.on_event(event)
    assert capsys.readouterr().out == "Listo.\n"


def test_json_mode_keeps_stdout_clean(capsys: pytest.CaptureFixture[str]) -> None:
    renderer = TurnRenderer(Output(plain=True, json_mode=True), "G-Mini", live=True)
    for event in script():
        renderer.on_event(event)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_approval_is_announced(capsys: pytest.CaptureFixture[str]) -> None:
    renderer = TurnRenderer(Output(plain=True), "G-Mini", approval_hint="Responde con /aprobar o /rechazar.")
    renderer.on_event(
        ChatEvent("approval", {"pending": True, "summary": "Borrar archivos", "kind": "approval"})
    )
    err = capsys.readouterr().err
    assert "espera tu aprobación: Borrar archivos" in err
    assert "/aprobar" in err


class FakeSession:
    def __init__(self, events: list[ChatEvent]) -> None:
        self._events = events
        self.cancelled = False

    def events(self, message: str, attachments: object = None) -> Iterator[ChatEvent]:
        return iter(self._events)

    def cancel(self) -> None:
        self.cancelled = True


def test_ctrl_c_cancels_the_turn(capsys: pytest.CaptureFixture[str]) -> None:
    session = FakeSession(script())
    renderer = TurnRenderer(Output(plain=True), "G-Mini")
    original = renderer.on_event

    def interrupt_on_action(event: ChatEvent) -> None:
        original(event)
        if event.type == "action":
            raise KeyboardInterrupt

    renderer.on_event = interrupt_on_action  # type: ignore[method-assign]
    result, interrupted = run_turn(session, renderer, "hola")  # type: ignore[arg-type]
    assert interrupted
    assert session.cancelled
    assert not result.finished
    assert "(respuesta cancelada)" in capsys.readouterr().err


def test_error_from_event_maps_codes() -> None:
    error = error_from_event({"code": "provider_unavailable", "message": "sin key"})
    assert error.status == 503
    assert "proveedor" in error.message
