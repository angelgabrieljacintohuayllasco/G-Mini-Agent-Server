"""Salida de la CLI: rich cuando hay terminal, texto plano para scripts.

Reglas que siguen todos los comandos:

- Los datos van a stdout; avisos, errores y progreso van a stderr.
- Con ``--json`` stdout solo contiene JSON.
- Con ``--plain`` (o ``GMINI_PLAIN=1``) no se emiten colores ni cajas.
- El texto que llega del servidor nunca se interpreta como marcado de rich.
"""

from __future__ import annotations

import codecs
import contextlib
import json
import os
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any, TextIO

from rich.console import Console
from rich.table import Table
from rich.text import Text

from .errors import CliError, UsageError

LINE_ENDINGS = "\r\n"

STYLE_OK = "green"
STYLE_WARN = "yellow"
STYLE_ERROR = "bold red"
STYLE_DIM = "dim"
STYLE_LABEL = "bold"
STYLE_ACCENT = "bold cyan"


def configure_stdio() -> None:
    """Prepara stdin/stdout/stderr para UTF-8 en todos los sistemas.

    En una consola de Windows Python ya escribe Unicode con la API de la
    consola. Cuando la salida es una tubería o un archivo (Git Bash sin
    pseudo-consola, redirecciones, CI) se fuerza UTF-8 para no depender de la
    página de códigos ANSI. En cualquier caso, un carácter que no se pueda
    representar se reemplaza en lugar de abortar el comando.
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            if sys.platform == "win32" and not stream.isatty():
                stream.reconfigure(encoding="utf-8", errors="replace")
            else:
                stream.reconfigure(errors="replace")
        except (OSError, ValueError):
            pass


def legacy_encoding() -> str:
    """Codificación de respaldo para texto que no llega en UTF-8.

    En Windows, ``echo`` de cmd escribe en la página de códigos de la consola
    (850 en español), no en la ANSI que reporta ``locale``.
    """
    if sys.platform == "win32":
        try:
            import ctypes

            codepage = ctypes.windll.kernel32.GetConsoleOutputCP()
            if codepage:
                return f"cp{codepage}"
        except (OSError, AttributeError):
            pass
    import locale

    return locale.getpreferredencoding(False) or "latin-1"


def decode_input(raw: bytes) -> str:
    """UTF-8 (con o sin BOM) y, si no lo es, la codificación de la consola."""
    if raw.startswith(codecs.BOM_UTF8):
        raw = raw[len(codecs.BOM_UTF8) :]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode(legacy_encoding(), errors="replace")


def read_stdin_text() -> str:
    """Lee todo stdin como texto."""
    stream = sys.stdin
    if stream is None:
        return ""
    buffer = getattr(stream, "buffer", None)
    return decode_input(buffer.read()) if buffer is not None else stream.read()


def read_stdin_line() -> str | None:
    """Lee una línea de stdin sin el salto final; ``None`` al terminar la entrada."""
    stream = sys.stdin
    if stream is None:
        return None
    buffer = getattr(stream, "buffer", None)
    if buffer is None:
        line = stream.readline()
        return line.rstrip(LINE_ENDINGS) if line else None
    raw = buffer.readline()
    return decode_input(raw).rstrip(LINE_ENDINGS) if raw else None


def _spinner_name() -> str:
    # La consola clásica de Windows (conhost con Consolas) no tiene glifos Braille.
    if sys.platform == "win32" and not os.environ.get("WT_SESSION"):
        return "line"
    return "dots"


class _NullSpinner:
    def update(self, text: str) -> None:
        pass

    def stop(self) -> None:
        pass


class _RichSpinner:
    def __init__(self, console: Console, text: str) -> None:
        self._status = console.status(Text(text, style=STYLE_DIM), spinner=_spinner_name())
        self._status.start()
        self._active = True

    def update(self, text: str) -> None:
        if self._active:
            self._status.update(Text(text, style=STYLE_DIM))

    def stop(self) -> None:
        if self._active:
            self._status.stop()
            self._active = False


class Output:
    """Punto único de salida de la CLI."""

    def __init__(self, *, plain: bool = False, json_mode: bool = False, verbose: bool = False) -> None:
        env_plain = os.environ.get("GMINI_PLAIN", "").strip().lower() in {"1", "true", "yes", "si", "sí"}
        self.plain = plain or env_plain
        self.json_mode = json_mode
        self.verbose = verbose
        self._console: Console | None = None
        self._err_console: Console | None = None

    # ── Consolas ────────────────────────────────────────────────────────

    @property
    def stdout(self) -> TextIO:
        return sys.stdout

    @property
    def stderr(self) -> TextIO:
        return sys.stderr

    @staticmethod
    def _make_console(stream: TextIO) -> Console:
        # Sin terminal, rich asume 80 columnas y partiría las líneas: en una
        # tubería o un archivo el texto debe salir entero.
        try:
            tty = stream.isatty()
        except (AttributeError, ValueError):
            tty = False
        return Console(file=stream, highlight=False, emoji=False, soft_wrap=True, width=None if tty else 200)

    def _out(self) -> Console:
        if self._console is None or self._console.file is not sys.stdout:
            self._console = self._make_console(sys.stdout)
        return self._console

    def _err(self) -> Console:
        if self._err_console is None or self._err_console.file is not sys.stderr:
            self._err_console = self._make_console(sys.stderr)
        return self._err_console

    def stdout_is_tty(self) -> bool:
        try:
            return sys.stdout.isatty()
        except (AttributeError, ValueError):
            return False

    def stderr_is_tty(self) -> bool:
        try:
            return sys.stderr.isatty()
        except (AttributeError, ValueError):
            return False

    @staticmethod
    def stdin_is_tty() -> bool:
        try:
            return sys.stdin is not None and sys.stdin.isatty()
        except (AttributeError, ValueError):
            return False

    # ── Texto ───────────────────────────────────────────────────────────

    def line(self, text: str = "", style: str | None = None) -> None:
        if self.plain:
            print(text, file=sys.stdout)
        else:
            self._out().print(Text(text, style=style or ""))

    def success(self, text: str) -> None:
        self.line(text, STYLE_OK)

    @staticmethod
    def _flush_stdout() -> None:
        # Con stdout en una tubería Python lo acumula; vaciarlo antes de
        # escribir en stderr mantiene el orden cuando ambos van al mismo sitio.
        with contextlib.suppress(OSError, ValueError, AttributeError):
            sys.stdout.flush()

    def _stderr_text(self, plain_text: str, rich_text: Text) -> None:
        self._flush_stdout()
        if self.plain or not self.stderr_is_tty():
            print(plain_text, file=sys.stderr, flush=True)
        else:
            self._err().print(rich_text)

    def note(self, text: str) -> None:
        """Mensaje informativo secundario (stderr)."""
        self._stderr_text(text, Text(text, style=STYLE_DIM))

    def warn(self, text: str) -> None:
        self._stderr_text(f"Aviso: {text}", Text.assemble(("Aviso: ", STYLE_WARN), text))

    def error(self, message: str, hint: str | None = None) -> None:
        self._stderr_text(f"Error: {message}", Text.assemble(("Error: ", STYLE_ERROR), message))
        if hint:
            self._stderr_text(f"  {hint}", Text(f"  {hint}", style=STYLE_DIM))

    def report(self, exc: CliError) -> None:
        if self.json_mode:
            self._flush_stdout()
            print(json.dumps(exc.to_json(), ensure_ascii=False), file=sys.stderr, flush=True)
        else:
            self.error(exc.message, exc.hint)

    def progress(self, text: str, style: str = STYLE_DIM) -> None:
        """Línea de progreso en stderr (acciones del agente, avisos)."""
        self._stderr_text(text, Text(text, style=style))

    def debug(self, text: str) -> None:
        if self.verbose:
            self._flush_stdout()
            print(f"[debug] {text}", file=sys.stderr, flush=True)

    def step(self, index: int, total: int, text: str) -> None:
        prefix = f"[{index}/{total}] "
        if self.plain:
            print(prefix + text, file=sys.stdout, flush=True)
        else:
            self._out().print(Text.assemble((prefix, STYLE_ACCENT), (text, STYLE_LABEL)))

    def clear(self) -> None:
        if not self.plain and self.stdout_is_tty():
            self._out().clear()

    def write(self, text: str) -> None:
        """Escribe texto sin salto de línea y vacía el búfer (streaming)."""
        sys.stdout.write(text)
        sys.stdout.flush()

    def write_styled(self, text: str, style: str) -> None:
        if self.plain or not self.stdout_is_tty():
            self.write(text)
        else:
            self._out().print(Text(text, style=style), end="")
            sys.stdout.flush()

    def json(self, payload: Any) -> None:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str), file=sys.stdout)

    # ── Estructuras ─────────────────────────────────────────────────────

    def table(
        self, headers: Sequence[str], rows: Sequence[Sequence[Any]], *, title: str | None = None
    ) -> None:
        cells = [["" if value is None else str(value) for value in row] for row in rows]
        if self.plain:
            if title:
                print(title, file=sys.stdout)
            widths = [len(h) for h in headers]
            for row in cells:
                for i, value in enumerate(row):
                    widths[i] = max(widths[i], len(value))
            for row in [list(headers), *cells]:
                print("  ".join(v.ljust(widths[i]) for i, v in enumerate(row)).rstrip(), file=sys.stdout)
            return
        table = Table(
            title=title,
            title_justify="left",
            header_style=STYLE_LABEL,
            show_edge=False,
            box=None,
            pad_edge=False,
            padding=(0, 2, 0, 0),
        )
        for header in headers:
            table.add_column(header, overflow="fold")
        for row in cells:
            table.add_row(*[Text(v) for v in row])
        self._out().print(table)

    def fields(self, pairs: Sequence[tuple[str, Any]], *, title: str | None = None) -> None:
        """Bloque ``clave  valor`` alineado."""
        visible = [(k, "-" if v is None or v == "" else str(v)) for k, v in pairs]
        width = max((len(k) for k, _ in visible), default=0)
        if title:
            self.line(title, STYLE_LABEL)
        for key, value in visible:
            if self.plain:
                print(f"{key.ljust(width)}  {value}", file=sys.stdout)
            else:
                self._out().print(Text.assemble((key.ljust(width), STYLE_DIM), "  ", value))

    # ── Interacción ─────────────────────────────────────────────────────

    def spinner(self, text: str) -> _RichSpinner | _NullSpinner:
        if self.plain or self.json_mode or not self.stderr_is_tty():
            return _NullSpinner()
        return _RichSpinner(self._err(), text)

    @contextmanager
    def spinning(self, text: str) -> Iterator[_RichSpinner | _NullSpinner]:
        spinner = self.spinner(text)
        try:
            yield spinner
        finally:
            spinner.stop()

    def confirm(self, question: str, *, assume_yes: bool = False, default: bool = False) -> bool:
        """Pregunta sí/no. Sin terminal interactiva exige ``--yes``."""
        if assume_yes:
            return True
        if not self.stdin_is_tty():
            raise UsageError(
                "Esta acción necesita confirmación y no hay una terminal interactiva.",
                hint="Repite el comando con --yes para confirmarla.",
            )
        suffix = " [S/n] " if default else " [s/N] "
        try:
            answer = input(question + suffix).strip().lower()
        except EOFError:
            return False
        if not answer:
            return default
        return answer in {"s", "si", "sí", "y", "yes"}
