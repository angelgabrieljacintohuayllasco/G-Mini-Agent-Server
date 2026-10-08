# Seguridad

## Versiones con soporte

| Versión | Soporte |
|---|---|
| 0.1.x | Sí |

## Reportar una vulnerabilidad

No abras un issue público. Usa el reporte privado de GitHub en la pestaña **Security > Report a
vulnerability** de este repositorio, con los pasos para reproducirla y su impacto. Si afecta al núcleo
(la API remota, el agente o sus acciones), repórtala en el
[repositorio del núcleo](https://github.com/angelgabrieljacintohuayllasco/G-Mini-Agent).

## Cómo protege la CLI tus datos

- **Tokens**: se guardan en el llavero del sistema. Si no hay uno (servidores sin escritorio), en
  `credentials.json` con permisos `600` o, en Windows, con una ACL limitada a tu usuario. `profiles.json`
  nunca contiene tokens.
- **Token de sesión del núcleo local**: se lee del archivo en cada comando y solo se envía a
  `127.0.0.1` o a un perfil configurado explícitamente con `--home`/`--token-file`. Nunca a una URL
  arbitraria pasada con `--url`.
- **Proxies**: la CLI ignora `HTTP_PROXY` para el núcleo local, para que un proxy del sistema no vea el
  token de sesión.
- **Alcances**: emparejar entrega tokens sin `admin`. Los comandos de administración solo funcionan con
  el token de sesión del servidor o con un token de API creado a propósito.
- **Contenido del servidor**: el texto que llega del agente se muestra tal cual, sin interpretarlo como
  marcado de la terminal.

## Recomendaciones para el servidor

- Expón el núcleo por Tailscale o detrás de un proxy con TLS; no lo publiques en `0.0.0.0` en una red
  pública sin cifrado.
- Revoca los dispositivos que ya no uses: `gmini devices list` y `gmini devices revoke <id>`.
- Las API keys van en `~/.config/g-mini/env` o en `docker/.env`, ambos fuera de git y con permisos
  restringidos.
- Las copias de `GMINI_HOME` incluyen hashes de tokens y, sin llavero, `secrets.json`: guárdalas cifradas.
