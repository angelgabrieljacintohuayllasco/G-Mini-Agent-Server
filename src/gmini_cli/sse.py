"""Decodificador de Server-Sent Events (``text/event-stream``).

Sigue el algoritmo de la especificación de HTML (EventSource) con dos
detalles importantes para servidores reales:

- Los finales de línea pueden ser ``\\n``, ``\\r\\n`` o ``\\r``, y un ``\\r\\n``
  puede llegar partido entre dos fragmentos de red. Partirlo mal crea una
  línea vacía de más, que despacha el evento antes de tiempo y pierde su
  nombre. Por eso no se delega en ``iter_lines`` de la librería HTTP.
- Si el flujo termina sin la línea vacía final, el último evento se entrega
  igual (la especificación lo descarta, pero perderíamos el ``done``).
"""

from __future__ import annotations

import codecs
from collections.abc import Iterable, Iterator
from dataclasses import dataclass


@dataclass
class SSEEvent:
    event: str = "message"
    data: str = ""
    id: str | None = None
    retry: int | None = None


def iter_decoded(chunks: Iterable[bytes], encoding: str = "utf-8") -> Iterator[str]:
    """Decodifica bytes de forma incremental (un carácter puede venir partido)."""
    decoder = codecs.getincrementaldecoder(encoding)(errors="replace")
    for chunk in chunks:
        text = decoder.decode(chunk)
        if text:
            yield text
    tail = decoder.decode(b"", final=True)
    if tail:
        yield tail


def iter_lines(chunks: Iterable[str]) -> Iterator[str]:
    """Separa texto en líneas sin el terminador, tolerando ``\\r\\n`` partidos."""
    buffer = ""
    first = True
    for chunk in chunks:
        if first and chunk:
            first = False
            if chunk.startswith("﻿"):
                chunk = chunk[1:]
        buffer += chunk
        start = 0
        length = len(buffer)
        while start < length:
            newline = buffer.find("\n", start)
            carriage = buffer.find("\r", start)
            if newline == -1 and carriage == -1:
                break
            if carriage != -1 and (newline == -1 or carriage < newline):
                if carriage == length - 1:
                    # Puede ser la primera mitad de un \r\n: esperar al siguiente fragmento.
                    break
                yield buffer[start:carriage]
                start = carriage + 2 if buffer[carriage + 1] == "\n" else carriage + 1
            else:
                yield buffer[start:newline]
                start = newline + 1
        buffer = buffer[start:]
    if buffer.endswith("\r"):
        buffer = buffer[:-1]
        yield buffer
        buffer = ""
    if buffer:
        yield buffer


class SSEDecoder:
    """Acumula campos línea a línea y devuelve un evento al ver una línea vacía."""

    def __init__(self) -> None:
        self._reset()
        self.last_event_id: str | None = None

    def _reset(self) -> None:
        self._event = ""
        self._data: list[str] = []
        self._has_data = False
        self._retry: int | None = None

    def feed(self, line: str) -> SSEEvent | None:
        if line == "":
            return self._dispatch()
        if line.startswith(":"):
            return None
        field, sep, value = line.partition(":")
        if sep and value.startswith(" "):
            value = value[1:]
        if field == "event":
            self._event = value
        elif field == "data":
            self._data.append(value)
            self._has_data = True
        elif field == "id":
            if "\0" not in value:
                self.last_event_id = value
        elif field == "retry" and value.isdigit():
            self._retry = int(value)
        return None

    def _dispatch(self) -> SSEEvent | None:
        if not self._has_data:
            self._reset()
            return None
        event = SSEEvent(
            event=self._event or "message",
            data="\n".join(self._data),
            id=self.last_event_id,
            retry=self._retry,
        )
        self._reset()
        return event

    def flush(self) -> SSEEvent | None:
        return self._dispatch()


def iter_sse_events(text_chunks: Iterable[str]) -> Iterator[SSEEvent]:
    decoder = SSEDecoder()
    for line in iter_lines(text_chunks):
        event = decoder.feed(line)
        if event is not None:
            yield event
    event = decoder.flush()
    if event is not None:
        yield event
