# Cambios

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/); el proyecto usa
[versionado semántico](https://semver.org/lang/es/).

## [0.1.0] - 2026-10-07

Primera versión. Compatible con la API remota v1 del núcleo de G-Mini Agent (v0.2.0 en adelante).

### Agregado

- CLI `gmini` para Windows, Linux y macOS, con textos y ayuda en español y salida `--plain` y `--json`.
- Perfiles para varios servidores (`connect`, `profiles list|use|remove`) y perfil `local` implícito que
  usa el token de sesión del núcleo de la misma máquina.
- Emparejamiento con código de 6 dígitos o enlace `gmini://pair` (`pair`), y generación de códigos con
  QR en la terminal (`pair-code`).
- Tokens en el llavero del sistema, con respaldo en un archivo privado (`600` o ACL de usuario en
  Windows) cuando no hay llavero.
- Chat con streaming por SSE o WebSocket: interactivo con comandos `/` (`chat`) o de una sola respuesta,
  apto para tuberías (`chat "texto"`, `ask`). Ctrl+C cancela la respuesta también en el servidor.
- Aprobación remota de acciones (`approve`, `reject`, `/aprobar`, `/rechazar`).
- Tareas en segundo plano con cron, intervalo y avisos (`task add|list|show|cancel`).
- Voz: texto a voz con reproducción o WAV (`say`), voz a texto (`transcribe`) y palabra de activación
  (`wake`).
- Administración de dispositivos (`devices list|revoke`).
- Mensajes de error claros para cada código de la API y códigos de salida estables para scripts.
- Imagen Docker del núcleo en modo servidor: versión fija del núcleo, solo dependencias de servidor,
  usuario sin root, volumen `/data`, healthcheck y la CLI incluida. Ejemplo de Docker Compose con las
  API keys en `.env`.
- Guías de la CLI, del servidor 24/7 (instalador oficial, Docker, Tailscale, TLS, copias), de la conexión
  con la app de escritorio y de solución de problemas.
- Integración continua: estilo, pruebas en Windows y Linux (Python 3.10 a 3.13), wheel y sdist, e imagen
  Docker probada con el núcleo real. Publicación por etiqueta en GitHub Releases y GHCR.

[0.1.0]: https://github.com/angelgabrieljacintohuayllasco/G-Mini-Agent-Server/releases/tag/v0.1.0
