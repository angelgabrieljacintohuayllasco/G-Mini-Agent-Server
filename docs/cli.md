# Guía de la CLI `gmini`

`gmini` es un cliente de la API remota v1 del núcleo de G-Mini Agent. No ejecuta el agente: habla con
un núcleo que corre en esta máquina (la app de escritorio) o en un servidor.

## Perfiles

Un perfil guarda la dirección de un servidor y cómo autenticarse. El perfil activo se usa por defecto;
`--profile <nombre>` (o `GMINI_PROFILE`) elige otro para un comando.

| Autenticación | Cómo se crea | De dónde sale el token |
|---|---|---|
| `token guardado` | `gmini pair` o `gmini connect --api-token` | Llavero del sistema o archivo de credenciales |
| `sesión local` | `gmini connect` contra `127.0.0.1`, o con `--home`/`--token-file` | Archivo `data/runtime/session_token` del núcleo, leído en cada comando |
| `sin token` | `gmini connect` a un servidor remoto sin emparejar | Ninguno: solo sirve `gmini status` |

Siempre existe un perfil `local` implícito (`http://127.0.0.1:8765` con el token de sesión). El token de
sesión se busca, en orden, en: `--token-file`, `GMINI_TOKEN_FILE`, `GMINI_HOME`, la carpeta indicada con
`connect --home`, la carpeta actual si es el repositorio del núcleo, `~/.local/share/g-mini` (instalador
oficial de Linux), la carpeta de datos de la app de escritorio y `~/.gmini`. Con `--url` a un host que no
sea `127.0.0.1` el token de sesión nunca se envía.

```bash
gmini connect                                   # núcleo de esta máquina
gmini connect --home "C:\G-Mini-Agent"          # si no encuentra el token de sesión solo
gmini connect https://gmini.midominio.com --name vps
gmini pair 100.71.131.70 482913                 # crea el perfil con el token del dispositivo
gmini profiles list
gmini profiles use vps
gmini profiles remove vps --yes
```

Las direcciones sin esquema usan `http://` y el puerto 8765 (`100.71.131.70`, `tv-server:9000`). Con
esquema se respeta el puerto estándar: `https://gmini.midominio.com` va al 443.

## Emparejar

1. En el servidor, con un perfil que tenga el permiso `admin` (token de sesión o de API):
   `gmini pair-code`. Muestra el código, el comando para el otro equipo y un QR con el enlace
   `gmini://pair?host=...&port=...&code=...`. Si el núcleo anunció `127.0.0.1`, la CLI prueba la IP de
   Tailscale del equipo y la usa si responde; `--host <ip>` la fija a mano.
2. En el equipo nuevo: `gmini pair <host> <código>` o `gmini pair "gmini://pair?..."`.

Opciones de `pair-code`: `--scopes chat,voice,tasks` (por defecto), `--type cli|pc|esp32|rpi`,
`--label` y `--no-qr`. El código vence a los 5 minutos y sirve una vez; el servidor acepta 5 intentos por
minuto por IP y anula el código al quinto fallo.

## Chat

```bash
gmini chat                          # interactivo
gmini chat "hola"                   # una respuesta y termina
gmini ask "resume esto" -a acta.pdf # con adjunto (hasta 20 MB)
cat notas.txt | gmini ask "ordénalas por prioridad"
gmini ask "estado del servidor" --json
```

- El texto llega en streaming por SSE. `--transport ws` usa el WebSocket; `--no-stream` espera la
  respuesta completa.
- Con la salida en una terminal se ve todo lo que el agente escribe; en una tubería o un archivo solo
  sale la conclusión del turno (`reply`), lista para otro programa. Acciones y avisos van a stderr
  (`--quiet` los oculta).
- Ctrl+C cancela la respuesta en curso: la CLI deja de mostrarla y le pide al núcleo que se detenga.

Comandos dentro del chat interactivo:

| Comando | Qué hace |
|---|---|
| `/ayuda` | Lista los comandos |
| `/sesiones` | Conversaciones recientes |
| `/historial [n]` | Últimos mensajes de la conversación |
| `/estado` | Estado del agente: ocupado, emoción, aprobaciones pendientes |
| `/aprobar`, `/rechazar` | Resuelve acciones que esperan aprobación |
| `/limpiar` | Limpia la pantalla |
| `/salir` | Termina (también Ctrl+D; en Windows Ctrl+Z y Enter) |

Con `prompt_toolkit` instalado (extra `repl`) hay historial persistente y edición de línea. Si la
entrada estándar no es una terminal, cada línea se envía como un mensaje:
`printf 'hola\n/estado\n' | gmini chat`.

