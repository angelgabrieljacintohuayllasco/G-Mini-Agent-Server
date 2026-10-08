"""Consumo de respuestas en streaming con Ctrl+C fiable en todos los sistemas.

En Windows una lectura de socket bloqueada no se interrumpe con Ctrl+C hasta
que llegan datos, y el agente puede pasar mucho tiempo pensando sin emitir
nada. Por eso la red se lee en un hilo aparte y el hilo principal espera en
una cola con un tiempo de espera corto: así ``KeyboardInterrupt`` llega al
instante y la respuesta se puede cancelar.
"""

from __future__ import annotations

import contextlib
import queue
import threading
from collections.abc import Callable, Iterator
from typing import Any

from .console import STYLE_ACCENT, STYLE_DIM, STYLE_ERROR, STYLE_OK, STYLE_WARN, Output
from .errors import STATUS_TO_CODE, ApiError
from .events import STATUS_LABELS, ChatAccumulator, ChatEvent, ChatResult, action_name

_CODE_TO_STATUS = {code: status for status, code in STATUS_TO_CODE.items()}
_END = object()


class _Failure:
    def __init__(self, exc: BaseException) -> None:
        self.exc = exc


class EventPump:
    """Lee un iterador de eventos en un hilo y los entrega en el hilo principal."""

    def __init__(self, events: Iterator[ChatEvent]) -> None:
        self._events = events
        self._queue: queue.Queue[Any] = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._worker, name="gmini-stream", daemon=True)

    def _worker(self) -> None:
        try:
            for event in self._events:
                if self._stop.is_set():
                    break
                self._queue.put(event)
        except BaseException as exc:  # se vuelve a lanzar en el hilo principal
            self._queue.put(_Failure(exc))
        finally:
            close = getattr(self._events, "close", None)
            if close is not None:
                with contextlib.suppress(Exception):
                    close()
            self._queue.put(_END)

    def run(self, on_event: Callable[[ChatEvent], None], *, poll: float = 0.1) -> None:
        """Entrega cada evento a ``on_event``; relanza errores y ``KeyboardInterrupt``."""
        self._thread.start()
        try:
            while True:
                try:
                    item = self._queue.get(timeout=poll)
                except queue.Empty:
                    continue
                if item is _END:
                    return
                if isinstance(item, _Failure):
                    raise item.exc
                on_event(item)
        except BaseException:
            self._stop.set()
            raise

    def wait(self, timeout: float) -> bool:
        """Espera a que el hilo lector termine; ``True`` si terminó."""
        self._thread.join(timeout)
        return not self._thread.is_alive()


def error_from_event(data: dict[str, Any]) -> ApiError:
    code = str(data.get("code") or "error")
    return ApiError(_CODE_TO_STATUS.get(code, 500), code, str(data.get("message") or ""))


class TurnRenderer:
    """Muestra una respuesta en curso: texto, acciones, estado y avisos.

    - ``live=True``: el texto se escribe en stdout a medida que llega (incluye
      lo que el agente dice entre acciones).
    - ``live=False``: solo se escribe la conclusión del turno (``reply``) al
      terminar; es lo que conviene cuando stdout es una tubería o un archivo.

    Acciones, avisos y el indicador de progreso van a stderr. Los errores no se
    imprimen aquí: quien llama decide si terminar el comando o seguir en el chat.
    """

    def __init__(
        self,
        out: Output,
        agent_name: str,
        *,
        show_prefix: bool = True,
        show_progress: bool = True,
        approval_hint: str = "",
        live: bool = True,
    ) -> None:
        self.out = out
        self.agent_name = agent_name
        self.show_prefix = show_prefix
        self.approval_hint = approval_hint
        self.live = live
        self.show_progress = show_progress and not out.json_mode
        self.accumulator = ChatAccumulator()
        self._spinner = out.spinner(f"{agent_name} está pensando...") if self.show_progress else None
        self._text_started = False
        self._line_open = False

    @property
    def result(self) -> ChatResult:
        return self.accumulator.result

    def _stop_spinner(self) -> None:
        if self._spinner is not None:
            self._spinner.stop()

    def _close_line(self) -> None:
        # Solo hay una línea abierta en modo en vivo: el progreso (stderr) se
        # intercala con el texto en la misma pantalla y debe ir en su propia línea.
        if self._line_open:
            self.out.write("\n")
            self._line_open = False

    def _progress(self, text: str, style: str) -> None:
        if not self.show_progress:
            return
        self._stop_spinner()
        self._close_line()
        self.out.progress(text, style)

    def _emit_text(self, text: str) -> None:
        # Con --json stdout queda reservado para el objeto final.
        if not text or self.out.json_mode:
            return
        self._stop_spinner()
        if not self._text_started and self.show_prefix:
            self.out.write_styled(f"{self.agent_name}> ", STYLE_ACCENT)
        self._text_started = True
        self.out.write(text)
        self._line_open = not text.endswith("\n")

    def on_event(self, event: ChatEvent) -> None:
        self.accumulator.feed(event)
        kind = event.type
        if kind == "chunk":
            if self.live:
                self._emit_text(event.text)
        elif kind == "state":
            status = str(event.data.get("status") or "")
            if self._spinner is not None and not self._text_started and status in STATUS_LABELS:
                self._spinner.update(f"{self.agent_name}: {STATUS_LABELS[status]}...")
        elif kind == "action":
            self._progress(f"  [acción] {action_name(event.data)}", STYLE_DIM)
        elif kind == "action_result":
            ok = event.data.get("success")
            label = "ok" if ok else "falló" if ok is False else "resultado"
            message = str(event.data.get("message") or action_name(event.data))
            self._progress(f"  [{label}] {message}".rstrip(), STYLE_OK if ok else STYLE_ERROR)
        elif kind == "notice":
            if event.text:
                self._progress(f"  [aviso] {event.text}", STYLE_DIM)
        elif kind == "notify":
            title = str(event.data.get("title") or "Aviso")
            body = str(event.data.get("body") or "")
            self._progress(f"  [aviso] {title}: {body}".rstrip(": "), STYLE_DIM)
        elif kind == "approval":
            if event.data.get("pending"):
                summary = str(event.data.get("summary") or "").strip()
                line = "  [aprobación] El agente espera tu aprobación"
                self._progress(f"{line}: {summary}" if summary else line + ".", STYLE_WARN)
                if self.approval_hint:
                    self._progress(f"  {self.approval_hint}", STYLE_DIM)
        elif kind == "done":
            if not self._text_started:
                self._emit_text(self.accumulator.result.reply)
                self._close_line()
            self.finish()
        elif kind == "error":
            self.finish()

    def finish(self) -> None:
        self._stop_spinner()
        self._close_line()

    def interrupted(self) -> None:
        self.finish()
        self._progress("  (respuesta cancelada)", STYLE_DIM)
