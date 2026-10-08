# syntax=docker/dockerfile:1.7
#
# Núcleo de G-Mini Agent en modo servidor (sin escritorio), listo para un VPS,
# una Raspberry Pi o cualquier equipo con Docker.
#
#   docker build -f docker/Dockerfile -t g-mini-agent-server .
#   docker build -f docker/Dockerfile --build-arg GMINI_CORE_REF=v0.3.2 -t g-mini-agent-server .
#
# El núcleo se descarga del repositorio principal en una versión fija
# (GMINI_CORE_REF: etiqueta, rama o commit) e instala solo
# backend/requirements-server.txt. La CLI gmini de este repositorio queda
# dentro para generar códigos de emparejamiento:
#
#   docker exec -it g-mini gmini pair-code --host <ip-del-servidor>

ARG PYTHON_VERSION=3.12

# ── Código del núcleo en la versión pedida ─────────────────────────────────
FROM python:${PYTHON_VERSION}-slim AS core-source
ARG GMINI_CORE_REPO=https://github.com/angelgabrieljacintohuayllasco/G-Mini-Agent.git
ARG GMINI_CORE_REF=v0.3.2
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN git init -q /src \
    && git -C /src remote add origin "${GMINI_CORE_REPO}" \
    && git -C /src fetch -q --depth 1 origin "${GMINI_CORE_REF}" \
    && git -C /src checkout -q FETCH_HEAD \
    && git -C /src rev-parse HEAD > /src/.core-commit \
    && test -f /src/backend/requirements-server.txt \
    && rm -rf /src/.git /src/.github /src/electron /src/tests

# ── Dependencias en entornos virtuales ─────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
COPY --from=core-source /src/backend/requirements-server.txt /tmp/requirements-server.txt
# GMINI_VOICE=1 suma Whisper para transcribir (dispositivos con micrófono, palabra de activación).
ARG GMINI_VOICE=0
RUN python -m venv /opt/g-mini/venv \
    && /opt/g-mini/venv/bin/pip install --upgrade pip wheel \
    && /opt/g-mini/venv/bin/pip install -r /tmp/requirements-server.txt \
    && if [ "$GMINI_VOICE" = "1" ]; then /opt/g-mini/venv/bin/pip install "faster-whisper>=1.0.0"; fi
COPY pyproject.toml README.md LICENSE /tmp/cli/
COPY src /tmp/cli/src
RUN python -m venv /opt/gmini-cli \
    && /opt/gmini-cli/bin/pip install /tmp/cli

# ── Imagen final ───────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim AS runtime
ARG GMINI_CORE_REF=v0.3.2
LABEL org.opencontainers.image.title="G-Mini Agent Server" \
      org.opencontainers.image.description="Núcleo de G-Mini Agent en modo servidor, con la CLI gmini" \
      org.opencontainers.image.source="https://github.com/angelgabrieljacintohuayllasco/G-Mini-Agent-Server" \
      org.opencontainers.image.licenses="MIT" \
      io.github.g-mini.core-ref="${GMINI_CORE_REF}"

# libsndfile convierte el MP3 de las voces de Edge a WAV; tini reparte señales
# y recoge los procesos que lanza el agente (terminal, servidores MCP).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libsndfile1 tini \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin gmini \
    && mkdir -p /data/logs \
    && chown -R gmini:gmini /data

COPY --from=build /opt/g-mini/venv /opt/g-mini/venv
COPY --from=build /opt/gmini-cli /opt/gmini-cli
COPY --from=core-source /src /opt/g-mini/app
COPY docker/entrypoint.sh /usr/local/bin/gmini-entrypoint
COPY docker/gmini-cli.sh /usr/local/bin/gmini

# El código queda de solo lectura para el usuario del servicio. El núcleo
# escribe sus registros junto al código: se redirigen al volumen.
RUN chmod 0755 /usr/local/bin/gmini-entrypoint /usr/local/bin/gmini \
    && ln -s /data/logs /opt/g-mini/app/logs \
    && /opt/g-mini/venv/bin/python -m compileall -q /opt/g-mini/app/backend

ENV GMINI_HOME=/data \
    PATH=/opt/g-mini/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUTF8=1

USER gmini
WORKDIR /opt/g-mini/app
VOLUME ["/data"]
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; r = urllib.request.urlopen('http://127.0.0.1:8765/api/v1/health', timeout=4); sys.exit(0 if r.status == 200 else 1)"]

ENTRYPOINT ["tini", "--", "gmini-entrypoint"]
CMD ["python", "-m", "backend.main", "--headless", "--host", "0.0.0.0", "--port", "8765"]
