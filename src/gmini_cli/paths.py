"""Ubicaciones de configuración, datos y tokens de sesión en cada sistema."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path

import platformdirs

APP_NAME = "gmini"
DESKTOP_PRODUCT_NAME = "G-Mini Agent"
SESSION_TOKEN_RELATIVE = Path("data", "runtime", "session_token")


def _environ(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def config_dir(env: Mapping[str, str] | None = None) -> Path:
    """Carpeta con los perfiles y el archivo de credenciales de respaldo.

    Linux: ``~/.config/gmini``; macOS: ``~/Library/Application Support/gmini``;
    Windows: ``%LOCALAPPDATA%\\gmini``. ``GMINI_CONFIG_DIR`` la reemplaza.
    """
    override = _environ(env).get("GMINI_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    return Path(platformdirs.user_config_dir(APP_NAME, appauthor=False, roaming=False))


def data_dir(env: Mapping[str, str] | None = None) -> Path:
    """Carpeta de datos de la CLI (historial del chat, instalación del servidor)."""
    override = _environ(env).get("GMINI_DATA_DIR")
    if override:
        return Path(override).expanduser()
    return Path(platformdirs.user_data_dir(APP_NAME, appauthor=False, roaming=False))


def default_server_dir(env: Mapping[str, str] | None = None) -> Path:
    """Carpeta gestionada donde ``gmini server install`` deja código, venv y datos."""
    return data_dir(env) / "server"


def history_file(env: Mapping[str, str] | None = None) -> Path:
    return data_dir(env) / "chat_history"


def session_token_file(home: str | os.PathLike[str]) -> Path:
    """Ruta del token de sesión dentro de un ``GMINI_HOME``."""
    return Path(home).expanduser() / SESSION_TOKEN_RELATIVE


def known_home_candidates(
    env: Mapping[str, str] | None = None,
    platform: str = sys.platform,
) -> list[Path]:
    """Carpetas de datos (``GMINI_HOME``) conocidas del núcleo en esta máquina.

    - Linux: ``~/.local/share/g-mini``, la que usa el instalador oficial del
      modo servidor (``deploy/linux/install-server.sh`` del repositorio del
      núcleo).
    - La carpeta de datos de Electron de la app de escritorio
      (``productName`` = "G-Mini Agent") y ``~/.gmini``.

    ``GMINI_HOME``, ``--token-file`` o ``gmini connect --home`` siempre tienen
    prioridad sobre estas conjeturas.
    """
    env = _environ(env)
    user_home = Path(env.get("USERPROFILE") or env.get("HOME") or str(Path.home()))
    candidates: list[Path] = []
    if platform == "win32":
        appdata = env.get("APPDATA")
        base = Path(appdata) if appdata else user_home / "AppData" / "Roaming"
        candidates.append(base / DESKTOP_PRODUCT_NAME)
    elif platform == "darwin":
        candidates.append(user_home / "Library" / "Application Support" / DESKTOP_PRODUCT_NAME)
    else:
        xdg_data = env.get("XDG_DATA_HOME")
        candidates.append((Path(xdg_data) if xdg_data else user_home / ".local" / "share") / "g-mini")
        xdg_config = env.get("XDG_CONFIG_HOME")
        candidates.append((Path(xdg_config) if xdg_config else user_home / ".config") / DESKTOP_PRODUCT_NAME)
    candidates.append(user_home / ".gmini")
    return candidates


def looks_like_core_checkout(path: Path) -> bool:
    """True si ``path`` parece la raíz del repositorio del núcleo."""
    return (path / "backend" / "main.py").is_file()


def session_token_candidates(
    env: Mapping[str, str] | None = None,
    *,
    explicit: Iterable[Path] = (),
    extra_homes: Iterable[Path] = (),
    cwd: Path | None = None,
    platform: str = sys.platform,
) -> list[Path]:
    """Lista ordenada (sin duplicados) de archivos donde buscar el token de sesión."""
    env = _environ(env)
    ordered: list[Path] = list(explicit)
    if env.get("GMINI_TOKEN_FILE"):
        ordered.append(Path(env["GMINI_TOKEN_FILE"]).expanduser())
    if env.get("GMINI_HOME"):
        ordered.append(session_token_file(env["GMINI_HOME"]))
    ordered.extend(session_token_file(home) for home in extra_homes)
    if cwd is not None and looks_like_core_checkout(cwd):
        ordered.append(session_token_file(cwd))
    ordered.extend(session_token_file(home) for home in known_home_candidates(env, platform))

    unique: list[Path] = []
    seen: set[str] = set()
    for path in ordered:
        key = os.path.normcase(str(path))
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def read_token_file(path: Path) -> str | None:
    """Lee un archivo de token; devuelve ``None`` si no existe, está vacío o no se puede leer."""
    try:
        token = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return token or None


def platform_tag(platform: str = sys.platform, machine: str | None = None) -> str:
    """Etiqueta corta de la plataforma: ``windows-x64``, ``linux-arm64``, ``macos-arm64``..."""
    import platform as _platform

    system = {"win32": "windows", "darwin": "macos"}.get(platform, platform.rstrip("0123456789"))
    machine = (machine if machine is not None else _platform.machine()).lower()
    arch = {"amd64": "x64", "x86_64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(
        machine, machine or "unknown"
    )
    return f"{system}-{arch}"
