"""Cliente HTTP de la API remota v1 del núcleo de G-Mini Agent.

Contrato: ``docs/protocol/remote-api-v1.md`` en el repositorio del núcleo.
"""

from __future__ import annotations

import base64
import mimetypes
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

from . import __version__
from .errors import ApiError, CliError, ConnectionFailure, parse_error_body
from .events import ChatEvent, events_from_reply
from .profiles import is_loopback_url
from .sse import iter_decoded, iter_sse_events

API_PREFIX = "/api/v1"
USER_AGENT = f"gmini-cli/{__version__}"
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
DEFAULT_TIMEOUT = 30.0
DEFAULT_STREAM_TIMEOUT = 600.0


def api_error_from_response(response: httpx.Response) -> ApiError:
    try:
        body: Any = response.json()
    except ValueError:
        body = None
    code, message = parse_error_body(response.status_code, body)
    return ApiError(response.status_code, code, message)


def ws_url_for(base_url: str) -> str:
    """``http(s)://host[:puerto][/prefijo]`` -> ``ws(s)://.../api/v1/ws``."""
    parts = urlsplit(base_url)
    scheme = "wss" if parts.scheme == "https" else "ws"
    path = parts.path.rstrip("/") + f"{API_PREFIX}/ws"
    return urlunsplit((scheme, parts.netloc, path, "", ""))


def build_attachment(path: Path) -> dict[str, Any]:
    """Prepara un adjunto del chat (``name``, ``mime_type``, ``data_base64``)."""
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise CliError(f"No se puede leer el adjunto {path}: {exc.strerror or exc}") from exc
    if size > MAX_ATTACHMENT_BYTES:
        raise CliError(
            f"El adjunto {path.name} pesa {size / 1048576:.1f} MB; el máximo es "
            f"{MAX_ATTACHMENT_BYTES // 1048576} MB.",
        )
    mime_type, _ = mimetypes.guess_type(path.name)
    return {
        "name": path.name,
        "mime_type": mime_type or "application/octet-stream",
        "data_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
    }


