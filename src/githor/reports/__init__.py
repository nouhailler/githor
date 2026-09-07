"""Rapports Markdown individuels : un dépôt, une page.

Là où l'export décrit le parc entier, un rapport répond à une seule question :
*où en est ce projet-là ?* Il est construit depuis la **seule base SQLite**, au
même titre qu'un export : il ne joint pas GitHub, et ne dépend donc ni du
réseau ni du quota.

Le rapport décrit le **dernier snapshot** enregistré, et le dit. Il n'invente
rien : chaque ligne se retrouve dans les tables qui l'ont produite.
"""

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from githor import __version__
from githor.errors import StorageError
from githor.exporters.dataset import RepositoryExport, build_repository_export
from githor.logging import get_logger
from githor.reports.markdown import render_report
from githor.scoring import ScoreHistoryEntry, compute_score
from githor.storage.code import AuditMetrics, audit_metrics, count_audits, latest_audit
from githor.storage.findings import findings_for_snapshot
from githor.storage.repositories import count_snapshots, first_snapshot, list_snapshots
from githor.storage.tables import RepositoryRow
from githor.utils.dates import utc_now

logger = get_logger("reports")

FILE_PREFIX = "githor-report"
EXTENSION = ".md"


class Report(BaseModel):
    """Tout ce qu'un rapport individuel donne à lire d'un repository."""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    githor_version: str
    generated_at: datetime

    repository: RepositoryExport
    """Le dépôt et son dernier état mesuré, décrits comme dans un export."""

    snapshots: int = 0
    """Nombre de mesures conservées : depuis quand ce dépôt est suivi."""

    first_snapshot_at: datetime | None = None

    code: AuditMetrics | None = None
    """Dernier audit de code, ``None`` si le dépôt n'a jamais été analysé.

    L'audit est **indépendant du snapshot** : il se lit sur un clone local et
    peut donc manquer là où un snapshot existe, ou l'inverse. Le rapport dit
    l'un ou l'autre selon ce qui est enregistré, sans jamais en supposer un.
    """

    audits: int = 0
    """Nombre d'audits de code conservés."""

    score_history: tuple[ScoreHistoryEntry, ...] = ()
    """Score à chaque snapshot connu, du plus ancien au plus récent.

    Dérivé des findings de chaque snapshot — voir :mod:`githor.scoring`. Le
    dernier de la liste correspond à ``repository.score``.
    """


def build_report(
    session: Session, row: RepositoryRow, *, generated_at: datetime | None = None
) -> Report:
    """Construit le rapport d'un repository à partir de la base.

    Args:
        session: session ouverte sur la base à lire.
        row: ligne du repository, résolue par la CLI.
        generated_at: date de génération ; maintenant par défaut.
    """
    oldest = first_snapshot(session, row.id)
    audit = latest_audit(session, row.id)
    report = Report(
        githor_version=__version__,
        generated_at=generated_at or utc_now(),
        repository=build_repository_export(session, row),
        snapshots=count_snapshots(session, row.id),
        first_snapshot_at=oldest.collected_at if oldest else None,
        code=audit_metrics(session, audit) if audit is not None else None,
        audits=count_audits(session, row.id),
        score_history=_score_history(session, row.id),
    )
    logger.debug("Rapport construit : %s (%s snapshot(s)).", row.full_name, report.snapshots)
    return report


def _score_history(session: Session, repository_id: int) -> tuple[ScoreHistoryEntry, ...]:
    """Recalcule le score de chaque snapshot connu, depuis ses findings.

    Rien n'est stocké : l'historique existe déjà dans les findings persistés
    à chaque scan, il suffit de les relire snapshot par snapshot.
    """
    return tuple(
        ScoreHistoryEntry(
            collected_at=snapshot.collected_at,
            score=compute_score(findings_for_snapshot(session, snapshot.id)),
        )
        for snapshot in list_snapshots(session, repository_id)
    )


def report_filename(full_name: str, *, moment: datetime | None = None) -> str:
    """Compose un nom de fichier horodaté pour le rapport d'un dépôt.

    Le nom complet est aplati — ``nouhailler/Architecturor`` devient
    ``nouhailler-Architecturor`` — afin qu'il ne désigne jamais un
    sous-répertoire qui n'existe pas.
    """
    stamp = (moment or utc_now()).strftime("%Y%m%d-%H%M%S")
    slug = full_name.strip().replace("/", "-")
    return f"{FILE_PREFIX}-{slug}-{stamp}{EXTENSION}"


def write_report(report: Report, destination: Path) -> Path:
    """Écrit le rapport et retourne le fichier produit.

    Args:
        report: rapport à rendre.
        destination: fichier à écrire, ou répertoire **existant** dans lequel
            un fichier horodaté est créé. Un répertoire déjà là est le cas
            courant (``-o data/exports``) ; ailleurs, l'utilisateur nomme son
            fichier et Githor le prend au mot.

    Raises:
        StorageError: si le fichier ou son répertoire est inaccessible.
    """
    path = (
        destination / report_filename(report.repository.full_name, moment=report.generated_at)
        if destination.is_dir()
        else destination
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_report(report), encoding="utf-8")
    except OSError as exc:
        raise StorageError(f"Rapport impossible à écrire dans {path} : {exc.strerror}") from exc

    logger.debug("Rapport écrit : %s", path)
    return path


__all__ = [
    "EXTENSION",
    "Report",
    "build_report",
    "render_report",
    "report_filename",
    "write_report",
]
