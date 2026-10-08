from __future__ import annotations

import pytest

from gmini_cli.errors import (
    EXIT_AUTH,
    EXIT_BUSY,
    EXIT_CONNECTION,
    EXIT_ERROR,
    ApiError,
    ConnectionFailure,
    parse_error_body,
)


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (
            401,
            {"error": {"code": "invalid_token", "message": "Token ausente o inválido"}},
            ("invalid_token", "Token ausente o inválido"),
        ),
        (
            403,
            {"error": {"code": "invalid_host", "message": "Host no permitido"}},
            ("invalid_host", "Host no permitido"),
        ),
        (409, {"error": {"code": "busy"}}, ("busy", "")),
        (404, {"detail": "Not Found"}, ("route_not_found", "")),
        (400, {"detail": "algo raro"}, ("bad_request", "algo raro")),
        (
            422,
            {"detail": [{"loc": ["body", "prompt"], "msg": "Field required"}]},
            ("validation_error", "prompt: Field required"),
        ),
        (500, None, ("internal_error", "")),
        (503, None, ("provider_unavailable", "")),
        (418, "texto", ("error", "")),
    ],
)
def test_parse_error_body(status: int, body: object, expected: tuple[str, str]) -> None:
    assert parse_error_body(status, body) == expected


@pytest.mark.parametrize(
    ("code", "exit_code"),
    [
        ("invalid_token", EXIT_AUTH),
        ("missing_scope", EXIT_AUTH),
        ("invalid_code", EXIT_AUTH),
        ("busy", EXIT_BUSY),
        ("rate_limited", EXIT_BUSY),
        ("not_ready", EXIT_BUSY),
        ("provider_unavailable", EXIT_ERROR),
        ("agent_error", EXIT_ERROR),
    ],
)
def test_api_error_exit_codes(code: str, exit_code: int) -> None:
    assert ApiError(400, code).exit_code == exit_code


def test_api_error_messages_are_spanish_and_include_server_detail() -> None:
    error = ApiError(409, "busy", "El agente está ocupado con otra conversación.")
    assert error.message.startswith("El agente está ocupado")
    assert "Detalle del servidor" not in error.message
    assert "gmini task add" in (error.hint or "")

    detailed = ApiError(422, "validation_error", "interval_seconds debe ser >= 60.")
    assert "interval_seconds debe ser >= 60." in detailed.message
    assert detailed.to_json()["error"]["status"] == 422


def test_unknown_code_keeps_status() -> None:
    error = ApiError(418, "teapot", "")
    assert "HTTP 418" in error.message


def test_connection_failures_exit_code() -> None:
    assert ConnectionFailure.unreachable("http://x:8765").exit_code == EXIT_CONNECTION
    assert "no respondió" in ConnectionFailure.timeout("http://x:8765").message
