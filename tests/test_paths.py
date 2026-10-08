from __future__ import annotations

from pathlib import Path

import pytest

from gmini_cli import paths


def test_config_and_data_dir_overrides() -> None:
    env = {"GMINI_CONFIG_DIR": "/srv/gmini/cfg", "GMINI_DATA_DIR": "/srv/gmini/data"}
    assert paths.config_dir(env) == Path("/srv/gmini/cfg")
    assert paths.data_dir(env) == Path("/srv/gmini/data")
    assert paths.history_file(env) == Path("/srv/gmini/data") / "chat_history"


def test_session_token_file_layout() -> None:
    assert paths.session_token_file("/data") == Path("/data") / "data" / "runtime" / "session_token"


def test_known_homes_linux_respects_xdg() -> None:
    env = {"HOME": "/home/gabriel", "XDG_DATA_HOME": "/home/gabriel/.datos"}
    homes = paths.known_home_candidates(env, platform="linux")
    assert homes[0] == Path("/home/gabriel/.datos") / "g-mini"
    assert Path("/home/gabriel") / ".config" / "G-Mini Agent" in homes
    assert homes[-1] == Path("/home/gabriel") / ".gmini"


def test_known_homes_linux_default_matches_official_installer() -> None:
    homes = paths.known_home_candidates({"HOME": "/home/gabriel"}, platform="linux")
    assert homes[0] == Path("/home/gabriel") / ".local" / "share" / "g-mini"


def test_known_homes_windows_and_macos() -> None:
    windows = paths.known_home_candidates(
        {"USERPROFILE": r"C:\Users\Gabriel", "APPDATA": r"C:\Users\Gabriel\AppData\Roaming"}, platform="win32"
    )
    assert windows[0] == Path(r"C:\Users\Gabriel\AppData\Roaming") / "G-Mini Agent"
    mac = paths.known_home_candidates({"HOME": "/Users/gabriel"}, platform="darwin")
    assert mac[0] == Path("/Users/gabriel") / "Library" / "Application Support" / "G-Mini Agent"


def test_session_token_candidates_order(tmp_path: Path) -> None:
    checkout = tmp_path / "G-Mini-Agent"
    (checkout / "backend").mkdir(parents=True)
    (checkout / "backend" / "main.py").write_text("", encoding="utf-8")
    env = {
        "HOME": str(tmp_path / "home"),
        "GMINI_TOKEN_FILE": str(tmp_path / "token.txt"),
        "GMINI_HOME": str(tmp_path / "gmini-home"),
    }
    explicit = tmp_path / "explicit"
    candidates = paths.session_token_candidates(
        env, explicit=[explicit], extra_homes=[tmp_path / "perfil"], cwd=checkout, platform="linux"
    )
    assert candidates[:5] == [
        explicit,
        tmp_path / "token.txt",
        paths.session_token_file(tmp_path / "gmini-home"),
        paths.session_token_file(tmp_path / "perfil"),
        paths.session_token_file(checkout),
    ]
    assert paths.session_token_file(tmp_path / "home" / ".local" / "share" / "g-mini") in candidates


def test_session_token_candidates_are_unique(tmp_path: Path) -> None:
    home = tmp_path / "home"
    env = {"HOME": str(home), "GMINI_HOME": str(home / ".gmini")}
    candidates = paths.session_token_candidates(env, platform="linux")
    assert len(candidates) == len(set(candidates))


def test_read_token_file(tmp_path: Path) -> None:
    token = tmp_path / "session_token"
    assert paths.read_token_file(token) is None
    token.write_text("  abc123\n", encoding="utf-8")
    assert paths.read_token_file(token) == "abc123"
    token.write_text("\n", encoding="utf-8")
    assert paths.read_token_file(token) is None


@pytest.mark.parametrize(
    ("platform", "machine", "expected"),
    [
        ("win32", "AMD64", "windows-x64"),
        ("linux", "x86_64", "linux-x64"),
        ("linux", "aarch64", "linux-arm64"),
        ("darwin", "arm64", "macos-arm64"),
        ("freebsd14", "riscv64", "freebsd-riscv64"),
    ],
)
def test_platform_tag(platform: str, machine: str, expected: str) -> None:
    assert paths.platform_tag(platform, machine) == expected
