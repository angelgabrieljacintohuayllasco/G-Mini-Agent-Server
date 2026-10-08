"""``gmini say`` (texto a voz) y ``gmini transcribe`` (voz a texto)."""

from __future__ import annotations

import argparse
import contextlib
import mimetypes
import sys
import tempfile
import time
import wave
from pathlib import Path

from ..audio import play_file, sniff_extension
from ..console import read_stdin_text
from ..context import AppContext
from ..errors import EXIT_ERROR, EXIT_OK, CliError, UsageError

MAX_AUDIO_BYTES = 25 * 1024 * 1024


def _human_size(size: int) -> str:
    return f"{size / 1024:.0f} KB" if size < 1024 * 1024 else f"{size / 1048576:.1f} MB"


def cmd_say(ctx: AppContext, args: argparse.Namespace) -> int:
    out = ctx.out
    text = " ".join(args.text or []).strip()
    if (not text or text == "-") and not out.stdin_is_tty():
        text = read_stdin_text().strip()
    if not text:
        raise UsageError("Falta el texto que el agente debe decir.")

    with ctx.client() as client, out.spinning("Sintetizando voz..."):
        audio, _content_type = client.tts(text, voice=args.voice)
    if not audio:
        raise CliError("El servidor devolvió un audio vacío.")
    extension = sniff_extension(audio)

    if args.out == "-":
        sys.stdout.buffer.write(audio)
        sys.stdout.buffer.flush()
        return EXIT_OK

    if args.out:
        path = Path(args.out).expanduser()
        if path.suffix.lower() != extension and extension != ".bin":
            out.warn(f"El audio es {extension[1:].upper()}, aunque el archivo se llame {path.name}.")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(audio)
        if out.json_mode:
            out.json({"path": str(path), "bytes": len(audio), "format": extension[1:]})
        else:
            out.success(f"Audio guardado en {path} ({_human_size(len(audio))}).")
        if args.play:
            _play(ctx, path, keep=True)
        return EXIT_OK

    fd, name = tempfile.mkstemp(prefix=f"gmini-say-{int(time.time())}-", suffix=extension)
    path = Path(name)
    with open(fd, "wb") as handle:
        handle.write(audio)
    _play(ctx, path, keep=False)
    return EXIT_OK


def _play(ctx: AppContext, path: Path, *, keep: bool) -> None:
    if play_file(path):
        if not keep:
            path.unlink(missing_ok=True)
        return
    ctx.out.warn(f"No encontré cómo reproducir el audio en este equipo; quedó en {path}")
    ctx.out.note('Guárdalo donde quieras con: gmini say "texto" --out archivo.wav')


def cmd_transcribe(ctx: AppContext, args: argparse.Namespace) -> int:
    out = ctx.out
    data, content_type = _read_audio(Path(args.file).expanduser())
    with ctx.client() as client, out.spinning("Transcribiendo..."):
        result = client.stt(data, content_type=content_type)
    text = str(result.get("text") or "").strip()
    if out.json_mode:
        out.json(result)
    elif text:
        out.line(text)
    else:
        out.warn(
            "El servidor no reconoció texto en el audio. Si esperabas texto, revisa que el STT esté "
            "activo en el núcleo (voice.stt_enabled y el paquete faster-whisper)."
        )
    return EXIT_OK


WAKE_MAX_SECONDS = 4.0


def _read_audio(path: Path) -> tuple[bytes, str]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise CliError(f"No se puede leer {path}: {exc.strerror or exc}") from exc
    if size > MAX_AUDIO_BYTES:
        raise CliError(f"El audio pesa {_human_size(size)}; el máximo es {_human_size(MAX_AUDIO_BYTES)}.")
    data = path.read_bytes()
    if sniff_extension(data) == ".wav":
        return data, "audio/wav"
    return data, mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def wav_seconds(path: Path) -> float | None:
    with contextlib.suppress(OSError, EOFError, wave.Error), wave.open(str(path), "rb") as handle:
        return handle.getnframes() / float(handle.getframerate() or 1)
    return None


def cmd_wake(ctx: AppContext, args: argparse.Namespace) -> int:
    """Pregunta al servidor si el clip empieza con la palabra de activación."""
    out = ctx.out
    path = Path(args.file).expanduser()
    data, content_type = _read_audio(path)
    seconds = wav_seconds(path) if content_type == "audio/wav" else None
    if seconds is not None and seconds > WAKE_MAX_SECONDS:
        out.warn(f"El clip dura {seconds:.1f} s; para la palabra de activación conviene menos de 4 s.")
    with ctx.client() as client, out.spinning("Escuchando..."):
        result = client.wake(data, content_type=content_type)
    detected = bool(result.get("wake"))
    if out.json_mode:
        out.json(result)
    elif detected:
        out.success(f"Palabra de activación detectada: «{result.get('phrase') or '-'}».")
        command = str(result.get("command") or "").strip()
        out.line(f"Pedido: {command}" if command else "Pedido: (vacío; el agente espera la orden)")
    else:
        out.line("No se detectó la palabra de activación.")
    transcript = str(result.get("transcript") or "").strip()
    if transcript and not out.json_mode:
        out.note(f"Transcripción: {transcript}")
    return EXIT_OK if detected else EXIT_ERROR
