"""``gmini task add|list|show|cancel``: el trabajador 24/7 del servidor."""

from __future__ import annotations

import argparse
from typing import Any

from ..console import STYLE_LABEL
from ..context import AppContext
from ..errors import EXIT_OK, UsageError
from ..schedule import build_schedule, describe_seconds, format_moment

STATUS_LABELS = {
    "queued": "en cola",
    "scheduled": "programada",
    "running": "en curso",
    "done": "terminada",
    "failed": "falló",
    "cancelled": "cancelada",
}
STATUSES = tuple(STATUS_LABELS)


def status_label(value: Any) -> str:
    return STATUS_LABELS.get(str(value), str(value or "-"))


def _schedule_label(task: dict[str, Any]) -> str:
    schedule = task.get("schedule")
    if not isinstance(schedule, dict):
        return "-"
    if schedule.get("cron"):
        return f"cron {schedule['cron']} ({schedule.get('timezone') or '-'})"
    if schedule.get("interval_seconds"):
        try:
            return f"cada {describe_seconds(int(schedule['interval_seconds']))}"
        except (TypeError, ValueError):
            return str(schedule["interval_seconds"])
    if schedule.get("at"):
        return f"el {format_moment(schedule['at'])}"
    return "-"


def _notify_targets(values: list[str] | None) -> list[str]:
    targets = [item.strip() for value in values or [] for item in value.split(",") if item.strip()]
    for target in targets:
        if ":" not in target:
            raise UsageError(
                f"Destino de aviso no válido: {target!r}",
                hint="Usa canal:destino, por ejemplo telegram:123456789.",
            )
    return targets


def cmd_task_add(ctx: AppContext, args: argparse.Namespace) -> int:
    out = ctx.out
    prompt = " ".join(args.prompt).strip()
    if not prompt:
        raise UsageError("Falta el pedido de la tarea.")
    schedule, description = build_schedule(cron=args.cron, tz=args.tz, every=args.every, at=args.at)
    payload: dict[str, Any] = {"prompt": prompt}
    if args.title:
        payload["title"] = args.title
    if schedule:
        payload["schedule"] = schedule
    notify = _notify_targets(args.notify)
    if notify:
        payload["notify"] = notify

    with ctx.client() as client:
        created = client.create_task(payload)
    task_id = created.get("task_id") or "-"
    if out.json_mode:
        out.json(created)
        return EXIT_OK
    out.success(f"Tarea creada: {task_id} ({status_label(created.get('status'))}).")
    out.fields([("Cuándo", description), ("Avisos", ", ".join(notify) or "-")])
    out.note(f"Sigue su estado con: gmini task show {task_id}")
    return EXIT_OK


def cmd_task_list(ctx: AppContext, args: argparse.Namespace) -> int:
    out = ctx.out
    with ctx.client() as client:
        tasks = client.list_tasks(status=args.status)
    if out.json_mode:
        out.json({"items": tasks})
        return EXIT_OK
    if not tasks:
        out.line("No hay tareas" + (f" con estado {status_label(args.status)}." if args.status else "."))
        return EXIT_OK
    rows = [
        [
            task.get("task_id", "-"),
            (task.get("title") or task.get("prompt") or "-")[:48],
            status_label(task.get("status")),
            task.get("runs", "-"),
            format_moment(task.get("next_run_at")),
            format_moment(task.get("created_at")),
        ]
        for task in tasks
    ]
    out.table(["ID", "Título", "Estado", "Corridas", "Próxima", "Creada"], rows)
    return EXIT_OK


def cmd_task_show(ctx: AppContext, args: argparse.Namespace) -> int:
    out = ctx.out
    with ctx.client() as client:
        task = client.get_task(args.task_id)
    if out.json_mode:
        out.json(task)
        return EXIT_OK
    out.fields(
        [
            ("ID", task.get("task_id")),
            ("Título", task.get("title")),
            ("Estado", status_label(task.get("status"))),
            ("Pedido", task.get("prompt")),
            ("Programación", _schedule_label(task)),
            ("Corridas", task.get("runs")),
            ("Creada", format_moment(task.get("created_at"))),
            ("Inicio", format_moment(task.get("started_at"))),
            ("Fin", format_moment(task.get("finished_at"))),
            ("Próxima", format_moment(task.get("next_run_at"))),
            ("Error", task.get("error")),
        ]
    )
    result = task.get("result")
    if result:
        out.line("")
        out.line("Resultado", STYLE_LABEL)
        out.line(str(result).strip())
    return EXIT_OK


def cmd_task_cancel(ctx: AppContext, args: argparse.Namespace) -> int:
    out = ctx.out
    with ctx.client() as client:
        response = client.cancel_task(args.task_id)
    if out.json_mode:
        out.json({"task_id": args.task_id, **response})
    else:
        out.success(f"Tarea {args.task_id} cancelada.")
    return EXIT_OK
