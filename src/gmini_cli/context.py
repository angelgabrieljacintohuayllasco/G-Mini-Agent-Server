"""Resolución del servidor de destino: perfil, URL y token de cada comando."""

from __future__ import annotations

import argparse
import os
import socket
from dataclasses import dataclass
from pathlib import Path

from . import paths
from .client import ApiClient
from .console import Output
from .credentials import CredentialStore, store_from_env
from .errors import EXIT_AUTH, CliError
from .profiles import (
    AUTH_SESSION,
    AUTH_STORED,
    LOCAL_PROFILE,
    Profile,
    ProfileStore,
    is_loopback_url,
    normalize_url,
)


@dataclass
class Target:
    url: str
    token: str | None
    profile: Profile | None
    #: Origen del token: flag, env, keyring/archivo, archivo de sesión o none.
    token_source: str

    @property
    def label(self) -> str:
        return self.profile.name if self.profile else self.url


def device_name_default() -> str:
    host = socket.gethostname() or "equipo"
    return f"gmini CLI ({host})"[:80]


class AppContext:
    """Estado compartido de una invocación de la CLI."""

    def __init__(self, args: argparse.Namespace, out: Output) -> None:
        self.args = args
        self.out = out
        self.config_dir = paths.config_dir()
        self._store: ProfileStore | None = None
        self._creds: CredentialStore | None = None

    # ── Almacenes ───────────────────────────────────────────────────────

    @property
    def store(self) -> ProfileStore:
        if self._store is None:
            self._store = ProfileStore(self.config_dir / "profiles.json")
        return self._store

    @property
    def creds(self) -> CredentialStore:
        if self._creds is None:
            self._creds = store_from_env(self.config_dir, warn=self.out.warn)
        return self._creds

    # ── Opciones globales ───────────────────────────────────────────────

    def _opt(self, name: str, env: str | None = None) -> str | None:
        value = getattr(self.args, name, None)
        if value:
            return str(value)
        return os.environ.get(env) if env else None

    @property
    def timeout(self) -> float:
        return float(getattr(self.args, "timeout", None) or 30.0)

    def profile_name(self) -> str:
        return self._opt("profile", "GMINI_PROFILE") or self.store.current_name or LOCAL_PROFILE

    # ── Token de sesión del núcleo local ────────────────────────────────

    def session_token(self, profile: Profile | None) -> tuple[str | None, Path | None, list[Path]]:
        explicit = []
        extra_homes = []
        if profile is not None:
            if profile.token_file:
                explicit.append(Path(profile.token_file).expanduser())
            if profile.home:
                extra_homes.append(Path(profile.home).expanduser())
        candidates = paths.session_token_candidates(
            explicit=explicit, extra_homes=extra_homes, cwd=Path.cwd()
        )
        for candidate in candidates:
            token = paths.read_token_file(candidate)
            if token:
                return token, candidate, candidates
        return None, None, candidates

    # ── Destino ─────────────────────────────────────────────────────────

    def resolve_target(self, *, need_token: bool = True) -> Target:
        flag_token = self._opt("token", "GMINI_TOKEN")
        token_file = self._opt("token_file", "GMINI_TOKEN_FILE")
        url_override = self._opt("url", "GMINI_URL")

        profile: Profile | None = None
        if url_override:
            url = normalize_url(url_override)
        else:
            profile = self.store.resolve(self.profile_name())
            url = profile.url

        token: str | None = None
        source = "none"
        searched: list[Path] = []
        if flag_token:
            token, source = flag_token.strip(), "flag"
        elif token_file:
            token = paths.read_token_file(Path(token_file).expanduser())
            source = f"archivo {token_file}"
            if token is None and need_token:
                raise CliError(
                    f"No se pudo leer el token de {token_file}.", exit_code=EXIT_AUTH, code="token_missing"
                )
        elif profile is not None and profile.auth == AUTH_STORED:
            token = self.creds.get(profile.name)
            source = "llavero" if self.creds.uses_keyring else "archivo de credenciales"
            if token is None and need_token:
                raise CliError(
                    f"No encontré el token del perfil «{profile.name}».",
                    hint=f"Vuelve a emparejar: gmini pair {profile.url} <código>",
                    exit_code=EXIT_AUTH,
                    code="token_missing",
                )
        elif (profile is not None and profile.auth == AUTH_SESSION) or (
            profile is None and is_loopback_url(url)
        ):
            # El token de sesión solo se usa con un perfil configurado para eso o
            # contra 127.0.0.1: nunca se envía a un host arbitrario de --url.
            token, path, searched = self.session_token(profile)
            if path is not None:
                source = f"sesión local ({path})"

        target = Target(url=url, token=token, profile=profile, token_source=source)
        if need_token and not token:
            raise self._missing_token(target, searched)
        return target

    def _missing_token(self, target: Target, searched: list[Path]) -> CliError:
        if is_loopback_url(target.url):
            self.out.debug("Busqué el token de sesión en: " + ", ".join(str(p) for p in searched))
            return CliError(
                "No encontré el token de sesión del núcleo local.",
                hint="Abre la app de escritorio, o indica su carpeta de datos con "
                "'gmini connect --home <carpeta>' (también sirven GMINI_HOME y --token-file).",
                exit_code=EXIT_AUTH,
                code="token_missing",
            )
        return CliError(
            f"El perfil «{target.label}» no tiene token.",
            hint="Pide un código en el servidor y empareja con: gmini pair <host> <código>",
            exit_code=EXIT_AUTH,
            code="token_missing",
        )

    def client(self, target: Target | None = None, *, need_token: bool = True) -> ApiClient:
        target = target or self.resolve_target(need_token=need_token)
        return ApiClient(
            target.url, target.token, timeout=self.timeout, debug=self.out.debug if self.out.verbose else None
        )

    def agent_label(self, target: Target, fallback: str = "G-Mini") -> str:
        if target.profile and target.profile.agent_name:
            return target.profile.agent_name
        return fallback
