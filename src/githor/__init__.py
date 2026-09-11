"""Githor — inventaire et audit local de repositories GitHub.

Le paquet est organisé en couches indépendantes :

- ``github``      : accès bas niveau à l'API REST GitHub (httpx).
- ``models``      : modèles normalisés, indépendants de la forme des réponses API.
- ``collectors``  : orchestration API -> normalisation -> modèles.
- ``storage``     : persistance SQLite via SQLAlchemy.
- ``exporters``   : export des données stockées (JSON, CSV, Markdown).
- ``utils``       : utilitaires transverses.
"""

__version__ = "0.8.0"

__all__ = ["__version__"]