def as_items(payload: Any, *keys: str) -> list[dict[str, Any]]:
    """Normaliza listados: ``{"items": [...]}``, otra clave conocida o una lista directa."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("items", *keys):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


class ApiClient:
    """Cliente síncrono; un objeto por servidor y por comando."""

    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        stream_timeout: float = DEFAULT_STREAM_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
        debug: Callable[[str], None] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._debug = debug
        connect = min(timeout, 10.0)
        self._timeout = httpx.Timeout(timeout, connect=connect)
        self._stream_timeout = httpx.Timeout(
            connect=connect, read=max(stream_timeout, timeout), write=timeout, pool=timeout
        )
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._http = httpx.Client(
            base_url=self.base_url,
            headers=headers,
            timeout=self._timeout,
            transport=transport,
            # Un proxy corporativo en HTTP_PROXY no debe interceptar el núcleo local.
            trust_env=not is_loopback_url(self.base_url),
            follow_redirects=False,
        )

    # ── Infraestructura ─────────────────────────────────────────────────

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> ApiClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _log(self, text: str) -> None:
        if self._debug:
            self._debug(text)

    @property
    def ws_url(self) -> str:
        return ws_url_for(self.base_url)

    def _wrap_transport_error(self, exc: Exception) -> CliError:
        if isinstance(exc, httpx.TimeoutException):
            return ConnectionFailure.timeout(self.base_url)
        if isinstance(exc, httpx.RemoteProtocolError):
            return ConnectionFailure.closed(self.base_url)
        detail = str(exc).strip()
        if "CERTIFICATE_VERIFY_FAILED" in detail:
            return ConnectionFailure(
                f"El certificado TLS de {self.base_url} no es válido.",
                hint="Revisa el proxy con TLS o usa la dirección de Tailscale.",
            )
        return ConnectionFailure.unreachable(self.base_url, detail[:160])

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        content: bytes | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
    ) -> httpx.Response:
        url = f"{API_PREFIX}{path}"
        started = time.perf_counter()
        self._log(f"{method} {self.base_url}{url}")
        try:
            response = self._http.request(
                method,
                url,
                json=json_body,
                content=content,
                params={k: v for k, v in (params or {}).items() if v is not None} or None,
                headers=headers,
                timeout=timeout or self._timeout,
            )
        except httpx.HTTPError as exc:
            raise self._wrap_transport_error(exc) from exc
        self._log(f"<- {response.status_code} en {(time.perf_counter() - started) * 1000:.0f} ms")
        if response.status_code >= 400:
            raise api_error_from_response(response)
        return response

    @staticmethod
    def _json(response: httpx.Response) -> Any:
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise CliError(
                "El servidor devolvió una respuesta que no es JSON.",
                hint="¿La URL apunta al núcleo de G-Mini Agent y no a otro servicio?",
            ) from exc

    def _get_json(self, path: str, **kwargs: Any) -> Any:
        return self._json(self.request("GET", path, **kwargs))

    # ── Estado ──────────────────────────────────────────────────────────

    def health(self) -> dict[str, Any]:
        data = self._get_json("/health")
        if not isinstance(data, dict) or "ok" not in data:
            raise CliError(
                "La respuesta de /api/v1/health no tiene el formato esperado.",
                hint="¿La URL apunta al núcleo de G-Mini Agent?",
            )
        return data

    def timed_health(self) -> tuple[dict[str, Any], float]:
        started = time.perf_counter()
        data = self.health()
        return data, (time.perf_counter() - started) * 1000

    def me(self) -> dict[str, Any]:
        return self._get_json("/me")

    def agent_state(self) -> dict[str, Any]:
        return self._get_json("/agent/state")

    # ── Chat ────────────────────────────────────────────────────────────

    def chat(
        self, message: str, *, session_id: str | None = None, attachments: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"message": message, "session_id": session_id, "stream": False}
        if attachments:
            body["attachments"] = attachments
        return self._json(self.request("POST", "/chat", json_body=body, timeout=self._stream_timeout))

    def chat_stream(
        self, message: str, *, session_id: str | None = None, attachments: list[dict[str, Any]] | None = None
    ) -> Iterator[ChatEvent]:
        """Envía un mensaje y entrega los eventos SSE a medida que llegan."""
        body: dict[str, Any] = {"message": message, "session_id": session_id, "stream": True}
        if attachments:
            body["attachments"] = attachments
        url = f"{API_PREFIX}/chat"
        self._log(f"POST {self.base_url}{url} (SSE)")
        try:
            with self._http.stream(
                "POST", url, json=body, headers={"Accept": "text/event-stream"}, timeout=self._stream_timeout
            ) as response:
                if response.status_code >= 400:
                    response.read()
                    raise api_error_from_response(response)
                content_type = response.headers.get("content-type", "")
                if "text/event-stream" not in content_type:
                    # El servidor ignoró el streaming: respuesta JSON completa.
                    response.read()
                    yield from events_from_reply(self._json(response))
                    return
                for sse in iter_sse_events(iter_decoded(response.iter_bytes())):
                    yield ChatEvent.from_sse(sse)
        except httpx.HTTPError as exc:
            raise self._wrap_transport_error(exc) from exc

    def sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        return as_items(self._get_json("/sessions", params={"limit": limit}), "sessions")

    def session_messages(self, session_id: str, limit: int = 100) -> list[dict[str, Any]]:
        path = f"/sessions/{quote(session_id, safe='')}/messages"
        return as_items(self._get_json(path, params={"limit": limit}), "messages")

    # ── Tareas ──────────────────────────────────────────────────────────

    def create_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._json(self.request("POST", "/tasks", json_body=payload))

    def list_tasks(self, status: str | None = None) -> list[dict[str, Any]]:
        return as_items(self._get_json("/tasks", params={"status": status}), "tasks")

    def get_task(self, task_id: str) -> dict[str, Any]:
        return self._get_json(f"/tasks/{quote(task_id, safe='')}")

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        response = self.request("DELETE", f"/tasks/{quote(task_id, safe='')}")
        data = self._json(response)
        return data if isinstance(data, dict) else {}

    # ── Voz ─────────────────────────────────────────────────────────────

    def tts(self, text: str, *, voice: str | None = None, fmt: str = "wav") -> tuple[bytes, str]:
        response = self.request(
            "POST",
            "/voice/tts",
            json_body={"text": text, "voice": voice, "format": fmt},
            headers={"Accept": "audio/*"},
            timeout=self._stream_timeout,
        )
        return response.content, response.headers.get("content-type", "audio/wav")

    def stt(self, audio: bytes, *, content_type: str = "audio/wav") -> dict[str, Any]:
        response = self.request(
            "POST",
            "/voice/stt",
            content=audio,
            headers={"Content-Type": content_type},
            timeout=self._stream_timeout,
        )
        data = self._json(response)
        return data if isinstance(data, dict) else {}

    def wake(self, audio: bytes, *, content_type: str = "audio/wav") -> dict[str, Any]:
        """Detecta la palabra de activación en un clip corto (``/voice/wake``)."""
        response = self.request(
            "POST",
            "/voice/wake",
            content=audio,
            headers={"Content-Type": content_type},
            timeout=self._stream_timeout,
        )
        data = self._json(response)
        return data if isinstance(data, dict) else {}

    # ── Aprobaciones ────────────────────────────────────────────────────

    def resolve_approval(self, approve: bool) -> dict[str, Any]:
        data = self._json(self.request("POST", "/approvals", json_body={"approve": approve}))
        return data if isinstance(data, dict) else {}

    # ── Emparejamiento y dispositivos ───────────────────────────────────

    def create_pairing(self, *, label: str, device_type: str, scopes: list[str]) -> dict[str, Any]:
        body = {"label": label, "device_type": device_type, "scopes": scopes}
        return self._json(self.request("POST", "/pairing", json_body=body))

    def claim_pairing(
        self, *, code: str, device_name: str, device_type: str, platform: str
    ) -> dict[str, Any]:
        body = {"code": code, "device_name": device_name, "device_type": device_type, "platform": platform}
        return self._json(self.request("POST", "/pairing/claim", json_body=body))

    def devices(self) -> list[dict[str, Any]]:
        return as_items(self._get_json("/devices"), "devices")

    def revoke_device(self, device_id: str) -> None:
        self.request("DELETE", f"/devices/{quote(device_id, safe='')}")
