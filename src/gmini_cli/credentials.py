"""Tokens de dispositivo y de API: llavero del sistema con respaldo en archivo.

Orden de preferencia (``GMINI_CREDENTIAL_STORE`` = ``auto`` | ``keyring`` | ``file``):

1. Llavero del sistema (Windows Credential Manager, Keychain de macOS,
   Secret Service en Linux con sesión gráfica).
2. Archivo ``credentials.json`` en la carpeta de configuración, con permisos
   ``600`` en Unix y, en Windows, una ACL que solo da acceso al usuario
   (mejor esfuerzo con ``icacls``). Es lo habitual en servidores sin
   escritorio, donde no hay un llavero disponible.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .errors import CliError

SERVICE_NAME = "gmini-cli"
FILE_VERSION = 1

# Backends de keyring que no protegen nada o que siempre fallan.
_UNUSABLE_BACKENDS = (
    "keyring.backends.fail.",
    "keyring.backends.null.",
    "keyrings.alt.file.PlaintextKeyring",
)


def _backend_label(backend: Any) -> str:
    return f"{type(backend).__module__}.{type(backend).__name__}"


def detect_keyring() -> Any | None:
    """Devuelve el backend de keyring si es utilizable; ``None`` si no."""
    try:
        import keyring
    except Exception:  # keyring roto o sin dependencias en este sistema
        return None
    try:
        backend = keyring.get_keyring()
    except Exception:
        return None
    label = _backend_label(backend)
    if any(label.startswith(prefix) for prefix in _UNUSABLE_BACKENDS):
        return None
    if type(backend).__name__ == "ChainerBackend" and not getattr(backend, "backends", None):
        return None
    return backend


def restrict_to_user(path: Path, *, is_dir: bool = False) -> None:
    """Deja ``path`` accesible solo para el usuario actual (mejor esfuerzo)."""
    if os.name != "nt":
        with contextlib.suppress(OSError):
            os.chmod(path, 0o700 if is_dir else 0o600)
        return
    user = os.environ.get("USERNAME")
    if not user:
        return
    domain = os.environ.get("USERDOMAIN")
    principal = f"{domain}\\{user}" if domain else user
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{principal}:(F)"],
            check=False,
            capture_output=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )


class FileCredentials:
    """Archivo JSON privado con ``{"tokens": {perfil: token}}``."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _read(self) -> dict[str, str]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            raise CliError(
                f"No se pudo leer el archivo de credenciales {self.path}: {exc}",
                hint="Corrígelo o bórralo y vuelve a emparejar los servidores.",
            ) from exc
        tokens = data.get("tokens") if isinstance(data, dict) else None
        return {str(k): str(v) for k, v in tokens.items()} if isinstance(tokens, dict) else {}

    def _write(self, tokens: dict[str, str]) -> None:
        parent = self.path.parent
        created_dir = not parent.exists()
        parent.mkdir(parents=True, exist_ok=True)
        if created_dir and os.name != "nt":
            restrict_to_user(parent, is_dir=True)
        payload = json.dumps({"version": FILE_VERSION, "tokens": tokens}, indent=2) + "\n"
        fd, tmp = tempfile.mkstemp(dir=parent, prefix=".credentials-", suffix=".tmp")
        try:
            if os.name != "nt":
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.replace(tmp, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
        # os.replace deja el descriptor de seguridad del temporal, así que la
        # restricción se aplica en cada escritura y no solo al crear el archivo.
        restrict_to_user(self.path)

    def get(self, name: str) -> str | None:
        return self._read().get(name)

    def set(self, name: str, token: str) -> None:
        tokens = self._read()
        tokens[name] = token
        self._write(tokens)

    def delete(self, name: str) -> bool:
        tokens = self._read()
        if name not in tokens:
            return False
        del tokens[name]
        self._write(tokens)
        return True


class CredentialStore:
    """Fachada que elige llavero o archivo y cae al archivo si el llavero falla."""

    def __init__(
        self,
        file_path: Path,
        *,
        mode: str = "auto",
        warn: Callable[[str], None] | None = None,
        keyring_backend: Any | None = None,
    ) -> None:
        mode = (mode or "auto").strip().lower()
        if mode not in {"auto", "keyring", "file"}:
            raise CliError(
                f"Valor no válido para GMINI_CREDENTIAL_STORE: {mode!r}",
                hint="Usa auto, keyring o file.",
            )
        self.mode = mode
        self.file = FileCredentials(file_path)
        self._warn = warn or (lambda _msg: None)
        self._warned = False
        if mode == "file":
            self._keyring = None
        else:
            self._keyring = keyring_backend if keyring_backend is not None else detect_keyring()
            if self._keyring is None and mode == "keyring":
                raise CliError(
                    "No hay un llavero del sistema disponible.",
                    hint="Usa GMINI_CREDENTIAL_STORE=file para guardar los tokens en un archivo privado.",
                )

    @property
    def uses_keyring(self) -> bool:
        return self._keyring is not None

    def describe(self) -> str:
        if self._keyring is not None:
            return f"llavero del sistema ({type(self._keyring).__name__})"
        return f"archivo privado ({self.file.path})"

    def _fallback(self, exc: Exception) -> None:
        if self.mode == "keyring":
            raise CliError(f"El llavero del sistema falló: {exc}") from exc
        if not self._warned:
            self._warn(f"El llavero del sistema no respondió ({exc}); uso el archivo {self.file.path}.")
            self._warned = True
        self._keyring = None

    def get(self, name: str) -> str | None:
        if self._keyring is not None:
            try:
                token = self._keyring.get_password(SERVICE_NAME, name)
            except Exception as exc:  # cualquier fallo del backend nativo
                self._fallback(exc)
            else:
                if token:
                    return token
        return self.file.get(name)

    def set(self, name: str, token: str) -> str:
        """Guarda el token y devuelve dónde quedó: ``keyring`` o ``file``."""
        if self._keyring is not None:
            try:
                self._keyring.set_password(SERVICE_NAME, name, token)
            except Exception as exc:
                self._fallback(exc)
            else:
                # Un token viejo en el archivo ya no debe tener prioridad ni quedar expuesto.
                self.file.delete(name)
                return "keyring"
        self.file.set(name, token)
        return "file"

    def delete(self, name: str) -> None:
        if self._keyring is not None:
            # Puede no existir o el backend puede no soportar borrar: da igual.
            with contextlib.suppress(Exception):
                self._keyring.delete_password(SERVICE_NAME, name)
        self.file.delete(name)


def store_from_env(config_path: Path, warn: Callable[[str], None] | None = None) -> CredentialStore:
    mode = os.environ.get("GMINI_CREDENTIAL_STORE", "auto")
    return CredentialStore(config_path / "credentials.json", mode=mode, warn=warn)
