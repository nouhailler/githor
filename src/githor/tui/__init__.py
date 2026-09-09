"""Interface interactive (Textual), en lecture seule.

Troisième vue sur les mêmes données que la CLI et les exports : aucune
requête ni logique de rendu propre à cette couche. Voir ``githor.tui.app``.
"""

from githor.tui.app import GithorApp

__all__ = ["GithorApp"]
