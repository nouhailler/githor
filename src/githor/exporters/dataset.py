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
from githor.models.code import DependencyScope
from githor.storage.code import audit_metrics, latest_audit
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


class CodeExport(BaseModel):
    """Ce que la dernière analyse locale a lu du code d'un repository.

    Volontairement plat : l'export décrit un parc, et doit rester lisible dans
    un tableur. Le détail — chaque fonction, chaque dépendance — appartient au
    rapport individuel, qui décrit un seul dépôt.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    analysed_at: datetime
    commit: str
    branch: str

    files_analysed: int = 0
    files_binary: int = 0
    files_too_large: int = 0
    parse_errors: int = 0

    lines_total: int = 0
    lines_code: int = 0
    lines_comment: int = 0
    lines_blank: int = 0
    comment_ratio: float | None = None

    primary_language: str | None = None
    """Langage portant le plus de lignes de code sur disque.

    Peut différer du ``primary_language`` du snapshot, que GitHub calcule sur
    l'ensemble du dépôt : l'écart entre les deux est précisément instructif.
    """

    functions: int = 0
    classes: int = 0
    average_complexity: float | None = None
    max_complexity: int = 0

    test_files: int = 0
    test_functions: int = 0
    test_frameworks: tuple[str, ...] = ()

    dependencies: int = 0
    dependencies_runtime: int = 0
    dependencies_development: int = 0


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

    code: CodeExport | None = None
    """Dernière analyse locale, ``None`` si le dépôt n'a jamais été analysé."""

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
        build_repository_export(session, row) for row in list_stored_repositories(session)
    )
    logger.debug("Jeu de données construit : %s repository(s).", len(repositories))
    return Dataset(
        githor_version=__version__,
        generated_at=generated_at or utc_now(),
        repository_count=len(repositories),
        repositories=repositories,
    )


def build_repository_export(session: Session, row: RepositoryRow) -> RepositoryExport:
    """Rassemble tout ce qui décrit un repository dans l'export.

    Publique : le rapport individuel (``githor report``) décrit un seul dépôt,
    et doit le décrire exactement comme l'export décrit chacun des siens.
    """
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
        code=_code(session, row.id),
    )


def _code(session: Session, repository_id: int) -> CodeExport | None:
    """Résume la dernière analyse locale, si le dépôt en a une.

    Les chiffres viennent de :func:`githor.storage.code.audit_metrics`, qui les
    recalcule depuis les tables : export et rapport ne peuvent donc pas donner
    deux valeurs différentes du même dépôt.
    """
    audit = latest_audit(session, repository_id)
    if audit is None:
        return None

    metrics = audit_metrics(session, audit, complex_limit=0)
    return CodeExport(
        analysed_at=metrics.analysed_at,
        commit=metrics.commit,
        branch=metrics.branch,
        files_analysed=metrics.files_analysed,
        files_binary=metrics.files_binary,
        files_too_large=metrics.files_too_large,
        parse_errors=metrics.parse_errors,
        lines_total=metrics.lines.total,
        lines_code=metrics.lines.code,
        lines_comment=metrics.lines.comment,
        lines_blank=metrics.lines.blank,
        comment_ratio=metrics.lines.comment_ratio,
        primary_language=metrics.languages[0].language if metrics.languages else None,
        functions=metrics.function_count,
        classes=metrics.class_count,
        average_complexity=metrics.average_complexity,
        max_complexity=metrics.max_complexity,
        test_files=metrics.tests.files,
        test_functions=metrics.tests.functions,
        test_frameworks=metrics.tests.frameworks,
        dependencies=len(metrics.dependencies),
        dependencies_runtime=len(metrics.dependencies_in_scope(DependencyScope.RUNTIME)),
        dependencies_development=len(metrics.dependencies_in_scope(DependencyScope.DEVELOPMENT)),
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
