"""Modèle normalisé d'une release GitHub.

Une release est un **fait daté** : elle appartient au repository, non au
snapshot. Son état peut toutefois évoluer — un brouillon finit par être publié —
ce qui justifie de la mettre à jour plutôt que de l'accumuler.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class Release(BaseModel):
    """Release publiée sur GitHub, ou encore à l'état de brouillon."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tag: str
    """Nom du tag ; identifie la release au sein du repository."""

    name: str | None = None
    published_at: datetime | None = None
    """Nulle tant que la release est un brouillon."""

    draft: bool = False
    prerelease: bool = False