El núcleo mantiene una conversación activa, compartida con la app de escritorio: la API v1 todavía no
permite abrir una nueva desde un cliente.

## Tareas en segundo plano

```bash
gmini task add "Revisa el correo y resume lo urgente"                        # una vez, ahora
gmini task add "Resumen de noticias" --every 2h                              # 90m, 1d, 3600 (mínimo 60 s)
gmini task add "Reporte semanal" --cron "0 9 * * 1" --tz America/Lima --notify telegram:123456789
gmini task list --status scheduled
gmini task show tsk_2b03510f4ff7
gmini task cancel tsk_2b03510f4ff7
```

- Sin `--tz`, `--cron` usa la zona horaria de este equipo (en Windows se traduce la zona del sistema a
  su nombre IANA; si no se puede, pide `--tz`).
- `--notify` acepta destinos del gateway del núcleo con la forma `canal:destino`.
- `--at <fecha ISO>` queda listo para cuando el núcleo lo acepte; la v0.2 responde que aún no está
  disponible.

## Voz

```bash
gmini say "La reunión empieza en cinco minutos"          # lo reproduce
gmini say "Hola" --out hola.wav                          # lo guarda (--play además lo reproduce)
gmini say "Hola" --out - | aplay                         # WAV por stdout
gmini transcribe nota.wav                                # voz a texto
gmini wake clip.wav                                      # ¿empieza con "Oye G-Mini"?
```

La reproducción usa `winsound` en Windows, `afplay` en macOS y `paplay`, `pw-play`, `aplay` o `ffplay`
en Linux. `transcribe` y `wake` necesitan el STT activo en el núcleo (`faster-whisper`). `wake`
termina con código 1 si no detecta la palabra de activación, para usarlo en scripts.

## Administración

Requiere un perfil con el permiso `admin` (token de sesión o de API):

```bash
gmini devices list
gmini devices revoke dev_1b2a833ca85a --yes
gmini pair-code --scopes chat,voice,node --type esp32
```

## Opciones globales

| Opción | Variable | Uso |
|---|---|---|
| `-p, --profile` | `GMINI_PROFILE` | Perfil a usar |
| `--url` | `GMINI_URL` | Servidor sin perfil |
| `--token` | `GMINI_TOKEN` | Token para esta llamada |
| `--token-file` | `GMINI_TOKEN_FILE` | Archivo con el token |
| `--timeout` | | Límite de cada petición (30 s; el streaming espera hasta 10 min sin datos) |
| `--plain` | `GMINI_PLAIN=1` | Sin colores ni decoración |
| `-v, --verbose` | | Muestra las peticiones HTTP |
| `--json` | | Salida en JSON (en los comandos que la tienen) |

Otras variables: `GMINI_CONFIG_DIR` (carpeta de perfiles), `GMINI_DATA_DIR` (historial del chat),
`GMINI_CREDENTIAL_STORE` (`auto`, `keyring` o `file`), `GMINI_HOME` (carpeta de datos del núcleo local),
`GMINI_COUNTRY` (país para traducir la zona horaria de Windows) y `NO_COLOR`.

## Archivos

| Sistema | Perfiles y credenciales | Historial del chat |
|---|---|---|
| Windows | `%LOCALAPPDATA%\gmini\` | `%LOCALAPPDATA%\gmini\chat_history` |
| Linux | `~/.config/gmini/` | `~/.local/share/gmini/chat_history` |
| macOS | `~/Library/Application Support/gmini/` | `~/Library/Application Support/gmini/chat_history` |

`profiles.json` nunca contiene tokens. `credentials.json` solo existe si no hay llavero del sistema y queda
con permisos `600` (en Windows, con una ACL solo para tu usuario).

## Códigos de salida

| Código | Significado |
|---|---|
| 0 | Correcto |
| 1 | Error del servidor o del agente (o `wake` sin detección) |
| 2 | Uso incorrecto: faltan argumentos o no son válidos |
| 3 | No se pudo conectar (red, DNS, TLS o tiempo de espera) |
| 4 | Token ausente, inválido o revocado, o sin permiso |
| 5 | Agente ocupado o demasiados intentos: se puede reintentar |
| 130 | Cancelado con Ctrl+C |

## Terminales

- **cmd y PowerShell**: los acentos y los bloques del QR se ven bien en la consola. Si rediriges la
  salida en PowerShell 5.1 (`gmini ask ... > archivo.txt`), configura antes
  `[Console]::OutputEncoding = [Text.Encoding]::UTF8`: la CLI escribe UTF-8.
- **Git Bash**: funciona tal cual. Sin pseudo-consola, el chat interactivo usa la entrada estándar en
  vez de `prompt_toolkit`.
- **Linux y macOS**: sin ajustes.
