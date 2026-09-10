"""Interface web locale (Flask), en lecture seule.

Quatrième vue sur les mêmes données que la CLI, les exports et la TUI :
aucune logique de calcul propre à cette couche. Voir ``githor.web.app``.
"""

from githor.web.app import create_app

__all__ = ["create_app"]
