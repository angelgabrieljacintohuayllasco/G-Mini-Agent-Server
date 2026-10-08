"""Código QR en la terminal para el enlace de emparejamiento (``gmini://pair?...``)."""

from __future__ import annotations

import segno

FULL, UPPER, LOWER, EMPTY = "█", "▀", "▄", " "


def qr_matrix(payload: str, border: int = 2) -> list[list[bool]]:
    """Matriz del QR con su margen; ``True`` = módulo oscuro."""
    qr = segno.make(payload, error="m")
    return [[bool(cell) for cell in row] for row in qr.matrix_iter(scale=1, border=border)]


def render_half_blocks(matrix: list[list[bool]], *, draw_dark: bool) -> list[str]:
    """Dos filas de módulos por línea de texto con medios bloques.

    ``draw_dark=True`` pinta los módulos oscuros; sirve con colores explícitos
    (negro sobre blanco). ``draw_dark=False`` pinta los claros, que es lo que
    se ve bien en una terminal de fondo oscuro sin colores.
    """
    rows = [row[:] for row in matrix]
    if len(rows) % 2:
        rows.append([False] * len(rows[0]))
    lines = []
    for top, bottom in zip(rows[0::2], rows[1::2], strict=True):
        chars = []
        for upper_dark, lower_dark in zip(top, bottom, strict=True):
            upper = upper_dark if draw_dark else not upper_dark
            lower = lower_dark if draw_dark else not lower_dark
            chars.append(FULL if upper and lower else UPPER if upper else LOWER if lower else EMPTY)
        lines.append("".join(chars))
    return lines


def render_ascii(matrix: list[list[bool]]) -> list[str]:
    """Respaldo para salidas que no pueden mostrar bloques Unicode."""
    return ["".join("##" if dark else "  " for dark in row) for row in matrix]


def can_encode(encoding: str | None, sample: str = FULL + UPPER + LOWER) -> bool:
    try:
        sample.encode(encoding or "ascii")
    except (UnicodeEncodeError, LookupError):
        return False
    return True
