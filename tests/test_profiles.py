from __future__ import annotations

import json
from pathlib import Path

import pytest

from gmini_cli.errors import CliError, UsageError
from gmini_cli.profiles import (
    AUTH_SESSION,
    AUTH_STORED,
    LOCAL_PROFILE,
    Profile,
    ProfileStore,
    default_profile_name,
    is_loopback_url,
    normalize_url,
    parse_pairing_link,
    sanitize_profile_name,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("100.71.131.70", "http://100.71.131.70:8765"),
        ("tv-server", "http://tv-server:8765"),
        ("tv-server:9000", "http://tv-server:9000"),
        ("http://gmini.example.com", "http://gmini.example.com"),
        ("https://gmini.example.com/", "https://gmini.example.com"),
        ("https://gmini.example.com:443", "https://gmini.example.com"),
        ("https://gmini.example.com:8443/api/v1", "https://gmini.example.com:8443"),
        ("http://proxy.local/gmini/api", "http://proxy.local/gmini"),
        ("localhost", "http://127.0.0.1:8765"),
        ("http://localhost:8765", "http://127.0.0.1:8765"),
        ("[::1]:8765", "http://127.0.0.1:8765"),
        ("http://0.0.0.0:8765", "http://127.0.0.1:8765"),
        ("  100.71.131.70:8765  ", "http://100.71.131.70:8765"),
        ("[fd7a:115c:a1e0::1]", "http://[fd7a:115c:a1e0::1]:8765"),
    ],
)
def test_normalize_url(raw: str, expected: str) -> None:
    assert normalize_url(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "ftp://servidor", "http://host:99999", "http://"])
def test_normalize_url_rejects_invalid(raw: str) -> None:
    with pytest.raises(UsageError):
        normalize_url(raw)


def test_loopback_detection() -> None:
    assert is_loopback_url("http://127.0.0.1:8765")
    assert is_loopback_url("http://localhost:8765")
    assert not is_loopback_url("http://100.71.131.70:8765")


@pytest.mark.parametrize(
    ("url", "server_name", "agent_name", "expected"),
    [
        ("http://127.0.0.1:8765", None, "G-Mini", LOCAL_PROFILE),
        ("http://127.0.0.1:8765", "tv-server", "G-Mini", "tv-server"),
        ("http://100.71.131.70:8765", "tv-server", "G-Mini", "tv-server"),
        ("http://100.71.131.70:8765", "G-Mini", "G-Mini", "100.71.131.70"),
        ("http://100.71.131.70:8765", None, None, "100.71.131.70"),
        ("https://gmini.example.com", "Servidor de casa", "Lia", "Servidor-de-casa"),
    ],
)
def test_default_profile_name(
    url: str, server_name: str | None, agent_name: str | None, expected: str
) -> None:
    assert default_profile_name(url, server_name, agent_name) == expected


def test_sanitize_profile_name() -> None:
    assert sanitize_profile_name("  Mi servidor/VPS  ") == "Mi-servidor-VPS"
    assert sanitize_profile_name("...") == ""


def test_parse_pairing_link() -> None:
    link = parse_pairing_link("gmini://pair?host=100.71.131.70&port=8765&code=482913")
    assert link.url == "http://100.71.131.70:8765"
    assert link.code == "482913"

    without_port = parse_pairing_link("gmini://pair?host=tv-server&code=1")
    assert without_port.url == "http://tv-server:8765"

    tls = parse_pairing_link("gmini://pair?host=gmini.example.com&port=443&tls=1")
    assert tls.url == "https://gmini.example.com"
    assert tls.code is None


@pytest.mark.parametrize("link", ["http://pair?host=x", "gmini://pair?code=123456"])
def test_parse_pairing_link_rejects_invalid(link: str) -> None:
    with pytest.raises(UsageError):
        parse_pairing_link(link)


def test_store_roundtrip_and_current(tmp_path: Path) -> None:
    path = tmp_path / "cfg" / "profiles.json"
    store = ProfileStore(path)
    assert store.current_name is None
    store.put(Profile(name="tv-server", url="http://100.71.131.70:8765", auth=AUTH_STORED, scopes=["chat"]))
    store.put(Profile(name="vps", url="https://gmini.example.com", auth=AUTH_STORED), make_current=False)

    reloaded = ProfileStore(path)
    assert reloaded.names() == ["tv-server", "vps"]
    assert reloaded.current_name == "tv-server"
    profile = reloaded.get("tv-server")
    assert profile is not None
    assert profile.scopes == ["chat"]
    assert profile.auth == AUTH_STORED

    reloaded.use("vps")
    assert ProfileStore(path).current_name == "vps"
    assert reloaded.remove("vps")
    assert ProfileStore(path).current_name == "tv-server"
    assert not reloaded.remove("no-existe")
    assert [p.name for p in path.parent.iterdir()] == ["profiles.json"]


def test_store_resolves_implicit_local(tmp_path: Path) -> None:
    store = ProfileStore(tmp_path / "profiles.json")
    local = store.resolve(LOCAL_PROFILE)
    assert local.implicit
    assert local.auth == AUTH_SESSION
    assert local.url == "http://127.0.0.1:8765"
    with pytest.raises(UsageError):
        store.resolve("desconocido")


def test_store_rejects_invalid_name(tmp_path: Path) -> None:
    store = ProfileStore(tmp_path / "profiles.json")
    with pytest.raises(UsageError):
        store.put(Profile(name="con espacio", url="http://x:8765"))


def test_store_reports_corrupted_file(tmp_path: Path) -> None:
    path = tmp_path / "profiles.json"
    path.write_text("{no es json", encoding="utf-8")
    with pytest.raises(CliError, match="dañado"):
        ProfileStore(path)


def test_profile_file_never_contains_tokens(tmp_path: Path) -> None:
    path = tmp_path / "profiles.json"
    ProfileStore(path).put(Profile(name="vps", url="https://gmini.example.com", auth=AUTH_STORED))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "token" not in json.dumps(data["profiles"]["vps"]).replace("token_file", "")
