"""Écriture et lecture des repositories et de leurs snapshots.

Deux gestes distincts, et c'est le cœur du modèle :

- le **repository** est mis à jour : c'est le projet, il n'a qu'un état courant ;
- le **snapshot** est ajouté : c'est une mesure datée, jamais réécrite.
"""

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from githor.logging import get_logger
from githor.models.activity import Commit
from githor.models.issue import Issue
from githor.models.release import Release
from githor.models.repository import Repository
from githor.models.snapshot import Language, RepositoryFile, RepositorySnapshot
from githor.storage.tables import (
    CommitRow,
    IssueRow,
    LanguageRow,
    ReleaseRow,
    RepositoryFileRow,
    RepositoryRow,
    RepositorySnapshotRow,
)

logger = get_logger("storage.repositories")


def find_repository(session: Session, github_id: int) -> RepositoryRow | None:
    """Retrouve un repository par son identifiant GitHub.

    L'identifiant GitHub, et non le nom, sert de clé : c'est la seule identité
    qui survit à un renommage du dépôt.
    """
    return session.scalar(select(RepositoryRow).where(RepositoryRow.github_id == github_id))


def list_stored_repositories(session: Session) -> list[RepositoryRow]:
    """Retourne les repositories connus de la base, ordonnés par nom complet."""
    return list(session.scalars(select(RepositoryRow).order_by(RepositoryRow.full_name)).all())


def find_repository_by_name(session: Session, name: str) -> RepositoryRow | None:
    """Retrouve un repository par son nom complet ou par son seul nom court.

    La comparaison ignore la casse : ``architecturor`` retrouve
    ``nouhailler/Architecturor``. Un nom court ambigu — le même dépôt chez deux
    propriétaires — renvoie la première correspondance par ordre alphabétique,
    l'utilisateur pouvant toujours lever le doute en donnant le nom complet.
    """
    lowered = name.strip().lower()
    column = RepositoryRow.full_name if "/" in lowered else RepositoryRow.name
    return session.scalar(
        select(RepositoryRow)
        .where(func.lower(column) == lowered)
        .order_by(RepositoryRow.full_name)
        .limit(1)
    )


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


def first_snapshot(session: Session, repository_id: int) -> RepositorySnapshotRow | None:
    """Retourne le snapshot le plus ancien d'un repository, s'il en existe un.

    Avec :func:`latest_snapshot`, il borne l'historique conservé : un rapport
    peut dire depuis quand un dépôt est suivi.
    """
    return session.scalar(
        select(RepositorySnapshotRow)
        .where(RepositorySnapshotRow.repository_id == repository_id)
        .order_by(RepositorySnapshotRow.collected_at)
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


def save_languages(session: Session, snapshot_id: int, languages: Sequence[Language]) -> int:
    """Enregistre la répartition des langages d'un snapshot.

    Returns:
        Le nombre de langages enregistrés.
    """
    session.add_all(
        LanguageRow(
            snapshot_id=snapshot_id,
            language=language.language,
            bytes=language.bytes,
            percentage=language.percentage,
        )
        for language in languages
    )
    session.flush()
    return len(languages)


def save_files(session: Session, snapshot_id: int, files: Sequence[RepositoryFile]) -> int:
    """Enregistre l'arborescence relevée pour un snapshot.

    Returns:
        Le nombre d'entrées enregistrées.
    """
    session.add_all(
        RepositoryFileRow(
            snapshot_id=snapshot_id,
            path=entry.path,
            type=entry.type,
            size=entry.size,
        )
        for entry in files
    )
    session.flush()
    return len(files)


def save_commits(session: Session, repository_id: int, commits: Sequence[Commit]) -> int:
    """Enregistre les commits encore inconnus d'un repository.

    Un commit est un fait daté : il n'est jamais réécrit, seulement ajouté s'il
    manque. Les scans successifs peuvent donc se recouvrir sans produire de
    doublon.

    Returns:
        Le nombre de commits réellement ajoutés.
    """
    if not commits:
        return 0

    known = set(
        session.scalars(
            select(CommitRow.sha).where(
                CommitRow.repository_id == repository_id,
                CommitRow.sha.in_([commit.sha for commit in commits]),
            )
        ).all()
    )

    added = [commit for commit in commits if commit.sha not in known]
    session.add_all(
        CommitRow(
            repository_id=repository_id,
            sha=commit.sha,
            author=commit.author,
            message=commit.message,
            committed_at=commit.committed_at,
        )
        for commit in added
    )
    session.flush()
    return len(added)


def save_releases(session: Session, repository_id: int, releases: Sequence[Release]) -> int:
    """Crée ou met à jour les releases d'un repository.

    Une release n'est pas figée : un brouillon finit par être publié, un nom se
    corrige. Elle est donc mise à jour, et non accumulée — le tag l'identifie.

    Returns:
        Le nombre de releases créées ou mises à jour.
    """
    if not releases:
        return 0

    known = {
        row.tag: row
        for row in session.scalars(
            select(ReleaseRow).where(
                ReleaseRow.repository_id == repository_id,
                ReleaseRow.tag.in_([release.tag for release in releases]),
            )
        ).all()
    }

    for release in releases:
        row = known.get(release.tag)
        if row is None:
            row = ReleaseRow(repository_id=repository_id, tag=release.tag)
            session.add(row)
        row.name = release.name
        row.published_at = release.published_at
        row.draft = release.draft
        row.prerelease = release.prerelease

    session.flush()
    return len(releases)


def save_issues(session: Session, repository_id: int, issues: Sequence[Issue]) -> int:
    """Crée ou met à jour les issues d'un repository.

    Une issue change d'état : elle se ferme, se rouvre, son titre se corrige.
    C'est le numéro, stable dans le dépôt, qui l'identifie d'un scan à l'autre.

    Returns:
        Le nombre d'issues créées ou mises à jour.
    """
    if not issues:
        return 0

    known = {
        row.number: row
        for row in session.scalars(
            select(IssueRow).where(
                IssueRow.repository_id == repository_id,
                IssueRow.number.in_([issue.number for issue in issues]),
            )
        ).all()
    }

    for issue in issues:
        row = known.get(issue.number)
        if row is None:
            row = IssueRow(repository_id=repository_id, number=issue.number)
            session.add(row)
        row.github_id = issue.github_id
        row.title = issue.title
        row.state = issue.state
        row.created_at = issue.created_at
        row.updated_at = issue.updated_at
        row.closed_at = issue.closed_at

    session.flush()
    return len(issues)
