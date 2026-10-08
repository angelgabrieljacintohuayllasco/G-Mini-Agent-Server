"""Reproducción de audio sin dependencias extra."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def sniff_extension(data: bytes) -> str:
    """Extensión según la cabecera: el contrato promete WAV, pero se comprueba."""
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return ".wav"
    if data[:3] == b"ID3" or (len(data) > 1 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0):
        return ".mp3"
    if data[:4] == b"OggS":
        return ".ogg"
    if data[:4] == b"fLaC":
        return ".flac"
    return ".bin"


def _players(path: Path, platform: str) -> list[list[str]]:
    if platform == "darwin":
        return [["afplay", str(path)]]
    return [
        ["paplay", str(path)],
        ["pw-play", str(path)],
        ["aplay", "-q", str(path)],
        ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)],
    ]


def play_file(path: Path, *, platform: str = sys.platform) -> bool:
    """Reproduce ``path`` y espera a que termine. ``False`` si no hay reproductor."""
    if platform == "win32":
        if path.suffix.lower() == ".wav":
            import winsound

            try:
                winsound.PlaySound(str(path), winsound.SND_FILENAME)
            except RuntimeError:
                return False
            return True
        return False
    for command in _players(path, platform):
        if shutil.which(command[0]):
            completed = subprocess.run(command, check=False, stdin=subprocess.DEVNULL)
            if completed.returncode == 0:
                return True
    return False
