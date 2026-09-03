"""Constitution du jeu de données exporté, à partir de la seule base SQLite.

Aucun appel réseau : un export décrit ce que le dernier scan a mesuré, pas
l'état de GitHub à l'instant où on l'exécute. C'est ce qui rend un export
reproductible et comparable à un autre.

Ce module porte aussi la première couche de **métriques** : elles sont dérivées
des tables, jamais stockées en double.
"""

from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from githor import __version__
from githor.logging import get_logger
from githor.storage.findings import latest_findings
from githor.storage.repositories import latest_snapshot, list_stored_repositories
from githor.storage.tables import (
    CommitRow,
    IssueRow,
    LanguageRow,
    ReleaseRow,
    RepositoryFileRow,
    RepositoryRow,
    RepositorySnapshotRow,
)
from githor.utils.dates import utc_now

logger = get_logger("exporters.dataset")

_EXPORT = ConfigDict(extra="forbid", frozen=True)


class LanguageExport(BaseModel):
    """Part d'un langage dans le dernier snapshot."""

    model_config = _EXPORT

    language: str
    bytes: int
    percentage: float


class FindingExport(BaseModel):
    """Constat du dernier snapshot."""

    model_config = _EXPORT

    category: str
    rule: str
    severity: str
    status: str
    message: str
    recommendation: str | None = None


class ReleaseExport(BaseModel):
    """Release connue du repository."""

    model_config = _EXPORT

    tag: str
    name: str | None = None
    published_at: datetime | None = None
    draft: bool = False
    prerelease: bool = False


class SnapshotExport(BaseModel):
    """État mesuré lors du dernier scan."""

    model_config = _EXPORT

    collected_at: datetime
    stars: int
    forks: int
    watchers: int
    open_issues: int
    """Compteur GitHub : il inclut les pull requests."""

    open_prs: int | None = None
    size_kb: int
    primary_language: str | None = None
    default_branch: str | None = None


class Metrics(BaseModel):
    """Métriques dérivées des tables, jamais stockées en double."""

    model_config = _EXPORT

    files: int = 0
    directories: int = 0
    languages: int = 0

    commits_30_days: int = 0
    commits_90_days: int = 0
    """Comptés depuis la date du snapshot, afin que l'export reste reproductible."""

    last_commit_at: datetime | None = None

    releases: int = 0
    open_issues: int = 0
    closed_issues: int = 0
    """Issues réelles : les pull requests en sont exclues."""

    findings_open: int = 0
    findings_high: int = 0
    findings_medium: int = 0
    findings_low: int = 0


class RepositoryExport(BaseModel):
    """Un repository, son dernier état mesuré et ce qui s'y rattache."""

    model_config = _EXPORT

    full_name: str
    name: str
    owner: str
    description: str | None = None
    url: str
    visibility: str
    default_branch: str
    fork: bool = False
    archived: bool = False

    created_at: datetime | None = None
    updated_at: datetime | None = None
    pushed_at: datetime | None = None

    snapshot: SnapshotExport | None = None
    metrics: Metrics = Metrics()
    languages: tuple[LanguageExport, ...] = ()
    findings: tuple[FindingExport, ...] = ()
    releases: tuple[ReleaseExport, ...] = ()

    @property
    def open_findings(self) -> tuple[FindingExport, ...]:
        """Constats ouverts, dans l'ordre où ils ont été lus."""
        return tuple(finding for finding in self.findings if finding.status == "open")


class Dataset(BaseModel):
    """Jeu de données complet d'un export."""

    model_config = _EXPORT

    githor_version: str
    generated_at: datetime
    repository_count: int
    repositories: tuple[RepositoryExport, ...] = ()

    @property
    def findings_open(self) -> int:
        """Total des constats ouverts, tous dépôts confondus."""
        return sum(repository.metrics.findings_open for repository in self.repositories)


def build_dataset(session: Session, *, generated_at: datetime | None = None) -> Dataset:
    """Construit le jeu de données à partir de la base.

    Args:
        session: session ouverte sur la base à exporter.
        generated_at: date de génération ; maintenant par défaut.
    """
    repositories = tuple(
        _build_repository(session, row) for row in list_stored_repositories(session)
    )
    logger.debug("Jeu de données construit : %s repository(s).", len(repositories))
    return Dataset(
        githor_version=__version__,
        generated_at=generated_at or utc_now(),
        repository_count=len(repositories),
        repositories=repositories,
    )


