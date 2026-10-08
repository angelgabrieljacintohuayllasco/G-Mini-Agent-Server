from __future__ import annotations

from gmini_cli.qr import EMPTY, FULL, can_encode, qr_matrix, render_ascii, render_half_blocks

LINK = "gmini://pair?host=100.71.131.70&port=8765&code=482913"


def test_matrix_is_square_with_quiet_zone() -> None:
    matrix = qr_matrix(LINK, border=2)
    assert len(matrix) == len(matrix[0])
    assert not any(matrix[0])
    assert not any(matrix[-1])


def test_half_blocks_use_two_rows_per_line() -> None:
    matrix = qr_matrix(LINK)
    lines = render_half_blocks(matrix, draw_dark=True)
    assert len(lines) == (len(matrix) + 1) // 2
    assert all(len(line) == len(matrix[0]) for line in lines)
    assert lines[0] == EMPTY * len(matrix[0])


def test_inverted_rendering_is_the_complement() -> None:
    matrix = qr_matrix(LINK)
    inverted = render_half_blocks(matrix, draw_dark=False)
    assert inverted[0] == FULL * len(matrix[0])


def test_ascii_fallback_doubles_width() -> None:
    matrix = qr_matrix(LINK)
    lines = render_ascii(matrix)
    assert len(lines) == len(matrix)
    assert len(lines[0]) == 2 * len(matrix[0])


def test_can_encode() -> None:
    assert can_encode("utf-8")
    assert can_encode("cp437")
    assert not can_encode("cp1252")
    assert not can_encode("encoding-inexistente")
