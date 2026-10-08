"""Fixtures comunes: entorno aislado, servidor falso y ejecución de la CLI."""

from __future__ import annotations

import io
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from gmini_cli.cli import main
from mock_server import MockCore, ServerThread

_ENV_TO_CLEAR = (
    "GMINI_PROFILE",
    "GMINI_URL",
    "GMINI_TOKEN",
    "GMINI_TOKEN_FILE",
    "GMINI_HOME",
    "GMINI_PLAIN",
    "GMINI_CONFIG_DIR",
    "GMINI_DATA_DIR",
    "GMINI_CREDENTIAL_STORE",
    "GMINI_COUNTRY",
    "TZ",
    "NO_COLOR",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
)


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Cada prueba usa carpetas propias: nunca toca la configuración real del equipo."""
    for name in _ENV_TO_CLEAR:
        monkeypatch.delenv(name, raising=False)
    home = tmp_path / "user"
    home.mkdir()
    monkeypatch.setenv("GMINI_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("GMINI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GMINI_CREDENTIAL_STORE", "file")
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def no_tailscale(monkeypatch: pytest.MonkeyPatch) -> None:
    """Las pruebas no dependen de que este equipo tenga Tailscale instalado."""
    monkeypatch.setattr("gmini_cli.commands.admin.tailscale_ipv4", lambda: None)


@pytest.fixture(scope="session")
def mock_server() -> Iterator[ServerThread]:
    server = ServerThread(MockCore()).start()
    yield server
    server.stop()


@pytest.fixture
def server(mock_server: ServerThread) -> ServerThread:
    mock_server.core.reset()
    return mock_server


@pytest.fixture
def core(server: ServerThread) -> MockCore:
    return server.core


@pytest.fixture
def session_home(tmp_path: Path, server: ServerThread, monkeypatch: pytest.MonkeyPatch) -> Path:
    """GMINI_HOME con el token de sesión del servidor falso, como el núcleo local."""
    home = tmp_path / "gmini-home"
    token_file = home / "data" / "runtime" / "session_token"
    token_file.parent.mkdir(parents=True)
    token_file.write_text(server.core.session_token + "\n", encoding="utf-8")
    monkeypatch.setenv("GMINI_HOME", str(home))
    return home


@dataclass
class CliResult:
    code: int
    out: str
    err: str


@pytest.fixture
def run_cli(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> Callable[..., CliResult]:
    def run(*args: str, stdin: str | bytes | None = None) -> CliResult:
        if stdin is not None:
            raw = stdin.encode("utf-8") if isinstance(stdin, str) else stdin
            monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8"))
        code = main(list(args))
        captured = capsys.readouterr()
        return CliResult(code, captured.out, captured.err)

    return run


@pytest.fixture
def paired(run_cli: Callable[..., CliResult], server: ServerThread) -> Callable[..., CliResult]:
    """Empareja la CLI con el servidor falso y devuelve el ejecutor de comandos."""
    code = server.core.pairing_code(["chat", "voice", "tasks"])
    result = run_cli("pair", server.url, code, "--name", "pruebas")
    assert result.code == 0, result.err
    return run_cli
