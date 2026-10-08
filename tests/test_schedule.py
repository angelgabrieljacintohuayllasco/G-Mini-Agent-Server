from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from gmini_cli import schedule
from gmini_cli.errors import UsageError


@pytest.mark.parametrize(
    ("raw", "seconds"),
    [("3600", 3600), ("90m", 5400), ("2h", 7200), ("1d", 86400), ("60s", 60), (" 15M ", 900)],
)
def test_parse_every(raw: str, seconds: int) -> None:
    assert schedule.parse_every(raw) == seconds


@pytest.mark.parametrize("raw", ["59", "30s", "abc", "", "1w", "-5m"])
def test_parse_every_rejects(raw: str) -> None:
    with pytest.raises(UsageError):
        schedule.parse_every(raw)


def test_parse_at_formats() -> None:
    now = datetime(2026, 10, 7, tzinfo=timezone.utc)
    assert schedule.parse_at("2026-10-08T08:00Z", now=now) == "2026-10-08T08:00:00+00:00"
    assert schedule.parse_at("2026-10-08 08:00-05:00", now=now) == "2026-10-08T08:00:00-05:00"
    naive = schedule.parse_at("2099-01-01T08:00")
    assert naive.startswith("2099-01-01T08:00:00")
    assert naive[-6] in "+-"


def test_parse_at_rejects_past_and_garbage() -> None:
    now = datetime(2026, 10, 7, tzinfo=timezone.utc)
    with pytest.raises(UsageError, match="ya pasó"):
        schedule.parse_at("2026-10-06T08:00Z", now=now)
    with pytest.raises(UsageError):
        schedule.parse_at("mañana a las 8", now=now)


def test_cron_and_timezone_validation() -> None:
    assert schedule.validate_cron("0  8 * *   *") == "0 8 * * *"
    with pytest.raises(UsageError):
        schedule.validate_cron("0 8 * *")
    assert schedule.validate_timezone("America/Lima") == "America/Lima"
    assert schedule.validate_timezone("America/Argentina/Buenos_Aires")
    assert schedule.validate_timezone("UTC") == "UTC"
    with pytest.raises(UsageError):
        schedule.validate_timezone("Lima")


def test_build_schedule_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    payload, text = schedule.build_schedule(cron="0 8 * * *", tz="America/Lima", every=None, at=None)
    assert payload == {"cron": "0 8 * * *", "timezone": "America/Lima"}
    assert "America/Lima" in text

    monkeypatch.setenv("TZ", "Europe/Madrid")
    payload, _ = schedule.build_schedule(cron="0 8 * * *", tz=None, every=None, at=None)
    assert payload == {"cron": "0 8 * * *", "timezone": "Europe/Madrid"}

    payload, text = schedule.build_schedule(cron=None, tz=None, every="2h", at=None)
    assert payload == {"interval_seconds": 7200}
    assert text == "cada 2 horas"

    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    payload, _ = schedule.build_schedule(cron=None, tz=None, every=None, at=future)
    assert payload is not None
    assert "at" in payload

    assert schedule.build_schedule(cron=None, tz=None, every=None, at=None) == (None, "una vez, ahora")
    with pytest.raises(UsageError):
        schedule.build_schedule(cron=None, tz="America/Lima", every=None, at=None)


def test_cron_without_detectable_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(schedule, "local_timezone", lambda env=None: None)
    with pytest.raises(UsageError, match="zona horaria"):
        schedule.build_schedule(cron="0 8 * * *", tz=None, every=None, at=None)


def test_windows_zone_mapping() -> None:
    assert schedule.windows_to_iana("SA Pacific Standard Time", "PE") == "America/Lima"
    assert schedule.windows_to_iana("SA Pacific Standard Time", "CO") == "America/Bogota"
    assert schedule.windows_to_iana("SA Pacific Standard Time") == "America/Bogota"
    assert schedule.windows_to_iana("Zona Inventada") is None


def test_local_timezone_from_env() -> None:
    assert schedule.local_timezone({"TZ": ":America/Lima"}, platform="linux") == "America/Lima"


def test_describe_seconds() -> None:
    assert schedule.describe_seconds(60) == "1 minuto"
    assert schedule.describe_seconds(5400) == "90 minutos"
    assert schedule.describe_seconds(86400) == "1 día"
    assert schedule.describe_seconds(61) == "61 segundos"


def test_format_moment_accepts_iso_and_epoch() -> None:
    assert schedule.format_moment(None) == "-"
    assert schedule.format_moment("no es fecha") == "no es fecha"
    iso = schedule.format_moment("2026-10-07T13:00:00Z")
    epoch = schedule.format_moment(datetime(2026, 10, 7, 13, tzinfo=timezone.utc).timestamp())
    assert iso == epoch
    assert len(iso) == len("2026-10-07 08:00")
    # Sin zona: el núcleo v0.2 manda la hora local del servidor y se muestra tal cual.
    assert schedule.format_moment("2026-10-07T19:44:54.363081") == "2026-10-07 19:44"


def test_relative_expiry() -> None:
    now = datetime(2026, 10, 7, 0, 0, tzinfo=timezone.utc)
    assert schedule.relative_expiry("2026-10-07T00:04:30Z", now=now) == "vence en 4 min 30 s"
    assert schedule.relative_expiry("2026-10-07T00:00:20Z", now=now) == "vence en 20 s"
    assert schedule.relative_expiry("2026-10-06T23:59:00Z", now=now) == "vencido"
    assert schedule.relative_expiry("raro", now=now) == ""
