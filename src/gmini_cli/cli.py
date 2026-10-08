"""Punto de entrada de ``gmini``: árbol de comandos y manejo de errores."""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from collections.abc import Sequence

from . import PROTOCOL_VERSION, __version__, _argparse_es
from .chat import TRANSPORTS
from .commands import admin, chat, connection, tasks, voice
from .console import Output, configure_stdio
from .context import AppContext
from .errors import EXIT_ERROR, EXIT_INTERRUPTED, EXIT_USAGE, CliError

DESCRIPTION = "CLI de G-Mini Agent: conversa con el agente y gestiona sus servidores."

EPILOG = """\
ejemplos:
  gmini pair 100.71.131.70 482913      emparejar este equipo con un servidor
  gmini chat                           chat interactivo con el perfil activo
  gmini ask "resume mis pendientes"    una pregunta, respuesta por stdout
  gmini task add "revisa el correo" --cron "0 8 * * *" --tz America/Lima
  gmini status                         salud del servidor, versión y latencia

Variables de entorno: GMINI_PROFILE, GMINI_URL, GMINI_TOKEN, GMINI_TOKEN_FILE,
GMINI_PLAIN, GMINI_CONFIG_DIR y GMINI_CREDENTIAL_STORE (auto, keyring, file).
Documentación: https://github.com/angelgabrieljacintohuayllasco/G-Mini-Agent-Server
"""


class _Formatter(argparse.RawDescriptionHelpFormatter):
    def __init__(self, prog: str) -> None:
        super().__init__(prog, max_help_position=34, width=100)


def _add_global_options(parser: argparse.ArgumentParser, *, suppress: bool) -> None:
    """Opciones válidas antes o después del subcomando.

    En los subcomandos el valor por defecto es SUPPRESS para no pisar lo que
    se haya indicado antes del subcomando.
    """

    def default(value: object) -> object:
        return argparse.SUPPRESS if suppress else value

    group = parser.add_argument_group("opciones globales")
    group.add_argument(
        "-p",
        "--profile",
        metavar="PERFIL",
        default=default(None),
        help="perfil a usar en lugar del activo (GMINI_PROFILE)",
    )
    group.add_argument(
        "--url", metavar="URL", default=default(None), help="servidor a usar sin perfil (GMINI_URL)"
    )
    group.add_argument(
        "--token", metavar="TOKEN", default=default(None), help="token para esta llamada (GMINI_TOKEN)"
    )
    group.add_argument(
        "--token-file",
        metavar="ARCHIVO",
        default=default(None),
        help="archivo con el token (GMINI_TOKEN_FILE)",
    )
    group.add_argument(
        "--timeout",
        metavar="SEG",
        type=float,
        default=default(None),
        help="límite de espera de cada petición (30 s)",
    )
    group.add_argument(
        "--plain",
        action="store_true",
        default=default(False),
        help="salida sin colores ni decoración (GMINI_PLAIN=1)",
    )
    group.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=default(False),
        help="muestra las peticiones y detalles de depuración",
    )


