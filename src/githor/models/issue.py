"""Modèles normalisés des issues GitHub.

GitHub range les pull requests parmi les issues. Githor les sépare : une issue
est stockée, une pull request est seulement comptée. Confondre les deux
fausserait toute mesure ultérieure de la charge de maintenance.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class Issue(BaseModel):
    """Issue GitHub, ouverte ou fermée."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    github_id: int
    number: int
    title: str | None = None
    state: str = "open"

    created_at: datetime | None = None
    updated_at: datetime | None = None
    closed_at: datetime | None = None

    @property
    def is_open(self) -> bool:
        """Vrai si l'issue est encore ouverte."""
        return self.state == "open"


class IssueCollection(BaseModel):
    """Issues d'un repository, une fois les pull requests écartées."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    issues: tuple[Issue, ...] = ()

    open_pull_requests: int = 0
    """Pull requests ouvertes, comptées mais non stockées."""

    truncated: bool = False
    """Vrai si la borne de pagination a été atteinte : les décomptes minorent."""

    @property
    def open_issues(self) -> int:
        """Nombre d'issues ouvertes, pull requests exclues."""
        return sum(1 for issue in self.issues if issue.is_open)

    @property
    def closed_issues(self) -> int:
        """Nombre d'issues fermées."""
        return len(self.issues) - self.open_issues
