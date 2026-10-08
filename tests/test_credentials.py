from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

from gmini_cli import credentials
from gmini_cli.credentials import SERVICE_NAME, CredentialStore, FileCredentials
from gmini_cli.errors import CliError


class MemoryKeyring:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, name: str) -> str | None:
        return self.items.get((service, name))

    def set_password(self, service: str, name: str, token: str) -> None:
        self.items[(service, name)] = token

    def delete_password(self, service: str, name: str) -> None:
        del self.items[(service, name)]


class BrokenKeyring:
    def get_password(self, service: str, name: str) -> str | None:
        raise RuntimeError("sin D-Bus")

    def set_password(self, service: str, name: str, token: str) -> None:
        raise RuntimeError("sin D-Bus")

    def delete_password(self, service: str, name: str) -> None:
        raise RuntimeError("sin D-Bus")


def test_file_credentials_roundtrip(tmp_path: Path) -> None:
    store = FileCredentials(tmp_path / "cfg" / "credentials.json")
    assert store.get("vps") is None
    store.set("vps", "gm_dev_1")
    store.set("tv", "gm_dev_2")
    assert store.get("vps") == "gm_dev_1"
    assert store.delete("vps")
    assert not store.delete("vps")
    data = json.loads(store.path.read_text(encoding="utf-8"))
    assert data == {"version": 1, "tokens": {"tv": "gm_dev_2"}}


@pytest.mark.skipif(sys.platform == "win32", reason="permisos POSIX")
def test_config_dir_created_by_profiles_is_private(tmp_path: Path) -> None:
    from gmini_cli.profiles import Profile, ProfileStore

    store = ProfileStore(tmp_path / "cfg" / "profiles.json")
    store.put(Profile(name="vps", url="https://gmini.example.com"))
    assert stat.S_IMODE(os.stat(tmp_path / "cfg").st_mode) == 0o700


@pytest.mark.skipif(sys.platform == "win32", reason="permisos POSIX")
def test_file_credentials_are_private_on_posix(tmp_path: Path) -> None:
    store = FileCredentials(tmp_path / "cfg" / "credentials.json")
    store.set("vps", "gm_dev_1")
    assert stat.S_IMODE(os.stat(store.path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(store.path.parent).st_mode) == 0o700


def test_acl_is_applied_on_every_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Path] = []
    monkeypatch.setattr(credentials, "restrict_to_user", lambda path, is_dir=False: calls.append(path))
    store = FileCredentials(tmp_path / "credentials.json")
    store.set("a", "1")
    store.set("b", "2")
    assert calls.count(store.path) == 2


def test_windows_acl_uses_icacls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: Any) -> None:
        recorded.append(command)

    monkeypatch.setattr(credentials.os, "name", "nt")
    monkeypatch.setattr(credentials.subprocess, "run", fake_run)
    monkeypatch.setenv("USERNAME", "gabriel")
    monkeypatch.setenv("USERDOMAIN", "PC")
    target = tmp_path / "credentials.json"
    credentials.restrict_to_user(target)
    assert recorded == [["icacls", str(target), "/inheritance:r", "/grant:r", "PC\\gabriel:(F)"]]


def test_store_prefers_keyring_and_cleans_file(tmp_path: Path) -> None:
    keyring = MemoryKeyring()
    store = CredentialStore(tmp_path / "credentials.json", keyring_backend=keyring)
    store.file.set("vps", "viejo")
    assert store.set("vps", "gm_dev_nuevo") == "keyring"
    assert keyring.items[(SERVICE_NAME, "vps")] == "gm_dev_nuevo"
    assert store.file.get("vps") is None
    assert store.get("vps") == "gm_dev_nuevo"
    assert "llavero" in store.describe()
    store.delete("vps")
    assert store.get("vps") is None


def test_store_falls_back_to_file_when_keyring_breaks(tmp_path: Path) -> None:
    warnings: list[str] = []
    store = CredentialStore(
        tmp_path / "credentials.json", keyring_backend=BrokenKeyring(), warn=warnings.append
    )
    assert store.set("vps", "gm_dev_1") == "file"
    assert store.get("vps") == "gm_dev_1"
    assert len(warnings) == 1
    assert not store.uses_keyring


def test_store_file_mode_ignores_keyring(tmp_path: Path) -> None:
    keyring = MemoryKeyring()
    store = CredentialStore(tmp_path / "credentials.json", mode="file", keyring_backend=keyring)
    assert store.set("vps", "gm_dev_1") == "file"
    assert keyring.items == {}


def test_store_mode_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(CliError):
        CredentialStore(tmp_path / "c.json", mode="vault")
    monkeypatch.setattr(credentials, "detect_keyring", lambda: None)
    with pytest.raises(CliError, match="llavero"):
        CredentialStore(tmp_path / "c.json", mode="keyring")


def test_detect_keyring_rejects_fail_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    import keyring
    from keyring.backends import fail

    monkeypatch.setattr(keyring, "get_keyring", lambda: fail.Keyring())
    assert credentials.detect_keyring() is None
