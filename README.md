# G-Mini Agent Server

[![CI](https://github.com/angelgabrieljacintohuayllasco/G-Mini-Agent-Server/actions/workflows/ci.yml/badge.svg)](https://github.com/angelgabrieljacintohuayllasco/G-Mini-Agent-Server/actions/workflows/ci.yml)
[![Licencia MIT](https://img.shields.io/badge/licencia-MIT-blue.svg)](LICENSE)

CLI `gmini` y distribución en Docker del núcleo de
[G-Mini Agent](https://github.com/angelgabrieljacintohuayllasco/G-Mini-Agent) para tenerlo
trabajando 24/7 en un VPS, una Raspberry Pi o un equipo de casa, y usarlo desde cualquier terminal.

- **CLI `gmini`**: conversa con el agente en streaming, encola tareas programadas, convierte texto a voz
  y administra varios servidores con perfiles. Funciona igual en cmd, PowerShell, Git Bash, Linux y macOS.
- **Servidor**: imagen Docker del núcleo en modo servidor (sin escritorio), con usuario sin privilegios,
  volumen de datos y healthcheck. En Linux también sirve el instalador oficial del núcleo
  (`systemctl --user`, sin sudo).

Todo habla con la [API remota v1](https://github.com/angelgabrieljacintohuayllasco/G-Mini-Agent/blob/main/docs/protocol/remote-api-v1.md)
del núcleo (REST, SSE y WebSocket) y se autentica con tokens por dispositivo.

[English version](README.en.md)

![Arquitectura: clientes, red privada o proxy con TLS y el núcleo como servicio](docs/assets/arquitectura.svg)

## Instalar la CLI

Requiere Python 3.10 o superior.

```bash
# con pipx
pipx install "gmini-cli[repl] @ git+https://github.com/angelgabrieljacintohuayllasco/G-Mini-Agent-Server"

# o con uv
uv tool install "gmini-cli[repl] @ git+https://github.com/angelgabrieljacintohuayllasco/G-Mini-Agent-Server"
```

También puedes instalar el wheel publicado en cada
[versión](https://github.com/angelgabrieljacintohuayllasco/G-Mini-Agent-Server/releases):
`pipx install gmini_cli-0.1.0-py3-none-any.whl`. El extra `repl` agrega historial y edición de
línea al chat interactivo; sin él se usa `readline` o la entrada estándar.

## Inicio rápido

1. **En el servidor**, pide un código de emparejamiento (vale 5 minutos y sirve una sola vez):

   ```bash
   gmini pair-code              # con la CLI instalada en el servidor
   ```

   Si el núcleo escucha en la IP de Tailscale, crea antes el perfil que lee su token de sesión:
   `gmini connect 100.71.131.70 --home ~/.local/share/g-mini --name servidor`. En Docker:
   `docker exec -it g-mini gmini pair-code --host <ip-del-servidor>`.

2. **En tu equipo**, empareja y prueba:

   ```bash
   gmini pair 100.71.131.70 482913     # guarda un perfil con el token del dispositivo
   gmini status                        # salud, versión, latencia y validez del token
   gmini chat                          # chat interactivo con streaming
   ```

3. Úsalo desde scripts:

   ```bash
   gmini ask "resume en tres líneas" < informe.txt
   gmini ask "¿qué tareas tengo hoy?" --json
   gmini task add "Revisa mi correo y resume lo urgente" --cron "0 8 * * *" --tz America/Lima --notify telegram:123456789
   gmini say "La reunión empieza en cinco minutos" --out aviso.wav
   ```

En el mismo equipo que la app de escritorio no hace falta emparejar: el perfil `local` usa el token de
sesión del núcleo (`gmini connect --home <carpeta-de-datos>` si no lo encuentra solo).

## Servidor 24/7

| Opción | Cuándo usarla | Guía |
|---|---|---|
| Instalador oficial (Linux) | VPS o mini PC con systemd; actualizaciones desde la rama `main` | [docs/servidor.md](docs/servidor.md#instalador-oficial-linux) |
| Docker | Cualquier sistema con Docker; versión fija del núcleo | [docs/servidor.md](docs/servidor.md#docker) |

Instalador oficial del núcleo (no necesita sudo; Python y dependencias van en tu carpeta personal):

```bash
curl -fsSLO https://raw.githubusercontent.com/angelgabrieljacintohuayllasco/G-Mini-Agent/main/deploy/linux/install-server.sh
bash install-server.sh --tailscale        # escucha en la IP de Tailscale
sudo loginctl enable-linger "$USER"       # que siga corriendo sin sesión abierta
```

Docker:

```bash
cp docker/.env.example docker/.env        # completa GMINI_BIND y tus GMINI_KEY_*
docker compose -f docker/compose.yaml up -d
docker compose -f docker/compose.yaml exec g-mini gmini status
```

Para usarlo fuera de tu red se recomienda [Tailscale](https://tailscale.com): el servidor queda en una
IP privada `100.x` sin abrir puertos. Si necesitas un dominio público, ponlo detrás de un proxy con TLS
(ejemplo con Caddy en [docs/servidor.md](docs/servidor.md#proxy-con-tls)).

## Comandos

| Comando | Qué hace |
|---|---|
| `gmini pair <host> <código>` | Empareja este equipo con un servidor (también acepta el enlace `gmini://pair?...` del QR) |
| `gmini connect [url]` | Guarda un servidor como perfil; sin URL, el núcleo de esta máquina |
| `gmini profiles list \| use \| remove` | Perfiles guardados y perfil activo |
| `gmini status` | Salud, modo, versión, latencia, token, alcances y estado del agente |
| `gmini chat` | Chat interactivo: `/ayuda`, `/sesiones`, `/historial`, `/estado`, `/aprobar`, `/rechazar`, `/salir` |
| `gmini chat "texto"`, `gmini ask "texto"` | Una respuesta; lee de una tubería; `--json`, `--no-stream`, `-a archivo` |
| `gmini approve`, `gmini reject` | Resuelve las acciones que el agente dejó esperando aprobación |
| `gmini task add \| list \| show \| cancel` | Tareas en segundo plano: ahora, `--cron` con `--tz` o `--every 2h` |
| `gmini say "texto"` | Texto a voz: lo reproduce o lo guarda con `--out archivo.wav` |
| `gmini transcribe audio.wav` | Voz a texto con el STT del servidor |
| `gmini wake clip.wav` | Comprueba si el clip empieza con la palabra de activación |
| `gmini devices list \| revoke <id>` | Dispositivos emparejados (requiere permiso `admin`) |
| `gmini pair-code` | Código de emparejamiento con QR en la terminal (requiere permiso `admin`) |

Opciones globales: `--profile`, `--url`, `--token`, `--token-file`, `--timeout`, `--plain` y
`--verbose`. Detalle de cada comando, variables de entorno y códigos de salida en
[docs/cli.md](docs/cli.md).

## Seguridad

- Cada equipo tiene su propio token de dispositivo con alcances (`chat`, `voice`, `tasks`). Se revoca
  desde el servidor con `gmini devices revoke <id>` sin afectar a los demás.
- La CLI guarda los tokens en el llavero del sistema (Administrador de credenciales de Windows, Llavero
  de macOS, Secret Service). En servidores sin llavero usa un archivo con permisos `600`
  (en Windows, con una ACL solo para tu usuario).
- El token de sesión del núcleo local nunca se envía a una URL indicada con `--url` que no sea
  `127.0.0.1`; solo lo usan los perfiles configurados para eso.
- Expón el núcleo por Tailscale o detrás de TLS. En modo servidor todas las rutas exigen token, pero el
  tráfico HTTP sin cifrar se puede leer en la red.

Más detalles y cómo reportar vulnerabilidades en [SECURITY.md](SECURITY.md).

## Documentación

- [Guía de la CLI](docs/cli.md)
- [Servidor 24/7: instalador, Docker, Tailscale, TLS y copias de seguridad](docs/servidor.md)
- [Conectar la app de escritorio a un servidor](docs/escritorio.md)
- [Solución de problemas](docs/solucion-de-problemas.md)

## Desarrollo

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev,repl]"      # en Windows: .venv\Scripts\pip
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests
```

Las pruebas usan un servidor falso de la API v1 (`tests/mock_server.py`) y no necesitan red. Ver
[CONTRIBUTING.md](CONTRIBUTING.md).

## Licencia

[MIT](LICENSE).
