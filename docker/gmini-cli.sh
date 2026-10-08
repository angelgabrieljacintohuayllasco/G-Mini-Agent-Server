#!/bin/sh
# gmini dentro del contenedor: habla con el núcleo local usando su token de sesión.
export GMINI_URL="${GMINI_URL:-http://127.0.0.1:8765}"
export GMINI_TOKEN_FILE="${GMINI_TOKEN_FILE:-${GMINI_HOME:-/data}/data/runtime/session_token}"
exec /opt/gmini-cli/bin/gmini "$@"
