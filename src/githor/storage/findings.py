"""Écriture et lecture des constats.

Un finding est rattaché à un snapshot : il décrit ce qui a été constaté à une
date, jamais « l'état actuel » du dépôt. Relire les constats d'un snapshot
ancien reste donc possible, et deux snapshots successifs racontent l'évolution.
"""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from githor.logging import get_logger
from githor.models.finding import Finding
from githor.storage.repositories import latest_snapshot
from githor.storage.tables import FindingRow

logger = get_logger("storage.findings")


def save_findings(
    session: Session, repository_id: int, snapshot_id: int, findings: Sequence[Finding]
) -> int:
    """Enregistre les constats d'un snapshot.

    Args:
        session: session ouverte.
        repository_id: repository concerné.
        snapshot_id: snapshot qui a motivé les constats.
        findings: constats produits par le moteur de règles.

    Returns:
        Le nombre de constats enregistrés.
    """
    session.add_all(
        FindingRow(
            repository_id=repository_id,
            snapshot_id=snapshot_id,
            category=finding.category,
            rule=finding.rule,
            severity=str(finding.severity),
            status=str(finding.status),
            message=finding.message,
            recommendation=finding.recommendation,
        )
        for finding in findings
    )
    session.flush()
    return len(findings)


def findings_for_snapshot(session: Session, snapshot_id: int) -> list[FindingRow]:
    """Retourne les constats d'un snapshot, ordonnés par catégorie puis par règle."""
    return list(
        session.scalars(
            select(FindingRow)
            .where(FindingRow.snapshot_id == snapshot_id)
            .order_by(FindingRow.category, FindingRow.rule)
        ).all()
    )


def latest_findings(session: Session, repository_id: int) -> list[FindingRow]:
    """Retourne les constats du snapshot le plus récent d'un repository.

    Returns:
        Une liste vide si le repository n'a encore aucun snapshot.
    """
    snapshot = latest_snapshot(session, repository_id)
    if snapshot is None:
        return []
    return findings_for_snapshot(session, snapshot.id)