def _build_repository(session: Session, row: RepositoryRow) -> RepositoryExport:
    """Rassemble tout ce qui décrit un repository dans l'export."""
    snapshot = latest_snapshot(session, row.id)
    findings = tuple(
        FindingExport(
            category=finding.category,
            rule=finding.rule,
            severity=finding.severity,
            status=finding.status,
            message=finding.message,
            recommendation=finding.recommendation,
        )
        for finding in latest_findings(session, row.id)
    )
    languages = _languages(session, snapshot)
    releases = _releases(session, row.id)

    return RepositoryExport(
        full_name=row.full_name,
        name=row.name,
        owner=row.owner,
        description=row.description,
        url=row.url,
        visibility=row.visibility,
        default_branch=row.default_branch,
        fork=row.fork,
        archived=row.archived,
        created_at=row.created_at,
        updated_at=row.updated_at,
        pushed_at=row.pushed_at,
        snapshot=_snapshot(snapshot),
        metrics=_metrics(session, row.id, snapshot, languages, releases, findings),
        languages=languages,
        findings=findings,
        releases=releases,
    )


def _snapshot(row: RepositorySnapshotRow | None) -> SnapshotExport | None:
    """Traduit le dernier snapshot, s'il existe."""
    if row is None:
        return None
    return SnapshotExport(
        collected_at=row.collected_at,
        stars=row.stars,
        forks=row.forks,
        watchers=row.watchers,
        open_issues=row.open_issues,
        open_prs=row.open_prs,
        size_kb=row.size_kb,
        primary_language=row.primary_language,
        default_branch=row.default_branch,
    )


def _languages(
    session: Session, snapshot: RepositorySnapshotRow | None
) -> tuple[LanguageExport, ...]:
    """Langages du dernier snapshot, du plus lourd au plus léger."""
    if snapshot is None:
        return ()
    rows = session.scalars(
        select(LanguageRow)
        .where(LanguageRow.snapshot_id == snapshot.id)
        .order_by(LanguageRow.bytes.desc(), LanguageRow.language)
    ).all()
    return tuple(
        LanguageExport(language=row.language, bytes=row.bytes, percentage=row.percentage)
        for row in rows
    )


def _releases(session: Session, repository_id: int) -> tuple[ReleaseExport, ...]:
    """Releases du repository, de la plus récente à la plus ancienne."""
    rows = session.scalars(
        select(ReleaseRow)
        .where(ReleaseRow.repository_id == repository_id)
        .order_by(ReleaseRow.published_at.desc().nulls_last(), ReleaseRow.tag)
    ).all()
    return tuple(
        ReleaseExport(
            tag=row.tag,
            name=row.name,
            published_at=row.published_at,
            draft=row.draft,
            prerelease=row.prerelease,
        )
        for row in rows
    )


def _metrics(
    session: Session,
    repository_id: int,
    snapshot: RepositorySnapshotRow | None,
    languages: tuple[LanguageExport, ...],
    releases: tuple[ReleaseExport, ...],
    findings: tuple[FindingExport, ...],
) -> Metrics:
    """Calcule les métriques dérivées d'un repository."""
    opened = [finding for finding in findings if finding.status == "open"]
    reference = snapshot.collected_at if snapshot else utc_now()

    return Metrics(
        files=_count_files(session, snapshot, "blob"),
        directories=_count_files(session, snapshot, "tree"),
        languages=len(languages),
        commits_30_days=_count_commits(session, repository_id, reference, days=30),
        commits_90_days=_count_commits(session, repository_id, reference, days=90),
        last_commit_at=session.scalar(
            select(func.max(CommitRow.committed_at)).where(CommitRow.repository_id == repository_id)
        ),
        releases=len(releases),
        open_issues=_count_issues(session, repository_id, "open"),
        closed_issues=_count_issues(session, repository_id, "closed"),
        findings_open=len(opened),
        findings_high=sum(1 for finding in opened if finding.severity == "high"),
        findings_medium=sum(1 for finding in opened if finding.severity == "medium"),
        findings_low=sum(1 for finding in opened if finding.severity == "low"),
    )


def _count_files(session: Session, snapshot: RepositorySnapshotRow | None, kind: str) -> int:
    """Compte les entrées d'arborescence d'un type donné dans le dernier snapshot."""
    if snapshot is None:
        return 0
    total = session.scalar(
        select(func.count())
        .select_from(RepositoryFileRow)
        .where(RepositoryFileRow.snapshot_id == snapshot.id, RepositoryFileRow.type == kind)
    )
    return total or 0


def _count_commits(session: Session, repository_id: int, reference: datetime, *, days: int) -> int:
    """Compte les commits des ``days`` jours précédant la date de référence."""
    total = session.scalar(
        select(func.count())
        .select_from(CommitRow)
        .where(
            CommitRow.repository_id == repository_id,
            CommitRow.committed_at >= reference - timedelta(days=days),
        )
    )
    return total or 0


def _count_issues(session: Session, repository_id: int, state: str) -> int:
    """Compte les issues d'un état donné."""
    total = session.scalar(
        select(func.count())
        .select_from(IssueRow)
        .where(IssueRow.repository_id == repository_id, IssueRow.state == state)
    )
    return total or 0
