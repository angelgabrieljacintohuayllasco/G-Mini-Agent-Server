"""Programación de tareas: ``--cron``, ``--every`` y ``--at``, y la zona horaria local."""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .errors import UsageError

_DURATION = re.compile(r"^\s*(\d+)\s*([smhd]?)\s*$", re.IGNORECASE)
_UNITS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}
MIN_INTERVAL_SECONDS = 60  # el núcleo rechaza intervalos menores
_IANA = re.compile(r"^(UTC|GMT|[A-Za-z_]+(?:/[A-Za-z0-9_+\-]+)+)$")

# Zona de Windows -> zona IANA (territorio "001" de CLDR windowsZones).
WINDOWS_ZONES = {
    "SA Pacific Standard Time": "America/Bogota",
    "Pacific SA Standard Time": "America/Santiago",
    "Argentina Standard Time": "America/Argentina/Buenos_Aires",
    "E. South America Standard Time": "America/Sao_Paulo",
    "SA Eastern Standard Time": "America/Cayenne",
    "SA Western Standard Time": "America/La_Paz",
    "Venezuela Standard Time": "America/Caracas",
    "Paraguay Standard Time": "America/Asuncion",
    "Montevideo Standard Time": "America/Montevideo",
    "Central America Standard Time": "America/Guatemala",
    "Central Standard Time (Mexico)": "America/Mexico_City",
    "Mountain Standard Time (Mexico)": "America/Mazatlan",
    "Pacific Standard Time (Mexico)": "America/Tijuana",
    "Eastern Standard Time (Mexico)": "America/Cancun",
    "Cuba Standard Time": "America/Havana",
    "Eastern Standard Time": "America/New_York",
    "Central Standard Time": "America/Chicago",
    "Mountain Standard Time": "America/Denver",
    "US Mountain Standard Time": "America/Phoenix",
    "Pacific Standard Time": "America/Los_Angeles",
    "Alaskan Standard Time": "America/Anchorage",
    "Hawaiian Standard Time": "Pacific/Honolulu",
    "Atlantic Standard Time": "America/Halifax",
    "Canada Central Standard Time": "America/Regina",
    "UTC": "UTC",
    "GMT Standard Time": "Europe/London",
    "Greenwich Standard Time": "Atlantic/Reykjavik",
    "W. Europe Standard Time": "Europe/Berlin",
    "Romance Standard Time": "Europe/Paris",
    "Central Europe Standard Time": "Europe/Budapest",
    "Central European Standard Time": "Europe/Warsaw",
    "GTB Standard Time": "Europe/Bucharest",
    "FLE Standard Time": "Europe/Kiev",
    "Russian Standard Time": "Europe/Moscow",
    "Turkey Standard Time": "Europe/Istanbul",
    "Israel Standard Time": "Asia/Jerusalem",
    "Arabian Standard Time": "Asia/Dubai",
    "India Standard Time": "Asia/Kolkata",
    "China Standard Time": "Asia/Shanghai",
    "Tokyo Standard Time": "Asia/Tokyo",
    "Korea Standard Time": "Asia/Seoul",
    "Singapore Standard Time": "Asia/Singapore",
    "AUS Eastern Standard Time": "Australia/Sydney",
}

# Una misma zona de Windows cubre varios países; el país del usuario la precisa.
WINDOWS_ZONES_BY_COUNTRY = {
    ("SA Pacific Standard Time", "PE"): "America/Lima",
    ("SA Pacific Standard Time", "EC"): "America/Guayaquil",
    ("SA Pacific Standard Time", "PA"): "America/Panama",
    ("SA Western Standard Time", "DO"): "America/Santo_Domingo",
    ("SA Western Standard Time", "PR"): "America/Puerto_Rico",
    ("Central America Standard Time", "CR"): "America/Costa_Rica",
    ("Central America Standard Time", "SV"): "America/El_Salvador",
    ("Central America Standard Time", "HN"): "America/Tegucigalpa",
    ("Central America Standard Time", "NI"): "America/Managua",
    ("Romance Standard Time", "ES"): "Europe/Madrid",
}


def parse_every(value: str) -> int:
    """``3600``, ``90s``, ``15m``, ``2h`` o ``1d`` -> segundos."""
    match = _DURATION.match(value or "")
    if not match:
        raise UsageError(
            f"Intervalo no válido: {value!r}",
            hint="Usa segundos (3600) o un sufijo: 90s, 15m, 2h, 1d.",
        )
    seconds = int(match.group(1)) * _UNITS[match.group(2).lower()]
    if seconds < MIN_INTERVAL_SECONDS:
        raise UsageError(f"El intervalo mínimo es de {MIN_INTERVAL_SECONDS} segundos.")
    return seconds


def parse_at(value: str, *, now: datetime | None = None) -> str:
    """Fecha ISO 8601; sin zona se interpreta en la hora local. Devuelve ISO con zona."""
    text = (value or "").strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as exc:
        raise UsageError(
            f"Fecha no válida para --at: {value!r}",
            hint="Usa ISO 8601, por ejemplo 2026-10-08T08:00 o 2026-10-08T08:00:00-05:00.",
        ) from exc
    if moment.tzinfo is None:
        moment = moment.astimezone()
    reference = now or datetime.now(timezone.utc)
    if moment <= reference:
        raise UsageError(f"La fecha de --at ya pasó: {moment.isoformat()}")
    return moment.isoformat()


