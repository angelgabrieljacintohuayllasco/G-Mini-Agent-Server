"""``gmini connect``, ``gmini pair`` y ``gmini profiles``."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

from .. import PROTOCOL_VERSION, paths
from ..client import ApiClient
from ..console import read_stdin_text
from ..context import AppContext, device_name_default
from ..errors import EXIT_OK, ApiError, CliError, UsageError
from ..profiles import (
    AUTH_NONE,
    AUTH_SESSION,
    AUTH_STORED,
    LOCAL_PROFILE,
    LOCAL_URL,
    Profile,
    default_profile_name,
    implicit_local_profile,
    is_loopback_url,
    normalize_url,
    parse_pairing_link,
    validate_profile_name,
)

_CODE_SEPARATORS = re.compile(r"[\s\-.]")
AUTH_LABELS = {
    AUTH_STORED: "token guardado",
    AUTH_SESSION: "sesión local",
    AUTH_NONE: "sin token",
}


def _check_protocol(ctx: AppContext, health: dict[str, Any]) -> None:
    protocol = health.get("protocol")
    if isinstance(protocol, int) and protocol > PROTOCOL_VERSION:
        ctx.out.warn(
            f"El servidor usa el protocolo {protocol} y esta CLI el {PROTOCOL_VERSION}; "
            "actualiza gmini si algo no funciona."
        )


def normalize_code(raw: str | None) -> str:
    code = _CODE_SEPARATORS.sub("", raw or "")
    if not code:
        raise UsageError(
            "Falta el código de emparejamiento.",
            hint="Pídelo en el servidor con 'gmini pair-code' o en Ajustes > Dispositivos.",
        )
    if not code.isdigit():
        raise UsageError(f"El código debe tener solo números: {raw!r}")
    return code


# ── connect ─────────────────────────────────────────────────────────────


def cmd_connect(ctx: AppContext, args: argparse.Namespace) -> int:
    out = ctx.out
    url = normalize_url(args.address or LOCAL_URL)
    name = validate_profile_name(args.name) if args.name else default_profile_name(url)

    api_token: str | None = None
    if args.api_token_stdin:
        api_token = read_stdin_text().strip() or None
        if not api_token:
            raise UsageError("No llegó ningún token por la entrada estándar.")
    elif args.api_token:
        api_token = args.api_token.strip()

    health: dict[str, Any] | None = None
    with ApiClient(url, timeout=ctx.timeout, debug=out.debug if out.verbose else None) as probe:
        try:
            health = probe.health()
        except CliError:
            if not args.force:
                raise
            out.warn("El servidor no respondió; guardo el perfil igual porque usaste --force.")
    if health:
        _check_protocol(ctx, health)

    profile = Profile(name=name, url=url, agent_name=(health or {}).get("name") or None)
    if api_token:
        profile.auth = AUTH_STORED
    elif args.token_file or args.home or is_loopback_url(url):
        profile.auth = AUTH_SESSION
        profile.token_file = str(Path(args.token_file).expanduser().resolve()) if args.token_file else None
        profile.home = str(Path(args.home).expanduser().resolve()) if args.home else None

    token: str | None = api_token
    token_note = AUTH_LABELS[profile.auth]
    if profile.auth == AUTH_SESSION:
        token, found, _ = ctx.session_token(profile)
        token_note = f"sesión local ({found})" if found else "sesión local (todavía no encontré el archivo)"

    me: dict[str, Any] | None = None
    if health and token:
        with ApiClient(url, token, timeout=ctx.timeout) as client:
            try:
                me = client.me()
            except ApiError as exc:
                if api_token:
                    raise
                out.warn(f"El token de sesión encontrado no sirvió: {exc.message}")
    if me:
        agent = me.get("agent") if isinstance(me.get("agent"), dict) else {}
        profile.agent_name = agent.get("name") or profile.agent_name
        profile.device_id = me.get("device_id") or None
        profile.scopes = [str(s) for s in me.get("scopes") or []]

    if api_token:
        where = ctx.creds.set(name, api_token)
        token_note = "llavero del sistema" if where == "keyring" else f"archivo {ctx.creds.file.path}"
    ctx.store.put(profile, make_current=not args.no_use)

    if out.json_mode:
        out.json(
            {
                "profile": name,
                "url": url,
                "auth": profile.auth,
                "current": not args.no_use,
                "health": health,
                "me": me,
            }
        )
        return EXIT_OK
    state = "guardado y activo" if not args.no_use else "guardado"
    out.success(f"Perfil «{name}» {state}.")
    pairs: list[tuple[str, Any]] = [("URL", url)]
    if health:
        mode = "servidor" if health.get("mode") == "server" else "escritorio"
        pairs.append(
            ("Agente", f"{health.get('name') or '-'} (modo {mode}, versión {health.get('version') or '?'})")
        )
    pairs.append(("Token", token_note))
    out.fields(pairs)
    if profile.auth == AUTH_NONE:
        out.note(f"Para usarlo, empareja este equipo: gmini pair {url} <código>")
    return EXIT_OK


# ── pair ────────────────────────────────────────────────────────────────


def cmd_pair(ctx: AppContext, args: argparse.Namespace) -> int:
    out = ctx.out
    if args.host.strip().lower().startswith("gmini://"):
        link = parse_pairing_link(args.host)
        url, code = link.url, normalize_code(args.code or link.code)
    else:
        url, code = normalize_url(args.host), normalize_code(args.code)

    with ApiClient(url, timeout=ctx.timeout, debug=out.debug if out.verbose else None) as client:
        health = client.health()
        _check_protocol(ctx, health)
        try:
            claim = client.claim_pairing(
                code=code,
                device_name=(args.device_name or device_name_default())[:80],
                device_type="cli",
                platform=paths.platform_tag(),
            )
        except ApiError as exc:
            if exc.status in (400, 401, 403, 404) and exc.code != "rate_limited":
                raise ApiError(exc.status, "invalid_code", exc.server_message) from exc
            raise

    token = str(claim.get("token") or "").strip()
    if not token:
        raise CliError("El servidor no devolvió un token al emparejar.")
    agent_name = str(claim.get("agent_name") or health.get("name") or "") or None
    server_name = str(claim.get("server_name") or "") or None
    name = (
        validate_profile_name(args.name) if args.name else default_profile_name(url, server_name, agent_name)
    )

    replaced = ctx.store.get(name)
    where = ctx.creds.set(name, token)
    profile = Profile(
        name=name,
        url=url,
        auth=AUTH_STORED,
        device_id=str(claim.get("device_id") or "") or None,
        server_name=server_name,
        agent_name=agent_name,
        scopes=[str(s) for s in claim.get("scopes") or []],
    )
    ctx.store.put(profile, make_current=True)
    store_label = "llavero del sistema" if where == "keyring" else f"archivo {ctx.creds.file.path}"

    if out.json_mode:
        out.json(
            {
                "profile": name,
                "url": url,
                "device_id": profile.device_id,
                "server_name": server_name,
                "agent_name": agent_name,
                "scopes": profile.scopes,
                "token_store": where,
            }
        )
        return EXIT_OK
    if replaced is not None:
        out.note(f"Reemplacé el perfil anterior «{name}».")
    out.success(f"Emparejado con {server_name or url}. Perfil «{name}» activo.")
    out.fields(
        [
            ("URL", url),
            ("Agente", agent_name),
            ("Dispositivo", profile.device_id),
            ("Alcances", ", ".join(profile.scopes) or "-"),
            ("Token", store_label),
        ]
    )
    out.note("Prueba: gmini status   |   gmini chat")
    return EXIT_OK


# ── profiles ────────────────────────────────────────────────────────────


def _profile_rows(ctx: AppContext) -> tuple[list[Profile], str | None]:
    profiles = ctx.store.all()
    current = ctx.store.current_name
    if not any(p.name == LOCAL_PROFILE for p in profiles):
        profiles.insert(0, implicit_local_profile())
    return profiles, current or LOCAL_PROFILE


def cmd_profiles_list(ctx: AppContext, args: argparse.Namespace) -> int:
    out = ctx.out
    profiles, current = _profile_rows(ctx)
    if out.json_mode:
        out.json(
            {
                "current": current,
                "credential_store": ctx.creds.describe(),
                "profiles": [{"name": p.name, "implicit": p.implicit, **p.to_dict()} for p in profiles],
            }
        )
        return EXIT_OK
    rows = []
    for profile in profiles:
        marker = "*" if profile.name == current else ""
        name = f"{profile.name} (implícito)" if profile.implicit else profile.name
        who = profile.server_name or profile.agent_name or "-"
        rows.append([marker, name, profile.url, AUTH_LABELS.get(profile.auth, profile.auth), who])
    out.table(["", "Perfil", "URL", "Autenticación", "Servidor"], rows)
    out.note(f"Tokens guardados en: {ctx.creds.describe()}")
    return EXIT_OK


def cmd_profiles_use(ctx: AppContext, args: argparse.Namespace) -> int:
    profile = ctx.store.use(args.name)
    if ctx.out.json_mode:
        ctx.out.json({"current": profile.name, "url": profile.url})
    else:
        ctx.out.success(f"Perfil activo: {profile.name} ({profile.url})")
    return EXIT_OK


def cmd_profiles_remove(ctx: AppContext, args: argparse.Namespace) -> int:
    out = ctx.out
    profile = ctx.store.get(args.name)
    if profile is None:
        raise UsageError(f"No existe el perfil «{args.name}».")
    question = f"¿Eliminar el perfil «{profile.name}» ({profile.url}) y su token guardado?"
    if not out.confirm(question, assume_yes=args.yes):
        out.line("Cancelado.")
        return EXIT_OK
    ctx.store.remove(profile.name)
    ctx.creds.delete(profile.name)
    if out.json_mode:
        out.json({"removed": profile.name, "current": ctx.store.current_name})
    else:
        out.success(f"Perfil «{profile.name}» eliminado.")
        if profile.device_id:
            out.note(
                "Si el dispositivo sigue registrado en el servidor, revócalo con "
                f"'gmini devices revoke {profile.device_id}' desde un perfil con permiso admin."
            )
    return EXIT_OK
