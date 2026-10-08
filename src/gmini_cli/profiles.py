"""Perfiles de conexión: uno por servidor (escritorio local, tv-server, VPS...).

Se guardan en ``<config>/profiles.json``. Los tokens nunca van en este
archivo: viven en el llavero del sistema o en el archivo de credenciales
(ver :mod:`gmini_cli.credentials`).
"""

from __future__ import annotations

import contextlib
import ipaddress
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .errors import CliError, UsageError

DEFAULT_PORT = 8765
LOCAL_PROFILE = "local"
LOCAL_URL = f"http://127.0.0.1:{DEFAULT_PORT}"
PROFILES_VERSION = 1

AUTH_SESSION = "session"  # token de sesión del núcleo local, leído del archivo en cada uso
AUTH_STORED = "stored"  # token de dispositivo o de API guardado en el llavero/archivo
AUTH_NONE = "none"

_PROFILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_LOOPBACK_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost"}


# ── URLs ────────────────────────────────────────────────────────────────


def _is_loopback_host(host: str) -> bool:
    host = host.strip("[]").lower()
    if host in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def normalize_url(raw: str) -> str:
    """Normaliza la dirección de un servidor.

    - Una dirección pelada (``100.71.131.70``, ``tv-server``) usa ``http://`` y
      el puerto 8765 del núcleo.
    - Con esquema escrito se respeta el puerto estándar (80 o 443): es lo
      normal detrás de un proxy con TLS. Igual que en el núcleo
      (``backend/core/remote_servers.py``).
    - ``localhost``/``::1``/``0.0.0.0`` pasan a ``127.0.0.1``: el núcleo de
      escritorio solo acepta ese Host y así se evita el intento por IPv6.
    - Se descartan la consulta, el fragmento y un ``/api/v1`` pegado al final.
    """
    text = (raw or "").strip()
    if not text:
        raise UsageError("Falta la dirección del servidor.")
    has_scheme = "://" in text
    if not has_scheme:
        text = "http://" + text
    parts = urlsplit(text)
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"}:
        raise UsageError(f"Esquema no soportado: {parts.scheme}://", hint="Usa http:// o https://.")
    host = parts.hostname
    if not host:
        raise UsageError(f"La dirección no tiene un host válido: {raw}")
    try:
        port = parts.port
    except ValueError as exc:
        raise UsageError(f"Puerto no válido en la dirección: {raw}") from exc
    if port is None and not has_scheme:
        port = DEFAULT_PORT
    if _is_loopback_host(host) or host == "0.0.0.0":
        host = "127.0.0.1"
        if port is None:
            port = DEFAULT_PORT
    host_part = f"[{host}]" if ":" in host else host
    default_port = 443 if scheme == "https" else 80
    port_part = f":{port}" if port is not None and port != default_port else ""
    path = parts.path.rstrip("/")
    for suffix in ("/api/v1", "/api"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    return f"{scheme}://{host_part}{port_part}{path}"


def is_loopback_url(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    return _is_loopback_host(host)


def url_host(url: str) -> str:
    return urlsplit(url).hostname or url


def default_profile_name(url: str, server_name: str | None = None, agent_name: str | None = None) -> str:
    """Nombre sugerido para un perfil.

    Primero el nombre del equipo que informa el servidor al emparejar (en el
    núcleo posterior a la v0.2.0 es su hostname); sirve también cuando se
    empareja por un túnel SSH a 127.0.0.1. Si no hay, ``local`` para el núcleo
    de esta máquina y, como último recurso, el host de la URL.
    """
    if server_name and server_name != agent_name:
        candidate = sanitize_profile_name(server_name)
        if candidate:
            return candidate
    if is_loopback_url(url):
        return LOCAL_PROFILE
    return sanitize_profile_name(url_host(url)) or "servidor"


def sanitize_profile_name(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    return name[:64]


def validate_profile_name(name: str) -> str:
    if not _PROFILE_NAME.match(name or ""):
        raise UsageError(
            f"Nombre de perfil no válido: {name!r}",
            hint="Usa letras, números, punto, guion o guion bajo (máximo 64).",
        )
    return name


@dataclass
class PairingLink:
    url: str
    code: str | None


def parse_pairing_link(link: str) -> PairingLink:
    """Interpreta el ``qr_payload`` del contrato: ``gmini://pair?host=..&port=..&code=..``."""
    parts = urlsplit(link.strip())
    if parts.scheme.lower() != "gmini":
        raise UsageError(f"No es un enlace de emparejamiento: {link}")
    query = {k: v[0] for k, v in parse_qs(parts.query).items() if v}
    host = query.get("host")
    if not host:
        raise UsageError("El enlace de emparejamiento no indica el host del servidor.")
    port = query.get("port") or str(DEFAULT_PORT)
    tls = query.get("tls", "").lower() in {"1", "true", "yes"} or query.get("scheme", "") == "https"
    scheme = "https" if tls else "http"
    host_part = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return PairingLink(url=normalize_url(f"{scheme}://{host_part}:{port}"), code=query.get("code"))


# ── Perfiles ────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class Profile:
    name: str
    url: str
    auth: str = AUTH_NONE
    token_file: str | None = None
    home: str | None = None
    device_id: str | None = None
    server_name: str | None = None
    agent_name: str | None = None
    scopes: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=_now_iso)
    implicit: bool = False

    @property
    def is_local(self) -> bool:
        return is_loopback_url(self.url)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("name")
        data.pop("implicit")
        return {k: v for k, v in data.items() if v not in (None, [], "")}

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> Profile:
        scopes = data.get("scopes") or []
        return cls(
            name=name,
            url=str(data.get("url") or LOCAL_URL),
            auth=str(data.get("auth") or AUTH_NONE),
            token_file=data.get("token_file") or None,
            home=data.get("home") or None,
            device_id=data.get("device_id") or None,
            server_name=data.get("server_name") or None,
            agent_name=data.get("agent_name") or None,
            scopes=[str(s) for s in scopes] if isinstance(scopes, list) else [],
            created_at=str(data.get("created_at") or ""),
        )


def implicit_local_profile() -> Profile:
    """Perfil ``local`` disponible aunque nunca se haya ejecutado ``gmini connect``."""
    return Profile(name=LOCAL_PROFILE, url=LOCAL_URL, auth=AUTH_SESSION, created_at="", implicit=True)


class ProfileStore:
    """Lectura y escritura atómica de ``profiles.json``."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {"version": PROFILES_VERSION, "current": None, "profiles": {}}
        except OSError as exc:
            raise CliError(f"No se pudo leer {self.path}: {exc}") from exc
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise CliError(
                f"El archivo de perfiles está dañado: {self.path}",
                hint="Corrígelo a mano o bórralo y vuelve a conectar los servidores.",
            ) from exc
        if not isinstance(data, dict) or not isinstance(data.get("profiles", {}), dict):
            raise CliError(f"Formato inesperado en {self.path}")
        data.setdefault("profiles", {})
        data.setdefault("current", None)
        return data

    def save(self) -> None:
        if not self.path.parent.exists():
            self.path.parent.mkdir(parents=True)
            if os.name != "nt":
                # La carpeta también guarda el archivo de credenciales de respaldo.
                os.chmod(self.path.parent, 0o700)
        self._data["version"] = PROFILES_VERSION
        payload = json.dumps(self._data, ensure_ascii=False, indent=2) + "\n"
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".profiles-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.replace(tmp, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    # Consultas

    def names(self) -> list[str]:
        return sorted(self._data["profiles"])

    def get(self, name: str) -> Profile | None:
        data = self._data["profiles"].get(name)
        return Profile.from_dict(name, data) if isinstance(data, dict) else None

    def all(self) -> list[Profile]:
        return [p for p in (self.get(n) for n in self.names()) if p is not None]

    @property
    def current_name(self) -> str | None:
        current = self._data.get("current")
        return current if isinstance(current, str) and current else None

    def resolve(self, name: str) -> Profile:
        profile = self.get(name)
        if profile is not None:
            return profile
        if name == LOCAL_PROFILE:
            return implicit_local_profile()
        raise UsageError(
            f"No existe el perfil «{name}».",
            hint="Mira los perfiles con 'gmini profiles list' o crea uno con 'gmini connect <url>'.",
        )

    # Cambios

    def put(self, profile: Profile, *, make_current: bool = True) -> None:
        validate_profile_name(profile.name)
        self._data["profiles"][profile.name] = profile.to_dict()
        if make_current or not self.current_name:
            self._data["current"] = profile.name
        self.save()

    def use(self, name: str) -> Profile:
        profile = self.resolve(name)
        if profile.implicit:
            self._data["profiles"][name] = profile.to_dict()
        self._data["current"] = name
        self.save()
        return profile

    def remove(self, name: str) -> bool:
        existed = self._data["profiles"].pop(name, None) is not None
        if self.current_name == name:
            remaining = self.names()
            self._data["current"] = remaining[0] if remaining else None
        if existed:
            self.save()
        return existed
