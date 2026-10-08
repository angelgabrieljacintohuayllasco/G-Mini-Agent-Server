# Conectar la app de escritorio a un servidor

La app de escritorio de G-Mini puede delegar trabajo a otro G-Mini emparejado (un VPS, una Raspberry Pi,
otra PC): le encarga tareas de fondo y consulta su estado por la API remota v1. La función llegó al núcleo
en la v0.3.0 (`backend/core/remote_servers.py`), así que la app debe tener esa versión o una posterior.

## 1. Preparar el servidor

Instálalo como indica [servidor.md](servidor.md) y comprueba desde el equipo de escritorio que responde:

```bash
curl http://100.71.131.70:8765/api/v1/health
```

## 2. Pedir un código en el servidor

```bash
gmini pair-code --label "PC de escritorio" --scopes chat,voice,tasks
```

## 3. Emparejar el núcleo de escritorio

El núcleo de escritorio canjea el código y guarda el token en su llavero (`remote_server_<id>`). Mientras
la pantalla de Ajustes no lo incluya, se hace con su API local, usando el token de sesión que está en
`data/runtime/session_token` dentro de la carpeta de G-Mini:

```powershell
$token = Get-Content "C:\ruta\a\G-Mini-Agent\data\runtime\session_token"
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/api/remote-servers/pair `
  -Headers @{ Authorization = "Bearer $token" } -ContentType "application/json" `
  -Body '{"url": "100.71.131.70", "code": "482913", "name": "tv-server"}'
```

```bash
# Linux o macOS
curl -X POST http://127.0.0.1:8765/api/remote-servers/pair \
  -H "Authorization: Bearer $(cat ~/G-Mini-Agent/data/runtime/session_token)" \
  -H "Content-Type: application/json" \
  -d '{"url": "100.71.131.70", "code": "482913", "name": "tv-server"}'
```

`GET /api/remote-servers` lista los servidores emparejados y
`GET /api/remote-servers/<id>/status` comprueba la conexión.

## 4. Delegar desde el chat

Con el servidor emparejado, pídeselo al agente con lenguaje natural:

> Encárgale a tv-server que todas las mañanas revise el clima y me avise por Telegram.

El agente usa sus acciones `remote_delegate` y `remote_task_status`, que crean y consultan tareas en el
servidor (`POST /api/v1/tasks`).

## Alternativa con la CLI

Desde el mismo equipo de escritorio también puedes hablar con el servidor sin pasar por la app:

```bash
gmini pair tv-server 482913            # usa otro código: cada uno sirve una sola vez
gmini -p tv-server task add "Revisa el clima a las 7" --cron "0 7 * * *" --tz America/Lima
gmini -p tv-server task list
gmini -p local status                  # y el núcleo de escritorio, con su token de sesión
```

## Revocar el acceso

En el servidor: `gmini devices list` y `gmini devices revoke <id>`. El núcleo de escritorio deja de
poder delegar en cuanto el token se revoca.
