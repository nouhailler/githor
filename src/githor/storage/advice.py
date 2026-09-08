"""Écriture et lecture des recommandations du conseiller IA (V0.4).

Un appel à ``githor advise`` **ajoute** une entrée, comme un scan ajoute un
snapshot ou un audit s'ajoute : deux exécutions successives se comparent,
elles ne se remplacent pas. Contrairement au score (V0.3), le texte produit
par Ollama n'est pas dérivable des findings — il coûte un appel au modèle et
n'est pas reproductible à l'identique — c'est donc lui, et non un calcul, qui
est écrit ici.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from githor.logging import get_logger
from githor.models.advice import Advice
from githor.storage.tables import AdviceItemRow, AdviceRunRow

logger = get_logger("storage.advice")


def save_advice(session: Session, repository_id: int, advice: Advice) -> AdviceRunRow:
    """Enregistre une exécution du conseiller et ses recommandations.

    Args:
        session: session ouverte.
        repository_id: identifiant interne du repository conseillé.
        advice: résultat produit par :func:`githor.analysis.advisor.generate_advice`.

    Returns:
        La ligne d'exécution créée.
    """
    row = AdviceRunRow(
        repository_id=repository_id,
        generated_at=advice.generated_at,
        model=advice.model,
        degraded=advice.degraded,
    )
    session.add(row)
    session.flush()

    session.add_all(
        AdviceItemRow(
            run_id=row.id,
            rank=item.rank,
            source_rule=item.source_rule,
            title=item.title,
            recommendation=item.recommendation,
        )
        for item in advice.items
    )

    session.flush()
    logger.debug(
        "Recommandations enregistrées : repository %s, %s élément(s), modèle %s.",
        repository_id,
        len(advice.items),
        advice.model,
    )
    return row


def latest_advice(session: Session, repository_id: int) -> AdviceRunRow | None:
    """Retourne la dernière exécution du conseiller pour un repository, ou ``None``."""
    return session.scalar(
        select(AdviceRunRow)
        .where(AdviceRunRow.repository_id == repository_id)
        .order_by(AdviceRunRow.generated_at.desc(), AdviceRunRow.id.desc())
        .limit(1)
    )


def count_advice_runs(session: Session, repository_id: int) -> int:
    """Nombre d'exécutions du conseiller conservées pour un repository."""
    total = session.scalar(
        select(func.count())
        .select_from(AdviceRunRow)
        .where(AdviceRunRow.repository_id == repository_id)
    )
    return int(total or 0)