def _json_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="salida en JSON")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gmini", description=DESCRIPTION, epilog=EPILOG, formatter_class=_Formatter
    )
    parser.add_argument(
        "--version", action="version", version=f"gmini {__version__} (API remota v{PROTOCOL_VERSION})"
    )
    _add_global_options(parser, suppress=False)

    common = argparse.ArgumentParser(add_help=False)
    _add_global_options(common, suppress=True)
    sub = parser.add_subparsers(title="comandos", metavar="<comando>")

    def command(
        name: str,
        help_text: str,
        handler: object,
        *,
        aliases: Sequence[str] = (),
        parent: argparse._SubParsersAction | None = None,
    ) -> argparse.ArgumentParser:
        target = parent or sub
        cmd = target.add_parser(
            name,
            help=help_text,
            description=help_text,
            aliases=list(aliases),
            parents=[common],
            formatter_class=_Formatter,
        )
        cmd.set_defaults(handler=handler)
        return cmd

    # Conexión
    p = command(
        "connect",
        "guarda un servidor como perfil (sin URL: el núcleo de esta máquina)",
        connection.cmd_connect,
    )
    p.add_argument(
        "address", nargs="?", metavar="URL", help="dirección del servidor (127.0.0.1:8765 si se omite)"
    )
    p.add_argument("--name", help="nombre del perfil")
    p.add_argument("--api-token", metavar="TOKEN", help="guarda un token de API para este perfil")
    p.add_argument(
        "--api-token-stdin", action="store_true", help="lee el token de API por la entrada estándar"
    )
    p.add_argument(
        "--home",
        metavar="CARPETA",
        help="carpeta de datos del núcleo (GMINI_HOME) para leer su token de sesión",
    )
    p.add_argument("--force", action="store_true", help="guarda el perfil aunque el servidor no responda")
    p.add_argument("--no-use", action="store_true", help="no lo deja como perfil activo")
    _json_flag(p)

    p = command(
        "pair", "empareja este equipo con un servidor usando un código de 6 dígitos", connection.cmd_pair
    )
    p.add_argument("host", help="IP, nombre o URL del servidor, o el enlace gmini://pair?... del QR")
    p.add_argument("code", nargs="?", help="código de emparejamiento")
    p.add_argument("--name", help="nombre del perfil (por defecto el nombre del servidor)")
    p.add_argument("--device-name", help="cómo se verá este equipo en el servidor")
    _json_flag(p)

    p = command("profiles", "perfiles guardados", None, aliases=["profile"])
    profiles = p.add_subparsers(title="acciones", metavar="<acción>")
    q = command("list", "lista los perfiles", connection.cmd_profiles_list, aliases=["ls"], parent=profiles)
    _json_flag(q)
    p.set_defaults(handler=connection.cmd_profiles_list, json=False)
    q = command("use", "cambia el perfil activo", connection.cmd_profiles_use, parent=profiles)
    q.add_argument("name", help="nombre del perfil")
    _json_flag(q)
    q = command(
        "remove",
        "elimina un perfil y su token",
        connection.cmd_profiles_remove,
        aliases=["rm"],
        parent=profiles,
    )
    q.add_argument("name", help="nombre del perfil")
    q.add_argument("-y", "--yes", action="store_true", help="no pide confirmación")
    _json_flag(q)

    p = command("status", "salud del servidor, versión, latencia y validez del token", admin.cmd_status)
    _json_flag(p)

    # Chat
    def chat_options(cmd: argparse.ArgumentParser) -> None:
        cmd.add_argument(
            "--transport",
            choices=TRANSPORTS,
            default="sse",
            help="transporte del streaming (sse por defecto)",
        )
        cmd.add_argument(
            "--no-stream", action="store_true", help="espera la respuesta completa (sin streaming)"
        )
        cmd.add_argument(
            "-a", "--attach", action="append", metavar="ARCHIVO", help="adjunta un archivo (repetible)"
        )
        cmd.add_argument(
            "-q", "--quiet", action="store_true", help="no muestra acciones ni avisos del agente"
        )
        _json_flag(cmd)

    p = command("chat", "chat interactivo; con un mensaje responde una vez y termina", chat.cmd_chat)
    p.add_argument("message", nargs="*", help="mensaje para una sola respuesta ('-' lee la entrada estándar)")
    chat_options(p)

    p = command("ask", "envía un mensaje y escribe la respuesta (apto para tuberías)", chat.cmd_ask)
    p.add_argument("message", nargs="*", help="mensaje; '-' o una tubería lo leen de la entrada estándar")
    chat_options(p)

    p = command("approve", "aprueba las acciones que el agente dejó pendientes", admin.cmd_approve)
    _json_flag(p)
    p = command("reject", "rechaza las acciones pendientes", admin.cmd_reject)
    _json_flag(p)

    # Tareas
    p = command("task", "tareas en segundo plano del servidor", None, aliases=["tasks"])
    task_sub = p.add_subparsers(title="acciones", metavar="<acción>")
    p.set_defaults(handler=tasks.cmd_task_list, status=None, json=False)
    q = command("add", "encola una tarea, ahora o programada", tasks.cmd_task_add, parent=task_sub)
    q.add_argument("prompt", nargs="+", help="lo que el agente debe hacer")
    q.add_argument("--title", help="título corto")
    when = q.add_mutually_exclusive_group()
    when.add_argument("--cron", metavar="EXPR", help='programación cron de 5 campos, p. ej. "0 8 * * *"')
    when.add_argument(
        "--every", metavar="DUR", help="repetir cada cierto tiempo: 3600, 90m, 2h, 1d (mínimo 60 s)"
    )
    when.add_argument(
        "--at", metavar="FECHA", help="una vez en una fecha ISO 8601 (el núcleo v0.2 aún no lo admite)"
    )
    q.add_argument(
        "--tz", metavar="ZONA", help="zona horaria IANA para --cron (por defecto la de este equipo)"
    )
    q.add_argument(
        "--notify",
        action="append",
        metavar="CANAL:DESTINO",
        help="avisar al terminar, p. ej. telegram:123456789 (repetible)",
    )
    _json_flag(q)
    q = command("list", "lista las tareas", tasks.cmd_task_list, aliases=["ls"], parent=task_sub)
    q.add_argument("--status", choices=tasks.STATUSES, help="filtra por estado")
    _json_flag(q)
    q = command("show", "detalle y resultado de una tarea", tasks.cmd_task_show, parent=task_sub)
    q.add_argument("task_id", metavar="ID", help="identificador de la tarea")
    _json_flag(q)
    q = command("cancel", "cancela una tarea y su programación", tasks.cmd_task_cancel, parent=task_sub)
    q.add_argument("task_id", metavar="ID", help="identificador de la tarea")
    _json_flag(q)

    # Voz
    p = command("say", "el agente dice un texto (TTS); se reproduce o se guarda en WAV", voice.cmd_say)
    p.add_argument("text", nargs="*", help="texto a decir ('-' lee la entrada estándar)")
    p.add_argument("-o", "--out", metavar="ARCHIVO", help="guarda el audio ('-' lo escribe en stdout)")
    p.add_argument("--voice", help="voz a usar (por defecto la del servidor)")
    p.add_argument("--play", action="store_true", help="con --out, además lo reproduce")
    _json_flag(p)

    p = command(
        "transcribe",
        "convierte un audio WAV en texto (STT del servidor)",
        voice.cmd_transcribe,
        aliases=["stt"],
    )
    p.add_argument("file", metavar="ARCHIVO", help="audio WAV (recomendado 16 kHz mono)")
    _json_flag(p)

    p = command(
        "wake", 'detecta la palabra de activación ("Oye G-Mini") en un clip WAV corto', voice.cmd_wake
    )
    p.add_argument("file", metavar="ARCHIVO", help="audio WAV de menos de 4 s")
    _json_flag(p)

    # Administración (requiere el alcance admin: token de sesión o de API)
    p = command("devices", "dispositivos emparejados (requiere permiso admin)", None, aliases=["device"])
    dev_sub = p.add_subparsers(title="acciones", metavar="<acción>")
    p.set_defaults(handler=admin.cmd_devices_list, json=False)
    q = command(
        "list", "lista dispositivos y tokens de API", admin.cmd_devices_list, aliases=["ls"], parent=dev_sub
    )
    _json_flag(q)
    q = command("revoke", "revoca un dispositivo", admin.cmd_devices_revoke, parent=dev_sub)
    q.add_argument("device_id", metavar="ID", help="identificador del dispositivo")
    q.add_argument("-y", "--yes", action="store_true", help="no pide confirmación")
    _json_flag(q)

    p = command(
        "pair-code", "genera un código de emparejamiento con QR (requiere permiso admin)", admin.cmd_pair_code
    )
    p.add_argument("--label", default="Emparejado desde gmini", help="etiqueta del código")
    p.add_argument("--type", default="cli", help="tipo de dispositivo: cli, pc, esp32, rpi... (cli)")
    p.add_argument("--scopes", help="alcances separados por comas (chat,voice,tasks)")
    p.add_argument("--host", help="host a anunciar en el enlace y el QR (p. ej. la IP de Tailscale)")
    p.add_argument("--no-qr", action="store_true", help="no dibuja el código QR")
    _json_flag(p)

    return parser


def _handle_broken_pipe() -> None:
    # La salida se cortó (p. ej. `gmini ask ... | head -1`): no mostrar trazas al cerrar.
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
    except (OSError, ValueError):
        pass


def main(argv: Sequence[str] | None = None) -> int:
    configure_stdio()
    _argparse_es.install()
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return EXIT_USAGE
    out = Output(
        plain=bool(args.plain), json_mode=bool(getattr(args, "json", False)), verbose=bool(args.verbose)
    )
    ctx = AppContext(args, out)
    try:
        return int(handler(ctx, args) or 0)
    except CliError as exc:
        out.report(exc)
        return exc.exit_code
    except KeyboardInterrupt:
        out.note("Cancelado.")
        return EXIT_INTERRUPTED
    except BrokenPipeError:
        _handle_broken_pipe()
        return EXIT_ERROR
    except Exception as exc:  # error inesperado: mensaje corto, traza con --verbose
        if out.verbose:
            traceback.print_exc()
        out.error(
            f"Error inesperado: {type(exc).__name__}: {exc}",
            hint=None if out.verbose else "Repite con --verbose para ver el detalle y repórtalo en GitHub.",
        )
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
