"""Pruebas de punta a punta de ``gmini`` contra el servidor falso."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

import pytest

from conftest import CliResult
from gmini_cli import __version__
from gmini_cli.cli import main
from gmini_cli.commands import connection, voice
from gmini_cli.errors import EXIT_AUTH, EXIT_BUSY, EXIT_ERROR, EXIT_USAGE
from mock_server import SESSION_ID, MockCore, ServerThread

Run = Callable[..., CliResult]


def credentials_file(config: Path) -> dict[str, str]:
    data = json.loads((config / "credentials.json").read_text(encoding="utf-8"))
    return data["tokens"]


# ── Ayuda y uso ─────────────────────────────────────────────────────────


def test_help_is_in_spanish(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        main(["--help"])
    assert caught.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("uso: gmini")
    assert "opciones globales" in out
    assert "comandos" in out


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["--version"])
    assert f"gmini {__version__}" in capsys.readouterr().out


def test_usage_errors_are_in_spanish(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        main(["task", "add"])
    assert caught.value.code == EXIT_USAGE
    assert "faltan estos argumentos" in capsys.readouterr().err


def test_no_command_prints_help(run_cli: Run) -> None:
    result = run_cli()
    assert result.code == EXIT_USAGE
    assert "uso: gmini" in result.out


# ── Emparejamiento y perfiles ───────────────────────────────────────────


def test_pair_creates_profile_and_stores_token(
    run_cli: Run, server: ServerThread, isolated_env: Path
) -> None:
    code = server.core.pairing_code(["chat", "voice", "tasks"])
    result = run_cli("pair", server.url, code)
    assert result.code == 0, result.err
    assert "Emparejado con mock-server" in result.out
    assert "chat, voice, tasks" in result.out

    tokens = credentials_file(isolated_env / "config")
    assert list(tokens) == ["mock-server"]
    device = next(iter(server.core.devices.values()))
    assert device.token == tokens["mock-server"]
    assert device.device_type == "cli"
    assert device.name.startswith("gmini CLI (")

    listing = run_cli("profiles", "list", "--plain")
    assert "*  mock-server" in listing.out
    assert "local (implícito)" in listing.out
    assert "token guardado" in listing.out


def test_pair_accepts_the_qr_link(run_cli: Run, server: ServerThread) -> None:
    code = server.core.pairing_code(["chat"])
    link = f"gmini://pair?host=127.0.0.1&port={server.port}&code={code}"
    result = run_cli("pair", link, "--name", "desde-qr", "--json")
    assert result.code == 0, result.err
    data = json.loads(result.out)
    assert data["profile"] == "desde-qr"
    assert data["scopes"] == ["chat"]
    assert data["token_store"] == "file"


def test_pair_with_wrong_code(run_cli: Run, server: ServerThread) -> None:
    result = run_cli("pair", server.url, "000-000")
    assert result.code == EXIT_AUTH
    assert "no es válido o ya venció" in result.err


def test_pair_rejects_letters_in_code(run_cli: Run, server: ServerThread) -> None:
    result = run_cli("pair", server.url, "12a456")
    assert result.code == EXIT_USAGE
    assert "solo números" in result.err


def test_profiles_use_and_remove(paired: Run, server: ServerThread, isolated_env: Path) -> None:
    assert paired("profiles", "use", "local").code == 0
    assert "*  local" in paired("profiles", "--plain").out
    removed = paired("profiles", "remove", "pruebas", "--yes")
    assert removed.code == 0
    assert "pruebas" not in credentials_file(isolated_env / "config")
    assert "pruebas" not in paired("profiles", "list", "--plain").out


def test_profiles_remove_requires_confirmation_without_tty(paired: Run) -> None:
    result = paired("profiles", "remove", "pruebas")
    assert result.code == EXIT_USAGE
    assert "--yes" in result.err


def test_connect_local_uses_session_token(run_cli: Run, server: ServerThread, session_home: Path) -> None:
    result = run_cli("connect", server.url, "--json")
    assert result.code == 0, result.err
    data = json.loads(result.out)
    assert data["profile"] == "local"
    assert data["auth"] == "session"
    assert data["me"]["kind"] == "session"
    status = run_cli("status", "--plain")
    assert status.code == 0
    assert "sesión local" in status.out


def test_connect_with_api_token(
    run_cli: Run, server: ServerThread, core: MockCore, isolated_env: Path
) -> None:
    result = run_cli("connect", server.url, "--name", "api", "--api-token", core.session_token, "--json")
    assert result.code == 0, result.err
    assert json.loads(result.out)["auth"] == "stored"
    assert credentials_file(isolated_env / "config")["api"] == core.session_token
    assert run_cli("status").code == 0


def test_connect_remote_without_token(
    run_cli: Run, server: ServerThread, monkeypatch: pytest.MonkeyPatch
) -> None:
    # El servidor falso solo escucha en 127.0.0.1: se simula que es un host remoto.
    monkeypatch.setattr(connection, "is_loopback_url", lambda url: False)
    result = run_cli("connect", server.url, "--name", "remoto", "--plain")
    assert result.code == 0, result.err
    assert "sin token" in result.out
    assert "gmini pair" in result.err


def test_connect_fails_when_unreachable(run_cli: Run) -> None:
    result = run_cli("connect", "127.0.0.1:9", "--name", "caido")
    assert result.code == 3
    assert "No se pudo conectar" in result.err
    forced = run_cli("connect", "127.0.0.1:9", "--name", "caido", "--force")
    assert forced.code == 0


# ── Estado ──────────────────────────────────────────────────────────────


def test_status_with_paired_profile(paired: Run) -> None:
    result = paired("status", "--plain")
    assert result.code == 0, result.err
    for pattern in (
        r"Perfil\s+pruebas",
        r"Agente\s+G-Mini",
        r"Modo\s+servidor",
        r"protocolo 1",
        r"Token\s+válido",
    ):
        assert re.search(pattern, result.out), pattern
    as_json = json.loads(paired("status", "--json").out)
    assert as_json["auth"]["valid"] is True
    assert as_json["health"]["version"] == "0.2.0"
    assert as_json["latency_ms"] >= 0


def test_status_without_token_returns_auth_exit_code(run_cli: Run, server: ServerThread) -> None:
    result = run_cli("--url", f"http://127.0.0.1:{server.port}", "status", "--plain")
    assert result.code == EXIT_AUTH
    assert "sin token" in result.out


def test_status_with_revoked_token(paired: Run, core: MockCore) -> None:
    core.devices.clear()
    result = paired("status", "--plain")
    assert result.code == EXIT_AUTH
    assert "rechazado" in result.out


def test_global_options_after_subcommand(run_cli: Run, server: ServerThread, core: MockCore) -> None:
    result = run_cli("status", "--plain", "--url", server.url, "--token", core.session_token)
    assert result.code == 0
    assert "válido (flag)" in result.out


# ── Chat ────────────────────────────────────────────────────────────────


def test_ask_prints_the_reply(paired: Run, core: MockCore) -> None:
    result = paired("ask", "hola")
    assert result.code == 0, result.err
    assert result.out == "Hola, soy G-Mini. ¿En qué te ayudo?\n"
    assert core.chat_bodies[-1]["stream"] is True


def test_ask_json_with_actions(paired: Run) -> None:
    result = paired("ask", "crea una acción", "--json")
    assert result.code == 0
    data = json.loads(result.out)
    assert data["reply"] == "Listo, te aviso a las 18:00."
    assert data["actions"][0]["action"] == "schedule_create_job"
    assert data["notices"] == ["Acciones ejecutadas: schedule_create_job"]


def test_stdout_is_flushed_before_stderr_notes(paired: Run, core: MockCore) -> None:
    result = paired("task", "add", "Revisa el correo", "--every", "1h", "--plain")
    assert result.out.startswith("Tarea creada")
    assert "gmini task show" in result.err


def test_ask_shows_actions_on_stderr(paired: Run) -> None:
    result = paired("ask", "crea una acción", "--plain")
    assert result.out == "Listo, te aviso a las 18:00.\n"
    assert "[acción] schedule_create_job" in result.err
    assert "[ok] Tarea creada" in result.err


def test_ask_without_stream(paired: Run, core: MockCore) -> None:
    result = paired("ask", "hola", "--no-stream")
    assert result.out.startswith("Hola, soy G-Mini")
    assert core.chat_bodies[-1]["stream"] is False


def test_ask_reads_stdin(paired: Run, core: MockCore) -> None:
    result = paired("ask", stdin="hola desde una tubería con acentos: ñandú\n")
    assert result.code == 0
    assert core.chat_bodies[-1]["message"] == "hola desde una tubería con acentos: ñandú"


def test_ask_combines_argument_and_stdin(paired: Run, core: MockCore) -> None:
    paired("ask", "resume esto", "-", stdin="línea 1\nlínea 2\n")
    assert core.chat_bodies[-1]["message"] == "resume esto\n\nlínea 1\nlínea 2"


def test_ask_over_websocket(paired: Run, core: MockCore) -> None:
    result = paired("ask", "hola", "--transport", "ws")
    assert result.code == 0, result.err
    assert result.out.startswith("Hola, soy G-Mini")
    assert any(frame.get("type") == "chat" for frame in core.ws_frames)


def test_chat_one_shot_with_attachment(paired: Run, core: MockCore, tmp_path: Path) -> None:
    note = tmp_path / "lista.txt"
    note.write_text("pan, leche", encoding="utf-8")
    result = paired("chat", "revisa la lista", "-a", str(note))
    assert result.code == 0
    assert core.chat_bodies[-1]["attachments"][0]["name"] == "lista.txt"


def test_provider_error(paired: Run) -> None:
    result = paired("ask", "esto falla")
    assert result.code == EXIT_ERROR
    assert "proveedor de IA no está disponible" in result.err
    as_json = paired("ask", "esto falla", "--json")
    assert as_json.code == EXIT_ERROR
    assert json.loads(as_json.out)["error"]["code"] == "provider_unavailable"


def test_busy_agent(paired: Run) -> None:
    result = paired("ask", "estás ocupado?")
    assert result.code == EXIT_BUSY
    assert "ocupado" in result.err
    assert "gmini task add" in result.err


def test_missing_message(paired: Run) -> None:
    result = paired("ask", stdin="")
    assert result.code == EXIT_USAGE
    assert "Falta el mensaje" in result.err


def test_chat_repl_from_stdin(paired: Run) -> None:
    script = "hola\n/estado\n/sesiones\n/historial 1\n/desconocido\n/salir\nno se envía\n"
    result = paired("chat", "--plain", stdin=script)
    assert result.code == 0, result.err
    assert "tú> hola" in result.out
    assert "G-Mini> Hola, soy G-Mini. ¿En qué te ayudo?" in result.out
    assert re.search(r"Ocupado\s+no", result.out)
    assert SESSION_ID in result.out
    assert "G-Mini:" in result.out
    assert "Comando desconocido: /desconocido" in result.err
    assert "no se envía" not in result.out


def test_chat_repl_approval_flow(paired: Run, core: MockCore) -> None:
    result = paired("chat", "--plain", stdin="quiero aprobar algo\n/aprobar\n/rechazar\n")
    assert result.code == 0
    assert "espera tu aprobación: Borrar 3 archivos de Descargas" in result.err
    assert "Aprobado." in result.out
    assert "No hay acciones esperando aprobación." in result.out
    assert core.approvals == [True]


def test_chat_repl_reports_errors_and_continues(paired: Run) -> None:
    result = paired("chat", "--plain", stdin="esto falla\nhola\n")
    assert result.code == 0
    assert "proveedor de IA" in result.err
    assert "G-Mini> Hola, soy G-Mini" in result.out


def test_approve_and_reject_commands(paired: Run, core: MockCore) -> None:
    assert "No hay acciones" in paired("approve").out
    core.approval_pending = True
    assert paired("reject").code == 0
    assert core.approvals == [False]


# ── Tareas ──────────────────────────────────────────────────────────────


def test_task_lifecycle(paired: Run, core: MockCore) -> None:
    added = paired("task", "add", "Resume", "mi", "correo", "--title", "Correo", "--plain")
    assert added.code == 0, added.err
    assert "Tarea creada: tsk_0001 (en cola)" in added.out
    assert core.tasks["tsk_0001"]["prompt"] == "Resume mi correo"

    listing = paired("task", "list", "--plain")
    assert "tsk_0001" in listing.out
    assert "terminada" in listing.out

    shown = paired("task", "show", "tsk_0001", "--plain")
    assert "Resultado" in shown.out
    assert "Hecho: Resume mi correo" in shown.out

    cancelled = paired("task", "cancel", "tsk_0001")
    assert "cancelada" in cancelled.out
    assert core.tasks["tsk_0001"]["status"] == "cancelled"
    assert "No hay tareas con estado en curso." in paired("tasks", "list", "--status", "running").out


def test_task_with_cron_and_notify(paired: Run, core: MockCore) -> None:
    result = paired(
        "task",
        "add",
        "Revisa",
        "el",
        "correo",
        "--cron",
        "0 8 * * *",
        "--tz",
        "America/Lima",
        "--notify",
        "telegram:123456,whatsapp:51999",
        "--json",
    )
    assert result.code == 0, result.err
    assert json.loads(result.out)["status"] == "scheduled"
    task = core.tasks["tsk_0001"]
    assert task["notify"] == ["telegram:123456", "whatsapp:51999"]


def test_task_with_interval(paired: Run, core: MockCore) -> None:
    result = paired("task", "add", "Mira el dólar", "--every", "2h")
    assert result.code == 0
    assert "cada 2 horas" in result.out


def test_task_validation(paired: Run) -> None:
    assert paired("task", "add", "x", "--every", "30").code == EXIT_USAGE
    assert paired("task", "add", "x", "--notify", "telegram").code == EXIT_USAGE
    assert paired("task", "add", "x", "--tz", "America/Lima").code == EXIT_USAGE
    at = paired("task", "add", "x", "--at", "2099-01-01T08:00Z")
    assert at.code == EXIT_ERROR
    assert "todavía no está disponible" in at.err
    with pytest.raises(SystemExit):
        main(["task", "add", "x", "--cron", "0 8 * * *", "--every", "1h"])


def test_task_not_found(paired: Run) -> None:
    result = paired("task", "show", "tsk_9999")
    assert result.code == EXIT_ERROR
    assert "No se encontró" in result.err


# ── Voz ─────────────────────────────────────────────────────────────────


def test_say_saves_wav(paired: Run, tmp_path: Path) -> None:
    target = tmp_path / "salida" / "hola.wav"
    result = paired("say", "Hola", "mundo", "--out", str(target))
    assert result.code == 0, result.err
    assert target.read_bytes()[:4] == b"RIFF"
    assert "Audio guardado" in result.out


def test_say_plays_and_cleans_up(paired: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    played: list[Path] = []

    def fake_play(path: Path) -> bool:
        played.append(path)
        assert path.read_bytes()[:4] == b"RIFF"
        return True

    monkeypatch.setattr(voice, "play_file", fake_play)
    assert paired("say", "Hola").code == 0
    assert len(played) == 1
    assert not played[0].exists()


def test_say_keeps_file_without_player(paired: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(voice, "play_file", lambda path: False)
    result = paired("say", "Hola")
    assert result.code == 0
    assert "No encontré cómo reproducir" in result.err


def test_transcribe(paired: Run, tmp_path: Path, core: MockCore) -> None:
    from mock_server import tiny_wav

    audio = tmp_path / "nota.wav"
    audio.write_bytes(tiny_wav())
    result = paired("transcribe", str(audio))
    assert result.code == 0
    assert result.out.startswith("transcripción de prueba")
    assert core.stt_bodies[-1] == audio.read_bytes()

    core.stt_silent = True
    silent = paired("transcribe", str(audio))
    assert silent.code == 0
    assert silent.out == ""
    assert "no reconoció texto" in silent.err


def test_wake_word(paired: Run, tmp_path: Path, core: MockCore) -> None:
    from mock_server import tiny_wav

    clip = tmp_path / "clip.wav"
    clip.write_bytes(tiny_wav())
    detected = paired("wake", str(clip), "--plain")
    assert detected.code == 0, detected.err
    assert "Palabra de activación detectada: «oye g-mini»" in detected.out
    assert "Pedido: qué hora es" in detected.out
    assert "Transcripción: Oye G-Mini, qué hora es" in detected.err

    core.stt_silent = True
    missed = paired("wake", str(clip), "--json")
    assert missed.code == EXIT_ERROR
    assert json.loads(missed.out)["wake"] is False


def test_wake_warns_about_long_clips(paired: Run, tmp_path: Path) -> None:
    from mock_server import tiny_wav

    clip = tmp_path / "largo.wav"
    clip.write_bytes(tiny_wav(milliseconds=5000))
    result = paired("wake", str(clip))
    assert "conviene menos de 4 s" in result.err


# ── Administración ──────────────────────────────────────────────────────


def test_devices_require_admin_scope(paired: Run) -> None:
    result = paired("devices", "list")
    assert result.code == EXIT_AUTH
    assert "no tiene permiso" in result.err


def test_devices_with_session_token(paired: Run, server: ServerThread, session_home: Path) -> None:
    device_id = next(iter(server.core.devices))
    listing = paired("--profile", "local", "--url", server.url, "devices", "--plain")
    assert listing.code == 0, listing.err
    assert device_id in listing.out
    revoked = paired("--url", server.url, "devices", "revoke", device_id, "--yes")
    assert revoked.code == 0
    assert device_id not in server.core.devices
    assert "No hay dispositivos" in paired("--url", server.url, "devices", "list").out


def test_pair_code_prints_command_and_qr(run_cli: Run, server: ServerThread, session_home: Path) -> None:
    result = run_cli("--url", server.url, "pair-code", "--host", "100.71.131.70", "--plain")
    assert result.code == 0, result.err
    assert f"gmini pair 100.71.131.70:{server.port} " in result.out
    assert "█" in result.out
    code = result.out.split("Código de emparejamiento: ")[1].split()[0]
    assert code in server.core.codes

    data = json.loads(run_cli("--url", server.url, "pair-code", "--host", "tv-server", "--json").out)
    assert data["command"].startswith(f"gmini pair tv-server:{server.port} ")
    assert "host=tv-server" in data["qr_payload"]


def test_pair_code_end_to_end(run_cli: Run, server: ServerThread, session_home: Path) -> None:
    created = json.loads(run_cli("--url", server.url, "pair-code", "--scopes", "chat,tasks", "--json").out)
    paired = run_cli("pair", server.url, created["code"], "--name", "otra-pc")
    assert paired.code == 0, paired.err
    device = next(iter(server.core.devices.values()))
    assert device.scopes == ["chat", "tasks"]
