"""``gmini chat`` (interactivo o de una sola vez) y ``gmini ask``."""

from __future__ import annotations

import argparse
from pathlib import Path

from .. import paths
from ..chat import ChatSession, run_turn
from ..client import build_attachment
from ..console import STYLE_DIM, read_stdin_text
from ..context import AppContext, device_name_default
from ..errors import EXIT_INTERRUPTED, EXIT_OK, UsageError
from ..events import events_from_reply
from ..repl import ChatRepl, LineReader
from ..stream import TurnRenderer, error_from_event


def _message_from(args: argparse.Namespace, ctx: AppContext, *, required: bool) -> str | None:
    parts = [p for p in (args.message or []) if p != "-"]
    text = " ".join(parts).strip()
    piped = not ctx.out.stdin_is_tty() and (not parts or "-" in (args.message or []))
    if piped:
        extra = read_stdin_text().strip()
        text = f"{text}\n\n{extra}".strip() if text else extra
    if not text and required:
        raise UsageError("Falta el mensaje.", hint='Ejemplo: gmini ask "¿qué tareas tengo hoy?"')
    return text or None


def one_shot(ctx: AppContext, args: argparse.Namespace, message: str) -> int:
    out = ctx.out
    target = ctx.resolve_target()
    attachments = [build_attachment(Path(p)) for p in (getattr(args, "attach", None) or [])]
    live = out.stdout_is_tty() and not out.json_mode
    renderer = TurnRenderer(
        out,
        ctx.agent_label(target),
        show_prefix=False,
        live=live,
        show_progress=not getattr(args, "quiet", False),
        approval_hint="Responde con 'gmini approve' o 'gmini reject'.",
    )
    with ctx.client(target) as client:
        if getattr(args, "no_stream", False):
            with out.spinning("Esperando la respuesta..."):
                payload = client.chat(message, attachments=attachments)
            for event in events_from_reply(payload):
                renderer.on_event(event)
            result = renderer.result
        else:
            session = ChatSession(
                client, transport=args.transport, device_name=device_name_default(), out=out
            )
            try:
                result, interrupted = run_turn(session, renderer, message, attachments)
            finally:
                session.close()
            if interrupted:
                return EXIT_INTERRUPTED
    if out.json_mode:
        out.json(result.to_json())
        return error_from_event(result.error).exit_code if result.error else EXIT_OK
    if result.error:
        raise error_from_event(result.error)
    return EXIT_OK


def cmd_ask(ctx: AppContext, args: argparse.Namespace) -> int:
    message = _message_from(args, ctx, required=True)
    assert message is not None
    return one_shot(ctx, args, message)


def cmd_chat(ctx: AppContext, args: argparse.Namespace) -> int:
    out = ctx.out
    if args.message:
        message = _message_from(args, ctx, required=True)
        assert message is not None
        return one_shot(ctx, args, message)
    if out.json_mode:
        raise UsageError('--json solo sirve con un mensaje: gmini chat "texto" --json')

    target = ctx.resolve_target()
    client = ctx.client(target)
    try:
        health = client.health()
        agent = str(health.get("name") or ctx.agent_label(target))
        mode = "servidor" if health.get("mode") == "server" else "escritorio"
        interactive = out.stdin_is_tty()
        if interactive:
            out.line(f"{agent} en {target.label} ({target.url}, modo {mode}).", STYLE_DIM)
            out.line("Escribe /ayuda para ver los comandos. Ctrl+C cancela una respuesta.", STYLE_DIM)
        session = ChatSession(client, transport=args.transport, device_name=device_name_default(), out=out)
        reader = LineReader(paths.history_file(), interactive=interactive)
        return ChatRepl(out, client, session, agent_name=agent, reader=reader).run()
    finally:
        client.close()
