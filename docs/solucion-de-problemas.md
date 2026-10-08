# Solución de problemas

Primero mira qué ve la CLI: `gmini status` (con `--verbose` muestra cada petición) y el código de salida
(`echo $?` o `$LASTEXITCODE`). Los códigos están en [cli.md](cli.md#códigos-de-salida).

## Conexión

**"No se pudo conectar con http://...:8765" (código 3)**

- ¿El núcleo está corriendo? `systemctl --user status g-mini` o `docker compose ps`.
- ¿Escucha donde crees? El instalador usa `127.0.0.1` salvo que pases `--tailscale` o `--host`; desde
  otro equipo así no se puede llegar. Revisa `ExecStart` con
  `systemctl --user show g-mini -p ExecStart`.
- ¿Tailscale está conectado en ambos equipos? `tailscale status`.
- ¿Un firewall corta el puerto 8765? Ver [servidor.md](servidor.md#firewall).

**"El servidor ... no respondió a tiempo"**: la red está lenta o el servidor saturado. Sube el límite con
`--timeout 60`. Las respuestas en streaming esperan hasta 10 minutos sin datos.

**"El certificado TLS ... no es válido"**: el proxy no tiene un certificado válido para ese nombre.
Revisa el `Caddyfile` o usa la IP de Tailscale.

## Autenticación

**"El token no es válido o fue revocado" (código 4)**: el dispositivo se revocó o el servidor perdió
`devices.json` (reinstalación sin copia). Pide otro código y vuelve a emparejar.

**"No encontré el token de sesión del núcleo local"**: la app de escritorio no está abierta o la CLI no
sabe dónde guarda sus datos. Indícalo una vez: `gmini connect --home "C:\ruta\a\G-Mini-Agent"`, o usa
`GMINI_HOME` / `--token-file`.

**"El token no tiene permiso para esta acción" (código 4)**: `devices` y `pair-code` piden el alcance
`admin`, que no tienen los tokens de dispositivo. Ejecútalos en el servidor con su token de sesión.

**"El núcleo rechazó la conexión por el nombre de host"**: el núcleo escucha en `127.0.0.1` y recibió
otro `Host`, normalmente a través de un proxy. Usa `header_up Host {upstream_hostport}` (Caddy) o agrega
el nombre a `server.allowed_hosts` en la configuración del núcleo.

**"El código de emparejamiento no es válido o ya venció"**: duran 5 minutos, sirven una vez y se anulan
al quinto intento fallido. Genera otro.

## Agente

**"El agente está ocupado con otra conversación" (código 5)**: el núcleo atiende un turno a la vez.
Reintenta en unos segundos o encola el pedido con `gmini task add`, que espera su turno.

**"El agente tuvo un error al responder" con un detalle sobre el proveedor**: el servidor no tiene una
API key válida o el proveedor no responde. Configura `GMINI_KEY_*` en `~/.config/g-mini/env` (o en
`docker/.env`) y reinicia el servicio. Si el servidor usa Gemini CLI y el detalle dice que no tiene
sesión, entra al servidor y ejecuta `gemini` una vez para iniciarla.

**La respuesta llega toda junta al final**: algo en el camino acumula el streaming. Con un proxy, desactiva
el búfer (`flush_interval -1` en Caddy, `proxy_buffering off` en nginx).

**`gmini transcribe` o `gmini wake` no reconocen nada**: el STT del servidor está apagado o falta
`faster-whisper` (no viene en `requirements-server.txt`). Instálalo en el entorno del núcleo y activa
`voice.stt_enabled`.

## Servicio en Linux

**`systemctl --user` dice "Failed to connect to bus"**: entraste con `su` o `sudo -u`. Inicia sesión
directamente por SSH con ese usuario o exporta `XDG_RUNTIME_DIR=/run/user/$(id -u)`.

**El servicio se detiene al cerrar la sesión SSH**: falta `sudo loginctl enable-linger "$USER"`.

## Docker

**El contenedor queda `unhealthy` o se reinicia**: `docker logs g-mini` y los registros de
`/data/logs`. Si montaste una carpeta del host, debe pertenecer al uid 10001.

**"port is already allocated"**: otro proceso usa el puerto 8765 en esa IP. Cambia `GMINI_PORT` en
`docker/.env`.

## Terminal

**Acentos raros al redirigir la salida en PowerShell 5.1**: la CLI escribe UTF-8. Antes de redirigir,
ejecuta `[Console]::OutputEncoding = [Text.Encoding]::UTF8` o usa PowerShell 7.

**El chat interactivo no tiene historial en Git Bash**: sin pseudo-consola `prompt_toolkit` no puede
usar la terminal y la CLI lee la entrada estándar. Usa Windows Terminal o `winpty gmini chat`.

**Errores del llavero en Linux con escritorio** (Secret Service bloqueado o sin desbloquear): guarda los
tokens en el archivo privado con `GMINI_CREDENTIAL_STORE=file`.
