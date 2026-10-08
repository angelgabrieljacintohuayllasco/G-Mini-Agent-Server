"""``gmini status``, ``gmini devices``, ``gmini pair-code`` y las aprobaciones."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from ..client import ApiClient
from ..console import STYLE_DIM, STYLE_LABEL
from ..context import AppContext
from ..errors import EXIT_AUTH, EXIT_OK, ApiError, CliError
from ..events import STATUS_LABELS
from ..profiles import DEFAULT_PORT, is_loopback_url, url_host
from ..qr import can_encode, qr_matrix, render_ascii, render_half_blocks
from ..schedule import format_moment, relative_expiry

DEFAULT_PAIR_SCOPES = ("chat", "voice", "tasks")
VALID_SCOPES = ("chat", "voice", "tasks", "node")


# ── status ──────────────────────────────────────────────────────────────


def cmd_status(ctx: AppContext, args: argparse.Namespace) -> int:
    out = ctx.out
    target = ctx.resolve_target(need_token=False)
    exit_code = EXIT_OK
    me: dict[str, Any] | None = None
    state: dict[str, Any] | None = None
    auth_error: ApiError | None = None
    with ctx.client(target, need_token=False) as client:
        samples = []
        health: dict[str, Any] = {}
        for _ in range(3):
            health, elapsed = client.timed_health()
            samples.append(elapsed)
        latency = sorted(samples)[len(samples) // 2]
        if target.token:
            try:
                me = client.me()
            except ApiError as exc:
                if exc.exit_code != EXIT_AUTH:
                    raise
                auth_error, exit_code = exc, EXIT_AUTH
            if me is not None:
                try:
                    state = client.agent_state()
                except ApiError:
                    state = None
        elif health.get("requires_auth", True):
            exit_code = EXIT_AUTH

    if out.json_mode:
        out.json(
            {
                "profile": target.profile.name if target.profile else None,
                "url": target.url,
                "health": health,
                "latency_ms": round(latency, 1),
                "auth": {
                    "source": target.token_source,
                    "valid": me is not None,
                    "error": auth_error.to_json()["error"] if auth_error else None,
                },
                "me": me,
                "state": state,
            }
        )
        return exit_code

    profile_label = target.label
    if target.profile is not None and target.profile.implicit:
        profile_label += " (implícito)"
    mode = "servidor" if health.get("mode") == "server" else "escritorio"
    if me is not None:
        token_line = f"válido ({target.token_source})"
    elif auth_error is not None:
        token_line = f"rechazado: {auth_error.message}"
    elif target.token is None:
        token_line = "sin token"
    else:
        token_line = "-"
    pairs: list[tuple[str, Any]] = [
        ("Perfil", profile_label),
        ("URL", target.url),
        ("Agente", health.get("name")),
        ("Modo", mode),
        ("Versión", f"{health.get('version') or '?'} (protocolo {health.get('protocol', '?')})"),
        ("Latencia", f"{latency:.0f} ms"),
        ("Token", token_line),
    ]
    if me is not None:
        who = me.get("device_name") or ("sesión local" if me.get("kind") == "session" else me.get("kind"))
        pairs.append(("Dispositivo", f"{who} ({me.get('device_id')})" if me.get("device_id") else who))
        pairs.append(("Alcances", ", ".join(str(s) for s in me.get("scopes") or []) or "-"))
    if state is not None:
        status = STATUS_LABELS.get(str(state.get("status")), str(state.get("status") or "-"))
        details = [status, f"emoción {state.get('emotion') or '-'}"]
        if state.get("busy"):
            details.append("ocupado")
        if state.get("approval_pending"):
            details.append("aprobación pendiente")
        pairs.append(("Estado", " · ".join(details)))
    out.fields(pairs)
    if exit_code == EXIT_AUTH and auth_error is None:
        out.note("Sin token no se puede conversar. Empareja con: gmini pair <host> <código>")
    elif auth_error is not None and auth_error.hint:
        out.note(auth_error.hint)
    return exit_code


# ── devices ─────────────────────────────────────────────────────────────


def cmd_devices_list(ctx: AppContext, args: argparse.Namespace) -> int:
    out = ctx.out
    with ctx.client() as client:
        devices = client.devices()
    if out.json_mode:
        out.json({"items": devices})
        return EXIT_OK
    if not devices:
        out.line("No hay dispositivos ni tokens de API registrados.")
        return EXIT_OK
    rows = [
        [
            device.get("id") or device.get("device_id") or "-",
            device.get("name") or device.get("device_name") or "-",
            device.get("kind") or "device",
            device.get("device_type") or "-",
            device.get("platform") or "-",
            ", ".join(str(s) for s in device.get("scopes") or []) or "-",
            format_moment(device.get("last_seen_at")) if device.get("last_seen_at") else "nunca",
        ]
        for device in devices
    ]
    out.table(["ID", "Nombre", "Clase", "Tipo", "Plataforma", "Alcances", "Último uso"], rows)
    return EXIT_OK


def cmd_devices_revoke(ctx: AppContext, args: argparse.Namespace) -> int:
    out = ctx.out
    if not out.confirm(
        f"¿Revocar el dispositivo {args.device_id}? Su token dejará de funcionar.", assume_yes=args.yes
    ):
        out.line("Cancelado.")
        return EXIT_OK
    with ctx.client() as client:
        client.revoke_device(args.device_id)
    if out.json_mode:
        out.json({"revoked": args.device_id})
    else:
        out.success(f"Dispositivo {args.device_id} revocado.")
    return EXIT_OK


# ── pair-code ───────────────────────────────────────────────────────────


def tailscale_ipv4() -> str | None:
    """IP de Tailscale de esta máquina, si está instalado y conectado."""
    exe = shutil.which("tailscale")
    if not exe:
        return None
    try:
        completed = subprocess.run(
            [exe, "ip", "-4"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    first = completed.stdout.strip().splitlines()[:1]
    return first[0].strip() if completed.returncode == 0 and first else None


def rewrite_pair_link(link: str, host: str | None) -> str:
    """Cambia el host del enlace ``gmini://pair?...`` conservando el resto."""
    if not host:
        return link
    parts = urlsplit(link)
    query = {k: v[0] for k, v in parse_qs(parts.query).items() if v}
    query["host"] = host
    return f"{parts.scheme}://{parts.netloc}{parts.path}?{urlencode(query)}"


