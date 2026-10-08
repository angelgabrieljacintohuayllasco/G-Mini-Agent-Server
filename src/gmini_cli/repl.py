"""Chat interactivo: ``gmini chat``.

Con una terminal usa prompt_toolkit (historial, edición, búsqueda) si está
instalado y, si no, readline o ``input``. Con stdin redirigido lee una línea
por mensaje, así que también sirve en scripts::

    printf 'hola\\n/estado\\n' | gmini chat
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .chat import ChatSession, run_turn, wait_until_idle
from .client import ApiClient
from .console import STYLE_ACCENT, STYLE_DIM, Output, read_stdin_line
from .errors import ApiError, CliError
from .events import STATUS_LABELS
from .schedule import format_moment
from .stream import TurnRenderer, error_from_event

PROMPT = "tú> "
APPROVAL_WAIT_SECONDS = 300

SLASH_COMMANDS = [
    ("/ayuda", "muestra esta ayuda"),
    ("/sesiones", "lista las conversaciones recientes"),
    ("/historial [n]", "muestra los últimos mensajes de la conversación"),
    ("/estado", "estado del agente (ocupado, emoción, aprobaciones)"),
    ("/aprobar", "aprueba las acciones que el agente dejó pendientes"),
    ("/rechazar", "rechaza las acciones pendientes"),
    ("/limpiar", "limpia la pantalla"),
    ("/salir", "termina el chat (también Ctrl+D; en Windows Ctrl+Z y Enter)"),
]


class LineReader:
    """Lee líneas con la mejor herramienta disponible."""

    def __init__(self, history_path: Path, *, interactive: bool) -> None:
        self.interactive = interactive
        self._prompt_session: Any = None
        self._readline: Any = None
        self._history_path = history_path
        if not interactive:
            return
        try:
            history_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        try:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.history import FileHistory

            self._prompt_session = PromptSession(history=FileHistory(str(history_path)))
        except Exception:  # sin prompt_toolkit o sin consola compatible (mintty sin pseudo-consola)
            self._prompt_session = None
            self._setup_readline()

    def _setup_readline(self) -> None:
        try:
            import readline
        except ImportError:
            return
        self._readline = readline
        with contextlib.suppress(OSError, AttributeError):
            readline.read_history_file(str(self._history_path))
        readline.set_history_length(1000)

    def read(self, prompt: str) -> str:
        if not self.interactive:
            line = read_stdin_line()
            if line is None:
                raise EOFError
            return line
        if self._prompt_session is not None:
            try:
                return self._prompt_session.prompt(prompt)
            except (KeyboardInterrupt, EOFError):
                raise
            except Exception:
                # Algunas terminales fallan recién al leer; se sigue con input().
                self._prompt_session = None
                self._setup_readline()
        return input(prompt)

    def close(self) -> None:
        if self._readline is not None:
            with contextlib.suppress(OSError):
                self._readline.write_history_file(str(self._history_path))


class ChatRepl:
    def __init__(
        self, out: Output, client: ApiClient, session: ChatSession, *, agent_name: str, reader: LineReader
    ) -> None:
        self.out = out
        self.client = client
        self.session = session
        self.agent_name = agent_name
        self.reader = reader
        self.session_id: str | None = None
        self.commands: dict[str, Callable[[list[str]], bool]] = {
            "/ayuda": self._help,
            "/help": self._help,
            "/sesiones": self._sessions,
            "/historial": self._history,
            "/estado": self._state,
            "/aprobar": self._approve,
            "/rechazar": self._reject,
            "/limpiar": self._clear,
            "/salir": self._quit,
            "/exit": self._quit,
            "/quit": self._quit,
        }

    # ── Bucle principal ─────────────────────────────────────────────────

    def run(self) -> int:
        try:
            while True:
                try:
                    line = self.reader.read(PROMPT)
                except KeyboardInterrupt:
                    self.out.note("(usa /salir o Ctrl+D para terminar)")
                    continue
                except EOFError:
                    if self.reader.interactive:
                        self.out.write("\n")
                    break
                text = line.strip()
                if not text:
                    continue
                if not self.reader.interactive:
                    self.out.line(PROMPT + text, STYLE_DIM)
                if text.startswith("/"):
                    if not self._slash(text):
                        break
                    continue
                self._turn(text)
        finally:
            self.session.close()
            self.reader.close()
        return 0

    def _turn(self, text: str) -> None:
        renderer = TurnRenderer(
            self.out,
            self.agent_name,
            show_prefix=True,
            live=True,
            approval_hint="Responde con /aprobar o /rechazar.",
        )
        try:
            result, interrupted = run_turn(self.session, renderer, text)
        except CliError as exc:
            self.out.report(exc)
            return
        if result.session_id:
            self.session_id = result.session_id
        if interrupted:
            wait_until_idle(self.client, timeout=5.0, poll=0.5)
            return
        if result.error:
            self.out.report(error_from_event(result.error))

    def _slash(self, text: str) -> bool:
        name, *rest = text.split()
        handler = self.commands.get(name.lower())
        if handler is None:
            self.out.warn(f"Comando desconocido: {name}. Escribe /ayuda para ver la lista.")
            return True
        try:
            return handler(rest)
        except CliError as exc:
            self.out.report(exc)
            return True

    # ── Comandos ────────────────────────────────────────────────────────

    def _help(self, _args: list[str]) -> bool:
        width = max(len(cmd) for cmd, _ in SLASH_COMMANDS)
        for cmd, description in SLASH_COMMANDS:
            self.out.line(f"  {cmd.ljust(width)}  {description}")
        self.out.line("  Ctrl+C cancela la respuesta en curso.", STYLE_DIM)
        return True

    def _quit(self, _args: list[str]) -> bool:
        return False

    def _clear(self, _args: list[str]) -> bool:
        self.out.clear()
        return True

    def _sessions(self, _args: list[str]) -> bool:
        items = self.client.sessions(limit=20)
        if not items:
            self.out.line("No hay conversaciones guardadas.")
            return True
        rows = [
            [
                str(i),
                item.get("id", "-"),
                item.get("title") or "(sin título)",
                format_moment(item.get("updated_at")),
                item.get("message_count", "-"),
            ]
            for i, item in enumerate(items, start=1)
        ]
        self.out.table(["#", "ID", "Título", "Actualizada", "Mensajes"], rows)
        return True

    def _current_session(self) -> str | None:
        if self.session_id:
            return self.session_id
        items = self.client.sessions(limit=1)
        return str(items[0].get("id")) if items and items[0].get("id") else None

    def _history(self, args: list[str]) -> bool:
        limit = int(args[0]) if args and args[0].isdigit() else 10
        session_id = self._current_session()
        if not session_id:
            self.out.line("Todavía no hay una conversación.")
            return True
        messages = self.client.session_messages(session_id, limit=limit)
        if not messages:
            self.out.line("La conversación no tiene mensajes.")
            return True
        for message in messages:
            role = str(message.get("role") or "")
            who = self.agent_name if role == "assistant" else "tú" if role == "user" else role or "?"
            self.out.line(f"  [{format_moment(message.get('created_at'))}] {who}:", STYLE_DIM)
            for text_line in str(message.get("content") or "").strip().splitlines() or [""]:
                self.out.line(f"    {text_line}")
        return True

    def _state(self, _args: list[str]) -> bool:
        state = self.client.agent_state()
        status = STATUS_LABELS.get(str(state.get("status")), str(state.get("status") or "-"))
        self.out.fields(
            [
                ("Estado", status),
                ("Emoción", state.get("emotion")),
                ("Ocupado", "sí" if state.get("busy") else "no"),
                ("Aprobación pendiente", "sí" if state.get("approval_pending") else "no"),
            ]
        )
        return True

    def _last_assistant_message(self, session_id: str | None) -> tuple[Any, str] | None:
        if not session_id:
            return None
        for message in reversed(self.client.session_messages(session_id, limit=5)):
            if message.get("role") == "assistant":
                return message.get("created_at"), str(message.get("content") or "")
        return None

    def _approve(self, _args: list[str]) -> bool:
        session_id = self._current_session()
        before = self._last_assistant_message(session_id)
        try:
            self.client.resolve_approval(True)
        except ApiError as exc:
            if exc.code == "not_found":
                self.out.line("No hay acciones esperando aprobación.")
                return True
            raise
        self.out.success("Aprobado. El agente sigue con las acciones pendientes.")
        try:
            with self.out.spinning("Esperando a que el agente termine..."):
                finished = wait_until_idle(self.client, timeout=APPROVAL_WAIT_SECONDS)
        except KeyboardInterrupt:
            self.out.note("(dejé de esperar; el agente sigue trabajando)")
            return True
        if not finished:
            self.out.note("El agente sigue trabajando; revisa luego con /historial.")
            return True
        after = self._last_assistant_message(session_id)
        if after and after != before:
            self.out.write_styled(f"{self.agent_name}> ", STYLE_ACCENT)
            self.out.write(after[1].strip() + "\n")
        return True

    def _reject(self, _args: list[str]) -> bool:
        try:
            self.client.resolve_approval(False)
        except ApiError as exc:
            if exc.code == "not_found":
                self.out.line("No hay acciones esperando aprobación.")
                return True
            raise
        self.out.line("Rechazado. El agente no ejecutará esas acciones.")
        return True
