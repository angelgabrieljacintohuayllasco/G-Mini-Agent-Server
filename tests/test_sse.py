from __future__ import annotations

from gmini_cli.events import ChatEvent
from gmini_cli.sse import SSEDecoder, iter_decoded, iter_lines, iter_sse_events


def test_iter_lines_handles_crlf_split_between_chunks() -> None:
    chunks = ["event: chunk\r", "\ndata: {}\r\n\r", "\n"]
    assert list(iter_lines(chunks)) == ["event: chunk", "data: {}", ""]


def test_iter_lines_mixed_endings_and_tail() -> None:
    assert list(iter_lines(["a\rb\nc\r\nd"])) == ["a", "b", "c", "d"]
    assert list(iter_lines(["fin\r"])) == ["fin"]


def test_iter_lines_strips_bom() -> None:
    assert list(iter_lines(["﻿data: x\n"])) == ["data: x"]


def test_iter_decoded_joins_split_multibyte_characters() -> None:
    raw = "acción ¿sí?".encode()
    pieces = [raw[i : i + 1] for i in range(len(raw))]
    assert "".join(iter_decoded(pieces)) == "acción ¿sí?"


def test_decoder_multiline_data_comments_and_fields() -> None:
    decoder = SSEDecoder()
    lines = [": comentario", "event: chunk", "id: 7", "retry: 3000", "data: primera", "data:segunda", ""]
    events = [e for e in (decoder.feed(line) for line in lines) if e is not None]
    assert len(events) == 1
    assert events[0].event == "chunk"
    assert events[0].data == "primera\nsegunda"
    assert events[0].id == "7"
    assert events[0].retry == 3000


def test_decoder_skips_events_without_data_and_defaults_to_message() -> None:
    decoder = SSEDecoder()
    assert decoder.feed("event: ping") is None
    assert decoder.feed("") is None
    decoder.feed("data: hola")
    event = decoder.feed("")
    assert event is not None
    assert event.event == "message"


def test_last_event_is_delivered_without_final_blank_line() -> None:
    stream = ["event: start\ndata: {}\n\n", 'event: done\ndata: {"reply": "ok"}']
    events = list(iter_sse_events(stream))
    assert [e.event for e in events] == ["start", "done"]


def test_full_stream_split_byte_by_byte_with_crlf() -> None:
    body = (
        b'event: start\r\ndata: {"session_id": "s1"}\r\n\r\n'
        b'event: chunk\r\ndata: {"text": "Listo, "}\r\n\r\n'
        b'event: done\r\ndata: {"session_id": "s1", "reply": "Listo, hecho."}\r\n\r\n'
    )
    pieces = [body[i : i + 1] for i in range(len(body))]
    events = [ChatEvent.from_sse(e) for e in iter_sse_events(iter_decoded(pieces))]
    assert [e.type for e in events] == ["start", "chunk", "done"]
    assert events[1].text == "Listo, "
    assert events[2].data["reply"] == "Listo, hecho."


def test_chat_event_from_non_json_data() -> None:
    event = ChatEvent.from_sse(next(iter_sse_events(["event: chunk\ndata: texto plano\n\n"])))
    assert event.text == "texto plano"
