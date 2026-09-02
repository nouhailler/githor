"""Modèles normalisés de l'activité d'un repository.

L'activité se mesure sur une fenêtre glissante : Githor ne télécharge jamais
l'historique complet d'un dépôt, seulement la période configurée.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class Commit(BaseModel):
    """Commit relevé dans la fenêtre d'historique."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sha: str
    author: str | None = None
    message: str | None = None
    committed_at: datetime | None = None


class Activity(BaseModel):
    """Synthèse de l'activité récente d'un repository."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    window_days: int
    """Profondeur réellement téléchargée, en jours."""

    commits: tuple[Commit, ...] = ()

    last_commit_at: datetime | None = None

    commits_30_days: int | None = None
    """Nul si la fenêtre configurée est plus courte que 30 jours."""

    commits_90_days: int | None = None
    """Nul si la fenêtre configurée est plus courte que 90 jours."""

    truncated: bool = False
    """Vrai si la borne de pagination a été atteinte : le décompte est un minorant."""

    @property
    def total(self) -> int:
        """Nombre de commits relevés dans la fenêtre."""
        return len(self.commits)
