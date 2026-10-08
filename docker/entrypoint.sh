#!/bin/sh
# Prepara la carpeta de datos (GMINI_HOME) y ejecuta el comando del contenedor,
# por defecto el núcleo de G-Mini en modo servidor.
set -eu

data_dir="${GMINI_HOME:-/data}"
if ! mkdir -p "$data_dir/logs" 2>/dev/null || [ ! -w "$data_dir" ]; then
    echo "Error: no se puede escribir en $data_dir (uid $(id -u))." >&2
    echo "Si montas una carpeta del host, dale permisos al usuario 10001:" >&2
    echo "  sudo chown -R 10001:10001 <carpeta>" >&2
    exit 1
fi

exec "$@"
