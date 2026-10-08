"""Errores de la CLI y su traducción a mensajes claros en español.

Los códigos de error vienen del contrato de la API remota v1
(``{"error": {"code": ..., "message": ...}}``). Cada uno se traduce a un
mensaje, una sugerencia de qué hacer y un código de salida estable para
scripts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Códigos de salida documentados en docs/cli.md.
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_CONNECTION = 3
EXIT_AUTH = 4
EXIT_BUSY = 5
EXIT_INTERRUPTED = 130

STATUS_TO_CODE = {
    400: "bad_request",
    401: "invalid_token",
    403: "missing_scope",
    404: "not_found",
    409: "busy",
    422: "validation_error",
    429: "rate_limited",
    503: "provider_unavailable",
}

ERROR_MESSAGES = {
    "bad_request": "La petición no es válida.",
    "invalid_token": "El token no es válido o fue revocado.",
    "missing_scope": "El token no tiene permiso para esta acción.",
    "not_found": "No se encontró lo que pediste.",
    "busy": "El agente está ocupado con otra conversación.",
    "validation_error": "Los datos enviados no son válidos.",
    "rate_limited": "Demasiados intentos seguidos.",
    "provider_unavailable": "El proveedor de IA no está disponible en este momento.",
    "invalid_host": "El núcleo rechazó la conexión por el nombre de host usado.",
    "invalid_code": "El código de emparejamiento no es válido o ya venció.",
    "not_ready": "El agente todavía está iniciando.",
    "agent_error": "El agente tuvo un error al responder.",
    "cancelled": "La respuesta se canceló.",
    "internal_error": "El servidor tuvo un error interno.",
    "route_not_found": "Este servidor no ofrece esa función de la API remota v1.",
}

ERROR_HINTS = {
    "invalid_token": (
        "Empareja de nuevo con 'gmini pair <host> <código>' o revisa el perfil con 'gmini profiles list'."
    ),
    "missing_scope": "Pide un token con el alcance necesario (Ajustes > Dispositivos en la app).",
    "busy": "Reintenta en unos segundos o encola el pedido con 'gmini task add'.",
    "rate_limited": "Espera un minuto antes de volver a intentarlo.",
    "provider_unavailable": "Revisa la API key y el proveedor de IA del servidor (variables GMINI_KEY_*).",
    "agent_error": "Si el detalle habla de una API key, configúrala en el servidor (variables GMINI_KEY_*).",
    "not_ready": "Espera unos segundos y vuelve a intentarlo.",
    "invalid_host": (
        "Conéctate con 127.0.0.1 o agrega el nombre a server.allowed_hosts en la configuración del núcleo."
    ),
    "invalid_code": "Los códigos duran 5 minutos y sirven una sola vez. Genera otro con 'gmini pair-code'.",
    "route_not_found": "Actualiza el núcleo de G-Mini Agent a una versión con la API remota v1.",
    "internal_error": "Revisa los registros del núcleo ('gmini server logs' si lo instalaste con gmini).",
}

AUTH_CODES = {"invalid_token", "missing_scope", "invalid_host", "invalid_code"}
RETRYABLE_CODES = {"busy", "rate_limited", "not_ready"}


class CliError(Exception):
    """Error que se muestra al usuario sin traza, con una sugerencia opcional."""

    exit_code = EXIT_ERROR

    def __init__(
        self, message: str, *, hint: str | None = None, exit_code: int | None = None, code: str = "error"
    ) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.code = code
        if exit_code is not None:
            self.exit_code = exit_code

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.hint:
            payload["hint"] = self.hint
        return {"error": payload}


class UsageError(CliError):
    exit_code = EXIT_USAGE

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message, hint=hint, code="usage")


class ConnectionFailure(CliError):
    """No se pudo hablar con el servidor (red, DNS, TLS, tiempo de espera)."""

    exit_code = EXIT_CONNECTION

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message, hint=hint, code="connection_failed")

    @classmethod
    def unreachable(cls, url: str, detail: str = "") -> ConnectionFailure:
        suffix = f" ({detail})" if detail else ""
        return cls(
            f"No se pudo conectar con {url}{suffix}.",
            hint="Comprueba que el núcleo esté encendido, la URL y el puerto, y tu conexión "
            "(Tailscale/VPN si el servidor es remoto).",
        )

    @classmethod
    def timeout(cls, url: str) -> ConnectionFailure:
        return cls(
            f"El servidor {url} no respondió a tiempo.",
            hint="Reintenta o aumenta el límite con --timeout.",
        )

    @classmethod
    def closed(cls, url: str) -> ConnectionFailure:
        return cls(
            f"La conexión con {url} se cerró antes de terminar la respuesta.",
            hint="Revisa los registros del núcleo y vuelve a intentarlo.",
        )


@dataclass
class ErrorDetails:
    status: int
    code: str
    server_message: str


class ApiError(CliError):
    """Respuesta de error de la API remota v1."""

    def __init__(self, status: int, code: str, server_message: str = "") -> None:
        self.status = status
        self.details = ErrorDetails(status, code, server_message)
        base = ERROR_MESSAGES.get(code) or f"El servidor respondió con un error (HTTP {status})."
        message = base
        if server_message and server_message.strip().rstrip(".").lower() != base.rstrip(".").lower():
            message = f"{base} Detalle del servidor: {server_message.strip()}"
        if code in AUTH_CODES:
            exit_code = EXIT_AUTH
        elif code in RETRYABLE_CODES:
            exit_code = EXIT_BUSY
        else:
            exit_code = EXIT_ERROR
        super().__init__(message, hint=ERROR_HINTS.get(code), exit_code=exit_code, code=code)
        self.server_message = server_message

    def to_json(self) -> dict[str, Any]:
        payload = super().to_json()
        payload["error"]["status"] = self.status
        if self.server_message:
            payload["error"]["server_message"] = self.server_message
        return payload


def parse_error_body(status: int, body: Any) -> tuple[str, str]:
    """Extrae ``(code, message)`` del cuerpo de una respuesta de error.

    Acepta el formato del contrato y, por compatibilidad, el ``{"detail": ...}``
    que FastAPI devuelve en rutas inexistentes o validaciones automáticas.
    """
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            code = str(error.get("code") or STATUS_TO_CODE.get(status, "error"))
            return code, str(error.get("message") or "")
        if isinstance(error, str):
            return STATUS_TO_CODE.get(status, "error"), error
        detail = body.get("detail")
        if status == 404 and detail == "Not Found":
            return "route_not_found", ""
        if isinstance(detail, str):
            return STATUS_TO_CODE.get(status, "error"), detail
        if isinstance(detail, list):
            return "validation_error", _summarize_validation(detail)
    if status == 404:
        return "route_not_found", ""
    if status >= 500 and status not in STATUS_TO_CODE:
        return "internal_error", ""
    return STATUS_TO_CODE.get(status, "error"), ""


def _summarize_validation(items: list[Any]) -> str:
    parts = []
    for item in items[:3]:
        if isinstance(item, dict):
            loc = ".".join(str(p) for p in item.get("loc", []) if p != "body")
            msg = str(item.get("msg") or "")
            parts.append(f"{loc}: {msg}" if loc else msg)
    return "; ".join(p for p in parts if p)