def pair_command_for(link: str, code: str) -> str:
    query = {k: v[0] for k, v in parse_qs(urlsplit(link).query).items() if v}
    host = query.get("host", "<host>")
    port = query.get("port")
    address = host if not port or port == str(DEFAULT_PORT) else f"{host}:{port}"
    return f"gmini pair {address} {code}"


def _print_qr(ctx: AppContext, payload: str) -> None:
    out = ctx.out
    matrix = qr_matrix(payload)
    if not can_encode(getattr(out.stdout, "encoding", None)):
        for line in render_ascii(matrix):
            out.line(line)
        return
    if out.plain or not out.stdout_is_tty():
        for line in render_half_blocks(matrix, draw_dark=False):
            out.line(line)
        return
    for line in render_half_blocks(matrix, draw_dark=True):
        out.line(line, "black on white")


LOOPBACK_LINK_WARNING = (
    "El enlace apunta a 127.0.0.1: en el otro equipo usa la IP de este servidor "
    "(por ejemplo la de Tailscale) o repite con --host <ip>."
)


def _reachable_alternative(ctx: AppContext, target_url: str, link: str) -> tuple[str | None, str | None]:
    """Host alcanzable desde otros equipos cuando el núcleo anunció 127.0.0.1.

    Devuelve ``(host, problema)``. Se prueba la IP de Tailscale de esta máquina
    contra /api/v1/health: si el núcleo solo escucha en 127.0.0.1 ningún otro
    equipo podría usar el enlace, y es mejor decirlo que anunciar una IP muerta.
    """
    if not is_loopback_url(target_url):
        return url_host(target_url), None
    candidate = tailscale_ipv4()
    if not candidate:
        return None, None
    port = parse_qs(urlsplit(link).query).get("port", [str(DEFAULT_PORT)])[0]
    try:
        with ApiClient(f"http://{candidate}:{port}", timeout=3.0) as probe:
            probe.health()
    except CliError:
        return None, (
            f"El núcleo no responde en la IP de Tailscale {candidate}:{port}; parece escuchar solo en "
            "127.0.0.1. Para emparejar otros equipos reinstálalo con --tailscale o --host."
        )
    ctx.out.note(f"El servidor anunció 127.0.0.1; uso la IP de Tailscale {candidate} para el otro equipo.")
    return candidate, None


def cmd_pair_code(ctx: AppContext, args: argparse.Namespace) -> int:
    out = ctx.out
    scopes = [s.strip() for s in (args.scopes or ",".join(DEFAULT_PAIR_SCOPES)).split(",") if s.strip()]
    unknown = [s for s in scopes if s not in VALID_SCOPES]
    if unknown:
        out.warn(f"Alcances desconocidos (el servidor los ignorará): {', '.join(unknown)}")
    target = ctx.resolve_target()
    with ctx.client(target) as client:
        data = client.create_pairing(label=args.label, device_type=args.type, scopes=scopes)
    code = str(data.get("code") or "")
    link = str(data.get("qr_payload") or "")
    advertised, problem = args.host, None
    link_host = parse_qs(urlsplit(link).query).get("host", [""])[0]
    if not advertised and link_host and is_loopback_url(f"http://{link_host}"):
        advertised, problem = _reachable_alternative(ctx, target.url, link)
    link = rewrite_pair_link(link, advertised) if link else link

    if out.json_mode:
        out.json({**data, "qr_payload": link, "command": pair_command_for(link, code) if link else None})
        return EXIT_OK
    expiry = relative_expiry(data.get("expires_at"))
    out.line(f"Código de emparejamiento: {code}" + (f"  ({expiry})" if expiry else ""), STYLE_LABEL)
    if link:
        out.line("En el otro equipo ejecuta:")
        out.line(f"  {pair_command_for(link, code)}")
        if not args.no_qr:
            out.line("")
            _print_qr(ctx, link)
        out.line(f"Enlace: {link}", STYLE_DIM)
        query_host = parse_qs(urlsplit(link).query).get("host", [""])[0]
        if is_loopback_url(f"http://{query_host}"):
            out.warn(problem or LOOPBACK_LINK_WARNING)
    out.note("El código sirve una sola vez y vence a los 5 minutos.")
    return EXIT_OK


# ── aprobaciones ────────────────────────────────────────────────────────


def _resolve(ctx: AppContext, approve: bool) -> int:
    out = ctx.out
    with ctx.client() as client:
        try:
            response = client.resolve_approval(approve)
        except ApiError as exc:
            if exc.code == "not_found":
                if out.json_mode:
                    out.json({"ok": False, "status": "none"})
                else:
                    out.line("No hay acciones esperando aprobación.")
                return EXIT_OK
            raise
    if out.json_mode:
        out.json(response)
    elif approve:
        out.success("Aprobado. El agente sigue con las acciones pendientes.")
    else:
        out.line("Rechazado. El agente no ejecutará esas acciones.")
    return EXIT_OK


def cmd_approve(ctx: AppContext, args: argparse.Namespace) -> int:
    return _resolve(ctx, True)


def cmd_reject(ctx: AppContext, args: argparse.Namespace) -> int:
    return _resolve(ctx, False)