def validate_cron(expression: str) -> str:
    fields = (expression or "").split()
    if len(fields) != 5:
        raise UsageError(
            f"Expresión cron no válida: {expression!r}",
            hint='Usa 5 campos: minuto hora día mes día-semana, por ejemplo "0 8 * * *".',
        )
    return " ".join(fields)


def validate_timezone(name: str) -> str:
    name = (name or "").strip()
    if not _IANA.match(name):
        raise UsageError(
            f"Zona horaria no válida: {name!r}", hint="Usa un nombre IANA, por ejemplo America/Lima."
        )
    return name


def _windows_country(env: Mapping[str, str]) -> str | None:
    override = env.get("GMINI_COUNTRY")
    if override:
        return override.upper()
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        buffer = ctypes.create_unicode_buffer(85)
        if ctypes.windll.kernel32.GetUserDefaultLocaleName(buffer, len(buffer)):
            parts = buffer.value.replace("_", "-").split("-")
            if len(parts) >= 2 and len(parts[-1]) == 2:
                return parts[-1].upper()
    except (OSError, AttributeError):
        return None
    return None


def _windows_zone_key() -> str | None:
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\TimeZoneInformation"
        ) as key:
            value, _ = winreg.QueryValueEx(key, "TimeZoneKeyName")
            return str(value).strip("\x00 ") or None
    except (OSError, ImportError):
        return None


def windows_to_iana(zone_key: str, country: str | None = None) -> str | None:
    if country:
        specific = WINDOWS_ZONES_BY_COUNTRY.get((zone_key, country.upper()))
        if specific:
            return specific
    return WINDOWS_ZONES.get(zone_key)


def local_timezone(env: Mapping[str, str] | None = None, platform: str = sys.platform) -> str | None:
    """Nombre IANA de la zona local, o ``None`` si no se puede saber."""
    env = os.environ if env is None else env
    tz = env.get("TZ", "").lstrip(":")
    if tz and _IANA.match(tz):
        return tz
    if platform == "win32":
        key = _windows_zone_key()
        return windows_to_iana(key, _windows_country(env)) if key else None
    timezone_file = Path("/etc/timezone")
    try:
        name = timezone_file.read_text(encoding="utf-8").strip()
        if _IANA.match(name):
            return name
    except OSError:
        pass
    try:
        target = os.path.realpath("/etc/localtime")
    except OSError:
        return None
    marker = "zoneinfo/"
    if marker in target:
        name = target.split(marker, 1)[1]
        if _IANA.match(name):
            return name
    return None


def build_schedule(
    *,
    cron: str | None,
    tz: str | None,
    every: str | None,
    at: str | None,
    env: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """Construye ``schedule`` del contrato y una descripción legible."""
    if tz and not cron:
        raise UsageError("--tz solo se usa junto con --cron.")
    if cron:
        expression = validate_cron(cron)
        zone = validate_timezone(tz) if tz else local_timezone(env)
        if not zone:
            raise UsageError(
                "No pude detectar tu zona horaria.",
                hint="Indícala con --tz, por ejemplo --tz America/Lima.",
            )
        return {"cron": expression, "timezone": zone}, f"cron «{expression}» ({zone})"
    if every:
        seconds = parse_every(every)
        return {"interval_seconds": seconds}, f"cada {describe_seconds(seconds)}"
    if at:
        moment = parse_at(at)
        return {"at": moment}, f"una vez, el {moment}"
    return None, "una vez, ahora"


def describe_seconds(seconds: int) -> str:
    for size, singular, plural in (
        (86400, "día", "días"),
        (3600, "hora", "horas"),
        (60, "minuto", "minutos"),
    ):
        if seconds >= size and seconds % size == 0:
            amount = seconds // size
            return f"{amount} {singular if amount == 1 else plural}"
    return f"{seconds} segundo{'s' if seconds != 1 else ''}"


def format_moment(value: Any) -> str:
    """ISO 8601 o época en segundos -> ``AAAA-MM-DD HH:MM``.

    Las fechas con zona y las épocas se pasan a la hora local. Una fecha sin
    zona se muestra tal cual: el núcleo v0.2 devuelve así la hora local del
    servidor y no hay forma fiable de convertirla.
    """
    if value in (None, ""):
        return "-"
    moment: datetime | None = None
    if isinstance(value, (int, float)):
        moment = datetime.fromtimestamp(float(value), tz=timezone.utc)
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            try:
                moment = datetime.fromtimestamp(float(text), tz=timezone.utc)
            except ValueError:
                return value
    if moment is None:
        return str(value)
    if moment.tzinfo is not None:
        moment = moment.astimezone()
    return moment.strftime("%Y-%m-%d %H:%M")


def relative_expiry(value: Any, *, now: datetime | None = None) -> str:
    """``vence en 4 min 50 s`` para un ``expires_at`` ISO o en época."""
    reference = now or datetime.now(timezone.utc)
    moment: datetime | None = None
    if isinstance(value, (int, float)):
        moment = datetime.fromtimestamp(float(value), tz=timezone.utc)
    elif isinstance(value, str):
        text = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            return ""
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
    if moment is None:
        return ""
    remaining = moment - reference
    if remaining <= timedelta(0):
        return "vencido"
    minutes, seconds = divmod(int(remaining.total_seconds()), 60)
    return f"vence en {minutes} min {seconds:02d} s" if minutes else f"vence en {seconds} s"
