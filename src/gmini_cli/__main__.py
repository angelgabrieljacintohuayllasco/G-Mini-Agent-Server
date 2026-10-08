"""Permite ejecutar la CLI con ``python -m gmini_cli``.

Usa una importación absoluta para que también sirva como punto de entrada del
binario empaquetado con PyInstaller.
"""

import sys

from gmini_cli.cli import main

if __name__ == "__main__":
    sys.exit(main())
