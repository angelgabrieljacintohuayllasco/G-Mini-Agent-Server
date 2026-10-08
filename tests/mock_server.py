"""Servidor falso de la API remota v1 de G-Mini para probar la CLI sin red.

Imita al núcleo real (``backend/api/v1.py`` del repositorio principal):
formato de errores ``{"error": {"code", "message"}}``, SSE con ``event:`` y
``data:``, WebSocket que se cierra con 4401 si el token no vale, tareas que
rechazan ``at`` e intervalos menores de 60 s, emparejamiento de un solo uso.

También se puede levantar a mano para probar la CLI::

    python tests/mock_server.py --port 8799 --home /tmp/gmini-home
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import secrets
import socket
import threading
import time
import wave
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response, StreamingResponse

AGENT_NAME = "G-Mini"
SERVER_NAME = "mock-server"
VERSION = "0.2.0"
ALL_SCOPES = ["chat", "voice", "tasks", "node", "admin"]
SESSION_ID = "ses_20261007_000501_ab12"


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def tiny_wav(milliseconds: int = 100, rate: int = 16000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * (rate * milliseconds // 1000))
    return buffer.getvalue()


class MockError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@dataclass
class Device:
    id: str
    name: str
    kind: str
    device_type: str
    platform: str
    scopes: list[str]
    token: str
    created_at: float = field(default_factory=time.time)
    last_seen_at: float = 0.0

    def public(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("token")
        return data


class MockCore:
    """Estado del servidor falso; las pruebas lo inspeccionan y lo modifican."""

    def __init__(self, session_token: str | None = None) -> None:
        self.session_token = session_token or secrets.token_urlsafe(32)
        self.reset()

    def reset(self) -> None:
        self.devices: dict[str, Device] = {}
        self.codes: dict[str, dict[str, Any]] = {}
        self.tasks: dict[str, dict[str, Any]] = {}
        self.busy = False
        self.approval_pending = False
        self.approvals: list[bool] = []
        self.cancelled = threading.Event()
        self.ws_frames: list[dict[str, Any]] = []
        self.chat_bodies: list[dict[str, Any]] = []
        self.stt_bodies: list[bytes] = []
        self.sse_newline = "\n"
        self.mode = "server"
        self.stt_silent = False

    # Tokens

    def issue(
        self,
        name: str,
        *,
        scopes: list[str],
        kind: str = "device",
        device_type: str = "cli",
        platform: str = "",
    ) -> tuple[str, Device]:
        prefix = "gm_api_" if kind == "api" else "gm_dev_"
        token = prefix + secrets.token_urlsafe(24)
        device = Device(
            id=("api_" if kind == "api" else "dev_") + secrets.token_hex(6),
            name=name,
            kind=kind,
            device_type=device_type,
            platform=platform,
            scopes=scopes,
            token=token,
        )
        self.devices[device.id] = device
        return token, device

    def auth(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        if secrets.compare_digest(token, self.session_token):
            return {"kind": "session", "scopes": ALL_SCOPES, "device_id": "", "device_name": ""}
        for device in self.devices.values():
            if secrets.compare_digest(token, device.token):
                device.last_seen_at = time.time()
                return {
                    "kind": device.kind,
                    "scopes": device.scopes,
                    "device_id": device.id,
                    "device_name": device.name,
                }
        return None

    def pairing_code(self, scopes: list[str]) -> str:
        code = f"{secrets.randbelow(1_000_000):06d}"
        self.codes[code] = {
            "scopes": [s for s in scopes if s in ALL_SCOPES and s != "admin"],
            "expires_at": time.time() + 300,
        }
        return code

    # Chat

    def script(self, message: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Eventos que el agente emite para ``message`` y el resultado final."""
        text = message.lower()
        start = {"type": "start", "session_id": SESSION_ID}
        thinking = {"type": "state", "status": "thinking", "emotion": "thinking"}
        if "falla" in text:
            error = {"code": "provider_unavailable", "message": "No hay API key para el proveedor."}
            return [start, thinking, {"type": "error", **error}], {"error": error}
        events: list[dict[str, Any]] = [start, thinking]
        actions: list[dict[str, Any]] = []
        notices: list[str] = []
        if "acción" in text or "accion" in text:
            events.append({"type": "chunk", "text": "Voy a crear la tarea. "})
            events.append(
                {
                    "type": "action",
                    "action": "schedule_create_job",
                    "params": {"cron": "0 18 * * *"},
                    "id": "a1",
                }
            )
            events.append(
                {
                    "type": "action_result",
                    "action": "schedule_create_job",
                    "success": True,
                    "message": "Tarea creada",
                    "id": "a1",
                }
            )
            notices.append("Acciones ejecutadas: schedule_create_job")
            events.append({"type": "notice", "kind": "system", "text": notices[0]})
            actions.append(
                {
                    "action": "schedule_create_job",
                    "params": {"cron": "0 18 * * *"},
                    "success": True,
                    "message": "Tarea creada",
                }
            )
            reply = "Listo, te aviso a las 18:00."
        elif "aprobar" in text:
            self.approval_pending = True
            events.append(
                {
                    "type": "approval",
                    "pending": True,
                    "summary": "Borrar 3 archivos de Descargas",
                    "kind": "approval",
                }
            )
            reply = "Necesito tu aprobación para borrar los archivos."
        elif "lento" in text:
            events.append({"type": "chunk", "text": "Pensando con calma"})
            events.append({"type": "wait"})
            reply = "Pensando con calma... listo."
        else:
            reply = "Hola, soy G-Mini. ¿En qué te ayudo?"
        words = reply.split(" ")
        for index, word in enumerate(words):
            events.append({"type": "chunk", "text": word + (" " if index < len(words) - 1 else "")})
        result: dict[str, Any] = {"session_id": SESSION_ID, "reply": reply, "actions": actions}
        if notices:
            result["notices"] = notices
        if self.approval_pending:
            result["approval_pending"] = True
        return events, result


