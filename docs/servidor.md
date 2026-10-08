# Servidor 24/7

El núcleo de G-Mini corre sin escritorio con `python -m backend.main --headless`. Así sigue trabajando
cuando apagas tu PC: atiende la CLI, la app de escritorio, la extensión y los dispositivos, y ejecuta las
tareas programadas. Hay dos formas de instalarlo:

| | Instalador oficial (Linux) | Docker |
|---|---|---|
| Requisitos | systemd, git, curl | Docker con Compose v2 |
| Versión del núcleo | rama `main` (se actualiza al volver a ejecutarlo) | etiqueta fija (`GMINI_CORE_REF`, por defecto `v0.2.0`) |
| Privilegios | ninguno (servicio de usuario) | usuario sin root dentro del contenedor |
| Datos | `~/.local/share/g-mini` | volumen `/data` |
| API keys | `~/.config/g-mini/env` | `docker/.env` |

## Instalador oficial (Linux)

Vive en el repositorio del núcleo
([`deploy/linux/install-server.sh`](https://github.com/angelgabrieljacintohuayllasco/G-Mini-Agent/blob/main/deploy/linux/install-server.sh));
este repositorio no lo duplica. Instala `uv` y Python 3.13 en tu carpeta personal, clona el núcleo en
`~/g-mini-agent`, instala `backend/requirements-server.txt` y deja el servicio `g-mini` de
`systemctl --user`.

```bash
curl -fsSLO https://raw.githubusercontent.com/angelgabrieljacintohuayllasco/G-Mini-Agent/main/deploy/linux/install-server.sh
less install-server.sh                    # revísalo antes de ejecutarlo
bash install-server.sh --tailscale        # o --host 127.0.0.1 (por defecto) o --host 0.0.0.0, --port 8765
sudo loginctl enable-linger "$USER"       # sin esto, el servicio se detiene al cerrar tu sesión
```

Variables opcionales del instalador: `GMINI_BRANCH`, `GMINI_APP_DIR`, `GMINI_HOME` y `GMINI_PYTHON`.

Operación diaria:

```bash
systemctl --user status g-mini
journalctl --user -u g-mini -f
systemctl --user restart g-mini           # tras cambiar ~/.config/g-mini/env
bash install-server.sh --tailscale        # actualizar: trae el código nuevo y reinicia
```

### API keys

El núcleo lee primero las variables `GMINI_KEY_<NOMBRE>`, luego el llavero del sistema, después
`data/runtime/secrets.json` (solo si no hay llavero) y por último las variables estándar del proveedor
(`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`...). En un servidor lo más simple es el archivo
de entorno del servicio:

```bash
nano ~/.config/g-mini/env
#   GMINI_KEY_GOOGLE_API=...
#   GMINI_KEY_OPENAI_API=...
systemctl --user restart g-mini
```

### Emparejar desde el servidor

Con la CLI instalada en el servidor (`pipx install ...` o `uv tool install ...`, ver el README):

```bash
# si el núcleo escucha en 127.0.0.1 basta con:
gmini pair-code
# si escucha en la IP de Tailscale, crea primero un perfil que lea el token de sesión:
gmini connect 100.71.131.70 --home ~/.local/share/g-mini --name servidor
gmini pair-code
```

Sin la CLI, el instalador muestra el comando `curl` equivalente contra `POST /api/v1/pairing`.

## Docker

```bash
git clone https://github.com/angelgabrieljacintohuayllasco/G-Mini-Agent-Server
cd G-Mini-Agent-Server
cp docker/.env.example docker/.env        # GMINI_BIND, TZ y tus GMINI_KEY_*
docker compose -f docker/compose.yaml up -d
docker compose -f docker/compose.yaml ps  # debe quedar "healthy"
docker exec -it g-mini gmini status
docker exec -it g-mini gmini pair-code --host <ip-del-servidor>
```

La imagen:

- se publica en `ghcr.io/angelgabrieljacintohuayllasco/g-mini-agent-server` para `linux/amd64` y
  `linux/arm64` (etiquetas `latest`, `0.1` y `0.1.0`);
- descarga el núcleo en la etiqueta `GMINI_CORE_REF` e instala solo `requirements-server.txt`;
- corre como el usuario `gmini` (uid 10001) con `tini` como proceso inicial;
- guarda todo en el volumen `/data` (`GMINI_HOME`): configuración, bases de datos, dispositivos y
  registros (`/data/logs`);
- trae la CLI `gmini`, que dentro del contenedor ya apunta al núcleo con su token de sesión
  (`/data/data/runtime/session_token`);
- comprueba `GET /api/v1/health` cada 30 s.

Para construirla con otra versión del núcleo:

```bash
GMINI_CORE_REF=v0.2.0 docker compose -f docker/compose.yaml build
# o sin compose
docker build -f docker/Dockerfile --build-arg GMINI_CORE_REF=v0.2.0 -t g-mini-agent-server .
```

Si en vez del volumen usas una carpeta del host, dale permisos al usuario del contenedor:
`sudo chown -R 10001:10001 ./datos`.

La primera vez que el flujo de publicación sube la imagen a GHCR queda privada: cámbiala a pública en
la configuración del paquete en GitHub si quieres que otros la descarguen sin iniciar sesión.

## Tailscale

[Tailscale](https://tailscale.com) crea una red privada entre tus equipos sin abrir puertos en el router.
Es la forma recomendada de usar el servidor desde fuera de casa.

1. Instálalo en el servidor y en cada cliente, con la misma cuenta.
2. Mira la IP del servidor: `tailscale ip -4` (por ejemplo `100.71.131.70`).
3. Haz que el núcleo escuche ahí: `bash install-server.sh --tailscale`, o `GMINI_BIND=100.71.131.70`
   en `docker/.env`.
4. Empareja usando esa IP o el nombre de MagicDNS: `gmini pair tv-server 482913`.

## Firewall

Si el núcleo escucha en `0.0.0.0`, limita quién llega al puerto. Con `ufw`:

```bash
sudo ufw default deny incoming
sudo ufw allow in on tailscale0 to any port 8765 proto tcp
sudo ufw allow OpenSSH
sudo ufw enable
```

Docker publica puertos con sus propias reglas de iptables y `ufw` no las filtra. Con Docker no publiques
en `0.0.0.0`: usa `GMINI_BIND` con `127.0.0.1` o la IP de Tailscale.

## Proxy con TLS

Para un dominio público, deja el núcleo en `127.0.0.1` y pon delante un proxy con TLS. Ejemplo de
`Caddyfile` (Caddy obtiene el certificado solo):

```caddyfile
gmini.midominio.com {
	reverse_proxy 127.0.0.1:8765 {
		# El núcleo en loopback solo acepta Host 127.0.0.1:<puerto> (protección contra DNS rebinding).
		header_up Host {upstream_hostport}
		# Entrega cada evento SSE apenas llega.
		flush_interval -1
	}
}
```

El WebSocket `/api/v1/ws` funciona sin configuración extra. Con nginx usa `proxy_set_header Host
127.0.0.1:8765`, `proxy_buffering off` y las cabeceras `Upgrade`/`Connection` para el WebSocket.
Empareja escribiendo la URL completa, porque el QR del núcleo anuncia `127.0.0.1`:

```bash
gmini pair https://gmini.midominio.com 482913
```

Otra opción sin abrir puertos es `tailscale serve`, que publica el puerto con HTTPS dentro de tu red.

## Copias de seguridad

Todo el estado vive en `GMINI_HOME`: `config.user.yaml`, las bases SQLite de `data/` (memoria,
conversaciones, tareas), `data/runtime/devices.json` (hashes de los tokens de dispositivo) y, si no hay
llavero, `data/runtime/secrets.json`. Trata la copia como un secreto.

```bash
# instalador oficial
systemctl --user stop g-mini
tar -czf g-mini-$(date +%F).tar.gz -C ~/.local/share g-mini
systemctl --user start g-mini

# Docker (el volumen se llama <proyecto>_g-mini-data)
docker compose -f docker/compose.yaml stop
docker run --rm -v g-mini_g-mini-data:/data:ro -v "$PWD":/backup alpine \
  tar -czf /backup/g-mini-data-$(date +%F).tar.gz -C /data .
docker compose -f docker/compose.yaml start
```

Para restaurar, detén el servicio, extrae el archivo en la misma carpeta (o volumen) y vuelve a
arrancarlo. Los tokens de dispositivo siguen valiendo porque viajan en `devices.json`.

## Actualizaciones

| Qué | Cómo |
|---|---|
| Núcleo con el instalador | `bash install-server.sh --tailscale` |
| Núcleo con Docker | `docker compose -f docker/compose.yaml pull && docker compose -f docker/compose.yaml up -d` |
| CLI | `pipx upgrade gmini-cli` o `uv tool upgrade gmini-cli` |

`gmini status` muestra la versión y el protocolo del servidor; la CLI avisa si el servidor usa un
protocolo más nuevo que el que conoce.

## Windows como servidor

Lo más sencillo es Docker Desktop (WSL 2) con la misma imagen. Si prefieres el núcleo sin contenedor,
crea una tarea programada que lo inicie al entrar a tu sesión. El Programador de tareas no fija variables
de entorno, así que se usa un `.cmd`:

```bat
@echo off
rem C:\g-mini-agent\gmini-server.cmd
set "GMINI_HOME=%LOCALAPPDATA%\g-mini"
cd /d C:\g-mini-agent
.venv\Scripts\python.exe -m backend.main --headless --host 100.71.131.70 --port 8765 >> "%LOCALAPPDATA%\g-mini\server.log" 2>&1
```

```powershell
schtasks /Create /TN "G-Mini Server" /SC ONLOGON /RL LIMITED /TR "C:\g-mini-agent\gmini-server.cmd"
schtasks /Run /TN "G-Mini Server"
```

Limitaciones: corre solo con tu sesión iniciada y no se reinicia si el proceso se cae. Para una máquina
dedicada, Docker o Linux son más robustos.
