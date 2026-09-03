"""Export JSON : le jeu de données complet, sans perte.

Le JSON est le format de référence : il conserve les structures imbriquées —
langages, constats, releases — que le CSV doit aplatir et que le Markdown
résume.
"""

from githor.exporters.dataset import Dataset


def render_json(dataset: Dataset) -> str:
    """Sérialise le jeu de données en JSON indenté.

    Les dates sortent au format ISO 8601 en UTC, telles qu'elles sont stockées.
    """
    return dataset.model_dump_json(indent=2, exclude_none=False) + "\n"
