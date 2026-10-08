"""Textos internos de argparse en español.

argparse obtiene sus textos ("usage:", "options", los mensajes de error) con
la función ``_`` de gettext a nivel de módulo, que se resuelve en cada
llamada. Reemplazarla por una tabla de traducción es el mecanismo previsto
para localizarlo; un texto que falte en la tabla se muestra en inglés.
"""

from __future__ import annotations

import argparse

_TRANSLATIONS = {
    "usage: ": "uso: ",
    "positional arguments": "argumentos",
    "options": "opciones",
    "optional arguments": "opciones",
    "subcommands": "subcomandos",
    "show this help message and exit": "muestra esta ayuda y termina",
    "show program's version number and exit": "muestra la versión y termina",
    "the following arguments are required: %s": "faltan estos argumentos: %s",
    "one of the arguments %s is required": "falta uno de estos argumentos: %s",
    "invalid choice: %(value)r (choose from %(choices)s)": (
        "opción no válida: %(value)r (elige entre %(choices)s)"
    ),
    "unrecognized arguments: %s": "argumentos no reconocidos: %s",
    "expected one argument": "falta un valor",
    "expected at least one argument": "falta al menos un valor",
    "expected at most one argument": "se espera como mucho un valor",
    "not allowed with argument %s": "no se puede usar junto con %s",
    "ambiguous option: %(option)s could match %(matches)s": (
        "opción ambigua: %(option)s puede ser %(matches)s"
    ),
    "invalid %(type)s value: %(value)r": "valor no válido (%(type)s): %(value)r",
    "argument %(argument_name)s: %(message)s": "argumento %(argument_name)s: %(message)s",
    "%(prog)s: error: %(message)s\n": "%(prog)s: error: %(message)s\n",
}


def translate(message: str) -> str:
    return _TRANSLATIONS.get(message, message)


def install() -> None:
    """Activa las traducciones. Es idempotente."""
    argparse._ = translate  # type: ignore[attr-defined]
