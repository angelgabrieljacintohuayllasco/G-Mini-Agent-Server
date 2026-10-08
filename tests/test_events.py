from __future__ import annotations

from gmini_cli.events import ChatAccumulator, ChatEvent, action_name, events_from_reply


def feed_all(events: list[ChatEvent]) -> ChatAccumulator:
    accumulator = ChatAccumulator()
    for event in events:
        accumulator.feed(event)
    return accumulator


def test_accumulator_follows_the_contract_events() -> None:
    accumulator = feed_all(
        [
            ChatEvent("start", {"session_id": "ses_1"}),
            ChatEvent("state", {"status": "thinking", "emotion": "neutral"}),
            ChatEvent("chunk", {"text": "Voy a crear la tarea. "}),
            ChatEvent(
                "action", {"action": "schedule_create_job", "params": {"cron": "0 18 * * *"}, "id": "a1"}
            ),
            ChatEvent(
                "action", {"action": "schedule_create_job", "params": {"cron": "0 9 * * *"}, "id": "a2"}
            ),
            ChatEvent(
                "action_result",
                {"action": "schedule_create_job", "success": False, "message": "x", "id": "a2"},
            ),
            ChatEvent(
                "action_result",
                {"action": "schedule_create_job", "success": True, "message": "ok", "id": "a1"},
            ),
            ChatEvent("notice", {"kind": "system", "text": "Acciones ejecutadas"}),
            ChatEvent("chunk", {"text": "Listo."}),
        ]
    )
    result = accumulator.result
    assert result.session_id == "ses_1"
    assert accumulator.partial_reply == "Voy a crear la tarea. Listo."
    assert result.actions[0]["success"] is True
    assert result.actions[1]["success"] is False
    assert result.notices == ["Acciones ejecutadas"]
    assert not result.finished


def test_done_reply_is_the_conclusion() -> None:
    accumulator = feed_all(
        [
            ChatEvent("chunk", {"text": "Voy a revisar. "}),
            ChatEvent("chunk", {"text": "Listo."}),
            ChatEvent(
                "done",
                {
                    "session_id": "ses_2",
                    "reply": "Listo.",
                    "actions": [{"action": "x"}],
                    "approval_pending": True,
                },
            ),
        ]
    )
    result = accumulator.result
    assert result.finished
    assert result.reply == "Listo."
    assert result.actions == [{"action": "x"}]
    assert result.approval_pending
    assert result.to_json()["session_id"] == "ses_2"


def test_approval_and_error_events() -> None:
    accumulator = feed_all(
        [
            ChatEvent("approval", {"pending": True, "summary": "Borrar archivos", "kind": "approval"}),
            ChatEvent("chunk", {"text": "parcial"}),
            ChatEvent("error", {"code": "provider_unavailable", "message": "sin key"}),
        ]
    )
    result = accumulator.result
    assert result.approval_pending
    assert result.approval_summary == "Borrar archivos"
    assert result.error == {"code": "provider_unavailable", "message": "sin key"}
    assert result.reply == "parcial"
    assert result.finished


def test_action_name_accepts_draft_key() -> None:
    assert action_name({"action": "web_search"}) == "web_search"
    assert action_name({"type": "web_search"}) == "web_search"
    assert action_name({}) == "?"


def test_events_from_reply_rebuilds_a_stream() -> None:
    payload = {
        "session_id": "ses_3",
        "reply": "Listo.",
        "actions": [{"action": "schedule_create_job", "success": True, "message": "Tarea creada"}],
        "notices": ["Acciones ejecutadas"],
        "approval_pending": True,
    }
    events = events_from_reply(payload)
    assert [e.type for e in events] == ["start", "action_result", "notice", "approval", "chunk", "done"]
    result = feed_all(events).result
    assert result.reply == "Listo."
    assert result.approval_pending


def test_events_from_reply_with_error_only() -> None:
    events = events_from_reply(
        {"session_id": "s", "reply": "", "error": {"code": "agent_error", "message": "x"}}
    )
    assert events[-1].type == "error"
    assert events[-1].data["code"] == "agent_error"
