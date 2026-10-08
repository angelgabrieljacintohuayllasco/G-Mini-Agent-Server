# G-Mini Agent Server

[![CI](https://github.com/angelgabrieljacintohuayllasco/G-Mini-Agent-Server/actions/workflows/ci.yml/badge.svg)](https://github.com/angelgabrieljacintohuayllasco/G-Mini-Agent-Server/actions/workflows/ci.yml)
[![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

The `gmini` command-line client and a Docker distribution of the
[G-Mini Agent](https://github.com/angelgabrieljacintohuayllasco/G-Mini-Agent) core, so the agent can
work 24/7 on a VPS, a Raspberry Pi or a home machine and be used from any terminal.

- **`gmini` CLI**: streaming chat with the agent, scheduled background tasks, text to speech and
  multiple servers through profiles. Works the same in cmd, PowerShell, Git Bash, Linux and macOS.
- **Server**: a Docker image of the core in headless mode, running as a non-root user with a data volume
  and a health check. On Linux the core's official installer (`systemctl --user`, no sudo) works too.

Everything talks to the core's
[remote API v1](https://github.com/angelgabrieljacintohuayllasco/G-Mini-Agent/blob/main/docs/protocol/remote-api-v1.md)
(REST, SSE and WebSocket) with per-device tokens. The CLI messages and the documentation are in Spanish;
this page is a summary in English.

![Architecture: clients, private network or TLS proxy, and the core running as a service](docs/assets/architecture.svg)

## Install the CLI

Requires Python 3.10 or newer.

```bash
pipx install "gmini-cli[repl] @ git+https://github.com/angelgabrieljacintohuayllasco/G-Mini-Agent-Server"
# or
uv tool install "gmini-cli[repl] @ git+https://github.com/angelgabrieljacintohuayllasco/G-Mini-Agent-Server"
```

Each [release](https://github.com/angelgabrieljacintohuayllasco/G-Mini-Agent-Server/releases) also ships
the wheel and the sdist.

## Quick start

1. **On the server**, create a pairing code (valid for 5 minutes, single use): `gmini pair-code`.
   If the core listens on its Tailscale IP, first create a profile that reads its session token:
   `gmini connect 100.71.131.70 --home ~/.local/share/g-mini --name servidor`. With Docker:
   `docker exec -it g-mini gmini pair-code --host <server-ip>`.
2. **On your machine**:

   ```bash
   gmini pair 100.71.131.70 482913
   gmini status
   gmini chat
   ```

3. From scripts:

   ```bash
   gmini ask "summarize in three lines" < report.txt
   gmini ask "what is on my list today?" --json
   gmini task add "Check my mail and summarize what is urgent" --cron "0 8 * * *" --tz America/Lima
   gmini say "The meeting starts in five minutes" --out alert.wav
   ```

## Running the server 24/7

Official installer (Linux, systemd user service, no sudo):

```bash
curl -fsSLO https://raw.githubusercontent.com/angelgabrieljacintohuayllasco/G-Mini-Agent/main/deploy/linux/install-server.sh
bash install-server.sh --tailscale
sudo loginctl enable-linger "$USER"
```

Docker:

```bash
cp docker/.env.example docker/.env        # set GMINI_BIND and your GMINI_KEY_* values
docker compose -f docker/compose.yaml up -d
docker compose -f docker/compose.yaml exec g-mini gmini status
```

The image pins the core to a release tag (`GMINI_CORE_REF`, default `v0.3.0`), installs only
`backend/requirements-server.txt`, runs as uid 10001, keeps data in the `/data` volume (`GMINI_HOME`) and
checks `/api/v1/health`. [Tailscale](https://tailscale.com) is the recommended way to reach the server
from other networks; for a public domain use a TLS reverse proxy (see [docs/servidor.md](docs/servidor.md)).

## Commands

| Command | Purpose |
|---|---|
| `gmini pair <host> <code>` | Pair this machine with a server (also accepts the `gmini://pair?...` QR link) |
| `gmini connect [url]` | Save a server as a profile; without a URL, the local core |
| `gmini profiles list \| use \| remove` | Manage profiles |
| `gmini status` | Health, mode, version, latency, token validity and agent state |
| `gmini chat` / `gmini chat "text"` / `gmini ask "text"` | Interactive chat or a single answer (`--json`, `--transport ws`) |
| `gmini approve` / `gmini reject` | Resolve actions waiting for approval |
| `gmini task add \| list \| show \| cancel` | Background tasks: now, `--cron` with `--tz`, or `--every 2h` |
| `gmini say` / `gmini transcribe` / `gmini wake` | Text to speech, speech to text and wake word detection |
| `gmini devices list \| revoke` / `gmini pair-code` | Device administration (requires the `admin` scope) |

Exit codes: `0` success, `1` error, `2` usage, `3` connection, `4` authentication or permission,
`5` busy or rate limited, `130` cancelled.

## Security

Tokens are stored in the system keyring (Windows Credential Manager, macOS Keychain, Secret Service) and
fall back to a `0600` file on headless servers. The local session token is never sent to an arbitrary
`--url`. Expose the core through Tailscale or TLS. See [SECURITY.md](SECURITY.md).

## Development

```bash
pip install -e ".[dev,repl]"
python -m pytest -q
ruff check src tests
```

## License

[MIT](LICENSE).
