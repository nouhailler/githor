"""Modèle normalisé d'une recommandation produite par le conseiller IA (V0.4).

Githor priorise, Ollama rédige : chaque recommandation reste rattachée à la
règle qui l'a motivée. ``source_rule`` est ``None`` uniquement dans le cas
dégradé où la réponse d'Ollama n'a pas pu être associée aux constats un à un —
la dégradation se montre, elle ne se cache jamais derrière une règle inventée.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

_FROZEN = ConfigDict(extra="forbid", frozen=True)


class AdviceItem(BaseModel):
    """Une recommandation, dans l'ordre de priorité calculé par Githor."""

    model_config = _FROZEN

    rank: int
    source_rule: str | None
    """Identifiant de la règle à l'origine de cette recommandation."""

    title: str
    recommendation: str


class Advice(BaseModel):
    """Recommandations produites pour un dépôt, à un instant donné."""

    model_config = _FROZEN

    generated_at: datetime
    model: str
    """Modèle Ollama interrogé : la formulation n'est pas reproductible à l'identique."""

    items: tuple[AdviceItem, ...]

    degraded: bool = False
    """Vrai si la réponse d'Ollama n'a pas pu être associée aux constats un à un.

    ``items`` porte alors une seule entrée, avec le texte brut renvoyé par
    Ollama et ``source_rule`` à ``None``.
    """
