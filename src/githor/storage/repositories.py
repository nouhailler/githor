"""Écriture et lecture des repositories et de leurs snapshots.

Deux gestes distincts, et c'est le cœur du modèle :

- le **repository** est mis à jour : c'est le projet, il n'a qu'un état courant ;
- le **snapshot** est ajouté : c'est une mesure datée, jamais réécrite.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from githor.logging import get_logger
from githor.models.repository import Repository
from githor.models.snapshot import RepositorySnapshot
from githor.storage.tables import RepositoryRow, RepositorySnapshotRow

logger = get_logger("storage.repositories")


def find_repository(session: Session, github_id: int) -> RepositoryRow | None:
    """Retrouve un repository par son identifiant GitHub.

    L'identifiant GitHub, et non le nom, sert de clé : c'est la seule identité
    qui survit à un renommage du dépôt.
    """
    return session.scalar(select(RepositoryRow).where(RepositoryRow.github_id == github_id))


def upsert_repository(session: Session, repository: Repository) -> tuple[RepositoryRow, bool]:
    """Crée ou met à jour la ligne d'un repository.

    Args:
        session: session ouverte.
        repository: modèle normalisé issu de la collecte.

    Returns:
        La ligne persistée, et ``True`` si elle vient d'être créée.
    """
    row = find_repository(session, repository.github_id)
    created = row is None

    if row is None:
        row = RepositoryRow(github_id=repository.github_id)
        session.add(row)

    if not created and row.full_name != repository.full_name:
        logger.debug("Repository renommé : %s -> %s", row.full_name, repository.full_name)

    row.full_name = repository.full_name
    row.name = repository.name
    row.owner = repository.owner
    row.description = repository.description
    row.url = repository.html_url
    row.default_branch = repository.default_branch
    row.visibility = repository.visibility
    row.created_at = repository.created_at
    row.updated_at = repository.updated_at
    row.pushed_at = repository.pushed_at
    row.archived = repository.archived
    row.fork = repository.fork

    session.flush()
    return row, created


def add_snapshot(
    session: Session, repository_id: int, snapshot: RepositorySnapshot
) -> RepositorySnapshotRow:
    """Ajoute un snapshot. Les snapshots précédents ne sont jamais touchés."""
    row = RepositorySnapshotRow(
        repository_id=repository_id,
        collected_at=snapshot.collected_at,
        stars=snapshot.stars,
        forks=snapshot.forks,
        watchers=snapshot.watchers,
        open_issues=snapshot.open_issues,
        open_prs=snapshot.open_prs,
        size_kb=snapshot.size_kb,
        primary_language=snapshot.primary_language,
        default_branch=snapshot.default_branch,
    )
    session.add(row)
    session.flush()
    return row


def latest_snapshot(session: Session, repository_id: int) -> RepositorySnapshotRow | None:
    """Retourne le snapshot le plus récent d'un repository, s'il en existe un.

    Permet de savoir si une mesure récente existe déjà avant d'en refaire une.
    """
    return session.scalar(
        select(RepositorySnapshotRow)
        .where(RepositorySnapshotRow.repository_id == repository_id)
        .order_by(RepositorySnapshotRow.collected_at.desc())
        .limit(1)
    )


def count_snapshots(session: Session, repository_id: int) -> int:
    """Compte les snapshots conservés pour un repository."""
    total = session.scalar(
        select(func.count())
        .select_from(RepositorySnapshotRow)
        .where(RepositorySnapshotRow.repository_id == repository_id)
    )
    return total or 0