def _bearer(headers: Any, query: Any | None = None) -> str:
    value = str(headers.get("authorization") or "")
    if value.lower().startswith("bearer "):
        return value[7:].strip()
    return str(query.get("token") or "") if query is not None else ""


def create_app(core: MockCore) -> FastAPI:
    app = FastAPI()

    @app.exception_handler(MockError)
    async def _mock_error(_request: Request, exc: MockError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message}}
        )

    def require(request: Request, scope: str | None = None) -> dict[str, Any]:
        info = core.auth(_bearer(request.headers))
        if info is None:
            raise MockError(401, "invalid_token", "Token ausente o inválido")
        if scope and scope not in info["scopes"]:
            raise MockError(403, "missing_scope", f"El token no tiene el permiso '{scope}'.")
        return info

    async def json_body(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except ValueError:
            raise MockError(400, "bad_request", "El cuerpo debe ser JSON.") from None
        if not isinstance(body, dict):
            raise MockError(400, "bad_request", "El cuerpo debe ser un objeto JSON.")
        return body

    @app.get("/api/v1/health")
    async def health() -> dict[str, Any]:
        return {
            "ok": True,
            "protocol": 1,
            "version": VERSION,
            "mode": core.mode,
            "name": AGENT_NAME,
            "requires_auth": True,
        }

    @app.get("/api/v1/me")
    async def me(request: Request) -> dict[str, Any]:
        info = require(request)
        return {**info, "agent": {"name": AGENT_NAME, "language": "es", "voice": "es-PE-CamilaNeural"}}

    @app.get("/api/v1/agent/state")
    async def agent_state(request: Request) -> dict[str, Any]:
        require(request, "chat")
        return {
            "status": "idle",
            "emotion": "neutral",
            "busy": core.busy,
            "approval_pending": core.approval_pending,
        }

    @app.post("/api/v1/approvals")
    async def approvals(request: Request) -> dict[str, Any]:
        require(request, "chat")
        body = await json_body(request)
        if not core.approval_pending:
            raise MockError(404, "not_found", "No hay acciones esperando aprobación.")
        core.approval_pending = False
        core.approvals.append(bool(body.get("approve")))
        return {"ok": True, "status": "approved" if body.get("approve") else "rejected"}

    # Emparejamiento y dispositivos

    @app.post("/api/v1/pairing", status_code=201)
    async def pairing(request: Request) -> dict[str, Any]:
        require(request, "admin")
        body = await json_body(request)
        scopes = (
            body.get("scopes") if isinstance(body.get("scopes"), list) else ["chat", "voice", "tasks", "node"]
        )
        code = core.pairing_code(scopes)
        host = request.url.hostname or "127.0.0.1"
        port = request.url.port or 8765
        expires = iso(datetime.now(timezone.utc) + timedelta(minutes=5))
        return {
            "code": code,
            "expires_at": expires,
            "qr_payload": f"gmini://pair?host={host}&port={port}&code={code}",
        }

    @app.post("/api/v1/pairing/claim")
    async def claim(request: Request) -> dict[str, Any]:
        body = await json_body(request)
        entry = core.codes.pop(str(body.get("code") or ""), None)
        if entry is None or entry["expires_at"] < time.time():
            raise MockError(401, "invalid_code", "Código inválido o vencido.")
        token, device = core.issue(
            str(body.get("device_name") or "Dispositivo"),
            scopes=entry["scopes"],
            device_type=str(body.get("device_type") or "custom"),
            platform=str(body.get("platform") or ""),
        )
        return {
            "token": token,
            "device_id": device.id,
            "server_name": SERVER_NAME,
            "agent_name": AGENT_NAME,
            "scopes": device.scopes,
        }

    @app.get("/api/v1/devices")
    async def devices(request: Request) -> dict[str, Any]:
        require(request, "admin")
        return {"items": [d.public() for d in core.devices.values()]}

    @app.delete("/api/v1/devices/{device_id}")
    async def revoke(device_id: str, request: Request) -> dict[str, Any]:
        require(request, "admin")
        if core.devices.pop(device_id, None) is None:
            raise MockError(404, "not_found", "Dispositivo no encontrado.")
        return {"ok": True}

    # Chat y sesiones

    async def run_script(events: list[dict[str, Any]]):
        for event in events:
            if event["type"] == "wait":
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline and not core.cancelled.is_set():
                    await asyncio.sleep(0.05)
                if core.cancelled.is_set():
                    yield {"type": "done", "session_id": SESSION_ID, "reply": "(cancelado)", "actions": []}
                    return
                continue
            yield event
            await asyncio.sleep(0)

    @app.post("/api/v1/chat")
    async def chat(request: Request):
        require(request, "chat")
        body = await json_body(request)
        core.chat_bodies.append(body)
        message = str(body.get("message") or "").strip()
        if not message:
            raise MockError(422, "validation_error", "Falta 'message'.")
        if core.busy or "ocupado" in message.lower():
            raise MockError(409, "busy", "El agente está ocupado con otra conversación.")
        events, result = core.script(message)
        wants_stream = bool(body.get("stream")) or "text/event-stream" in request.headers.get("accept", "")
        if not wants_stream:
            return result

        newline = core.sse_newline

        async def stream():
            finished = False
            async for item in run_script(events):
                payload = json.dumps({k: v for k, v in item.items() if k != "type"}, ensure_ascii=False)
                yield f"event: {item['type']}{newline}data: {payload}{newline}{newline}"
                finished = finished or item["type"] in ("done", "error")
            if not finished:
                yield f"event: done{newline}data: {json.dumps(result, ensure_ascii=False)}{newline}{newline}"

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/v1/sessions")
    async def sessions(request: Request, limit: int = 20) -> dict[str, Any]:
        require(request, "chat")
        items = [
            {
                "id": SESSION_ID,
                "title": "Pruebas de la CLI",
                "updated_at": "2026-10-07T05:05:01Z",
                "message_count": 2,
            }
        ]
        return {"items": items[:limit]}

    @app.get("/api/v1/sessions/{session_id}/messages")
    async def messages(session_id: str, request: Request, limit: int = 100) -> dict[str, Any]:
        require(request, "chat")
        if session_id != SESSION_ID:
            return {"items": []}
        items = [
            {"role": "user", "content": "hola", "created_at": "2026-10-07T05:05:01Z"},
            {
                "role": "assistant",
                "content": "Hola, soy G-Mini. ¿En qué te ayudo?",
                "created_at": "2026-10-07T05:05:02Z",
            },
        ]
        return {"items": items[-limit:]}

    # Voz

    @app.post("/api/v1/voice/tts")
    async def tts(request: Request) -> Response:
        require(request, "voice")
        body = await json_body(request)
        if not str(body.get("text") or "").strip():
            raise MockError(422, "validation_error", "Falta 'text'.")
        return Response(content=tiny_wav(), media_type="audio/wav")

    @app.post("/api/v1/voice/stt")
    async def stt(request: Request) -> dict[str, Any]:
        require(request, "voice")
        audio = await request.body()
        if not audio:
            raise MockError(422, "validation_error", "El cuerpo debe traer el audio (WAV).")
        core.stt_bodies.append(audio)
        if core.stt_silent:
            return {"text": ""}
        return {"text": f"transcripción de prueba ({len(audio)} bytes)"}

    @app.post("/api/v1/voice/wake")
    async def wake(request: Request) -> dict[str, Any]:
        require(request, "voice")
        audio = await request.body()
        if not audio:
            raise MockError(422, "validation_error", "El cuerpo debe traer el audio (WAV).")
        if core.stt_silent:
            return {"wake": False, "phrase": "", "command": "", "transcript": "buenos días"}
        return {
            "wake": True,
            "phrase": "oye g-mini",
            "command": "qué hora es",
            "transcript": "Oye G-Mini, qué hora es",
        }

    # Tareas

    @app.post("/api/v1/tasks", status_code=202)
    async def create_task(request: Request) -> dict[str, Any]:
        require(request, "tasks")
        body = await json_body(request)
        prompt = str(body.get("prompt") or "").strip()
        if not prompt:
            raise MockError(422, "validation_error", "Falta 'prompt'.")
        schedule = body.get("schedule")
        status, next_run = "queued", None
        if schedule is not None:
            if not isinstance(schedule, dict):
                raise MockError(422, "validation_error", "schedule debe ser un objeto.")
            if "at" in schedule:
                raise MockError(422, "validation_error", "schedule.at todavía no está disponible.")
            if schedule.get("cron"):
                if not schedule.get("timezone"):
                    raise MockError(422, "validation_error", "Falta schedule.timezone.")
                status, next_run = "scheduled", iso(datetime.now(timezone.utc) + timedelta(hours=8))
            elif "interval_seconds" in schedule:
                if int(schedule.get("interval_seconds") or 0) < 60:
                    raise MockError(422, "validation_error", "interval_seconds debe ser >= 60.")
                status = "scheduled"
                next_run = iso(
                    datetime.now(timezone.utc) + timedelta(seconds=int(schedule["interval_seconds"]))
                )
            else:
                raise MockError(422, "validation_error", "schedule no reconocido.")
        task_id = f"tsk_{len(core.tasks) + 1:04d}"
        now = iso(datetime.now(timezone.utc))
        core.tasks[task_id] = {
            "task_id": task_id,
            "title": str(body.get("title") or prompt[:60]),
            "prompt": prompt,
            "status": "done" if status == "queued" else status,
            "result": f"Hecho: {prompt}" if status == "queued" else None,
            "error": None,
            "runs": 1 if status == "queued" else 0,
            "created_at": now,
            "started_at": now if status == "queued" else None,
            "finished_at": now if status == "queued" else None,
            "next_run_at": next_run,
            "notify": body.get("notify") or [],
        }
        return {"task_id": task_id, "status": status}

    @app.get("/api/v1/tasks")
    async def list_tasks(request: Request, status: str | None = None) -> dict[str, Any]:
        require(request, "tasks")
        items = [t for t in core.tasks.values() if status is None or t["status"] == status]
        return {"items": items}

    @app.get("/api/v1/tasks/{task_id}")
    async def get_task(task_id: str, request: Request) -> dict[str, Any]:
        require(request, "tasks")
        task = core.tasks.get(task_id)
        if task is None:
            raise MockError(404, "not_found", "Tarea no encontrada.")
        return task

    @app.delete("/api/v1/tasks/{task_id}")
    async def cancel_task(task_id: str, request: Request) -> dict[str, Any]:
        require(request, "tasks")
        task = core.tasks.get(task_id)
        if task is None:
            raise MockError(404, "not_found", "Tarea no encontrada.")
        task["status"] = "cancelled"
        task["next_run_at"] = None
        return {"ok": True, "status": "cancelled"}

    # WebSocket

    @app.websocket("/api/v1/ws")
    async def websocket(ws: WebSocket) -> None:
        info = core.auth(_bearer(ws.headers, ws.query_params))
        if info is None:
            await ws.close(code=4401)
            return
        await ws.accept()
        outbox: asyncio.Queue = asyncio.Queue()

        async def writer() -> None:
            while True:
                await ws.send_text(json.dumps(await outbox.get(), ensure_ascii=False))

        async def answer(frame: dict[str, Any]) -> None:
            request_id = frame.get("id")
            events, result = core.script(str(frame.get("text") or ""))
            async for item in run_script(events):
                if item["type"] == "start":
                    continue
                await outbox.put({**item, "id": request_id})
                if item["type"] in ("done", "error"):
                    return
            await outbox.put({"type": "done", "id": request_id, **result})

        writer_task = asyncio.create_task(writer())
        chat_task: asyncio.Task | None = None
        try:
            while True:
                frame = json.loads(await ws.receive_text())
                core.ws_frames.append(frame)
                kind = frame.get("type")
                if kind == "ping":
                    await outbox.put({"type": "pong"})
                elif kind == "hello":
                    await outbox.put(
                        {"type": "ready", "agent_name": AGENT_NAME, "protocol": 1, "session_id": SESSION_ID}
                    )
                elif kind == "chat":
                    if chat_task is not None and not chat_task.done():
                        await outbox.put(
                            {"type": "error", "code": "busy", "message": "Ya hay una respuesta en curso."}
                        )
                        continue
                    core.cancelled.clear()
                    chat_task = asyncio.create_task(answer(frame))
                elif kind == "cancel":
                    core.cancelled.set()
                else:
                    await outbox.put(
                        {"type": "error", "code": "bad_request", "message": f"Tipo desconocido: {kind}"}
                    )
        except WebSocketDisconnect:
            pass
        finally:
            writer_task.cancel()
            if chat_task is not None:
                chat_task.cancel()

    return app


class ServerThread:
    """Servidor uvicorn en un hilo, en un puerto libre de 127.0.0.1."""

    def __init__(self, core: MockCore, port: int = 0) -> None:
        self.core = core
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", port))
        self.port = self.sock.getsockname()[1]
        config = uvicorn.Config(create_app(core), log_level="warning", lifespan="off", ws="auto")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, kwargs={"sockets": [self.sock]}, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> ServerThread:
        self.thread.start()
        deadline = time.monotonic() + 15
        while not self.server.started:
            if time.monotonic() > deadline or not self.thread.is_alive():
                raise RuntimeError("el servidor de pruebas no arrancó")
            time.sleep(0.02)
        return self

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)
        self.sock.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Servidor falso de la API remota v1 de G-Mini")
    parser.add_argument("--port", type=int, default=8799)
    parser.add_argument("--home", help="escribe el token de sesión en <home>/data/runtime/session_token")
    args = parser.parse_args()
    core = MockCore()
    if args.home:
        token_file = Path(args.home) / "data" / "runtime" / "session_token"
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(core.session_token, encoding="utf-8")
        print(f"Token de sesión en {token_file}", flush=True)
    else:
        print(f"Token de sesión: {core.session_token}", flush=True)
    server = ServerThread(core, port=args.port).start()
    print(f"Servidor falso escuchando en {server.url} (Ctrl+C para salir)", flush=True)
    try:
        while server.thread.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    main()
