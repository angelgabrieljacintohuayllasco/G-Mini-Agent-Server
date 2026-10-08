# Cómo contribuir

## Entorno

```bash
git clone https://github.com/angelgabrieljacintohuayllasco/G-Mini-Agent-Server
cd G-Mini-Agent-Server
python -m venv .venv
.venv/bin/pip install -e ".[dev,repl]"       # en Windows: .venv\Scripts\pip
```

## Antes de enviar cambios

```bash
python -m pytest -q
ruff check src tests
ruff format --check src tests
```

- Las pruebas levantan `tests/mock_server.py`, un servidor falso de la API remota v1, en un puerto libre
  de `127.0.0.1`: no necesitan red ni un núcleo real. Si el núcleo cambia el contrato, actualiza el
  servidor falso primero y luego la CLI.
- Para probar a mano: `python tests/mock_server.py --port 8799 --home /tmp/gmini-home` y, en otra
  terminal, `GMINI_HOME=/tmp/gmini-home gmini --url 127.0.0.1:8799 status`.
- Cada comando nuevo lleva su prueba de punta a punta en `tests/test_cli.py`.

## Estilo

- Identificadores en inglés; textos para el usuario, ayuda, documentación y mensajes de commit en
  español.
- Nada de emojis en la salida, el código ni la documentación.
- Los mensajes de error dicen qué pasó y qué hacer (`CliError(..., hint=...)`).
- stdout solo lleva datos; avisos y progreso van a stderr.

## Commits y pull requests

- Mensajes en español con [Conventional Commits](https://www.conventionalcommits.org/es/v1.0.0/):
  `feat(cli): ...`, `fix(docker): ...`, `docs: ...`, `test: ...`, `ci: ...`.
- Un cambio lógico por commit; el pull request explica el porqué y cómo se probó.

## Publicar una versión

1. Actualiza `__version__` en `src/gmini_cli/__init__.py` y agrega la sección en `CHANGELOG.md`.
2. Crea la etiqueta: `git tag v0.1.1 && git push origin v0.1.1`.
3. El flujo `Publicar versión` corre las pruebas, publica el wheel y el sdist en GitHub Releases y la
   imagen en GHCR. La versión del núcleo de la imagen se cambia con la variable de repositorio
   `GMINI_CORE_REF`.
