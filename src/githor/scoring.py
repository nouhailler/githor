"""Score dérivé des findings, jamais stocké (étape 20, V0.3).

Un score n'est pas une mesure de plus : c'est une lecture des findings déjà
persistés. Comme le reste des métriques de Githor, il se recalcule à chaque
lecture — depuis les findings d'un snapshot pour un score ponctuel, depuis
ceux de plusieurs snapshots pour un historique. Rien n'est ajouté au schéma :
l'historique des scores existe déjà, pour peu qu'on relise les findings passés.

Chaque catégorie d'un tableau de comparaison (Docs, Tests, CI, Security) est un
sous-ensemble de règles du catalogue ; son score est le pourcentage de règles
satisfaites parmi celles qui ont été évaluées. Une catégorie dont aucune règle
n'apparaît dans les findings fournis vaut ``None`` — une valeur absente vaut
mieux qu'un chiffre faux, au même titre que les décomptes à 90 jours qui
restent ``None`` quand la fenêtre n'est pas couverte.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict

SCORE_GROUPS: dict[str, tuple[str, ...]] = {
    "docs": (
        "documentation.readme",
        "documentation.license",
        "documentation.changelog",
        "documentation.contributing",
        "documentation.docs",
    ),
    "tests": ("development.tests",),
    "ci": ("development.github_actions",),
    "security": ("security.dependabot", "security.policy"),
}
"""Regroupement des règles du catalogue en colonnes de score.

Distinct de :data:`githor.rules.catalog.CATEGORIES` : une catégorie de règles
range les findings pour l'affichage détaillé, un groupe de score range les
règles pour une comparaison entre projets. Les deux taxonomies n'ont pas à
coïncider — ``maintenance`` et ``infrastructure.docker`` par exemple ne
contribuent à aucune colonne affichée, mais restent comptés dans ``overall``.
"""


class _StatusedFinding(Protocol):
    """Ce qu'un score a besoin de lire d'un finding, stocké ou exporté."""

    rule: str
    status: str


class Score(BaseModel):
    """Score d'un repository à un instant donné, par groupe et global.

    Chaque champ est un pourcentage entier de règles satisfaites, ou ``None``
    si aucune règle du groupe n'a été évaluée pour ce repository.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    docs: int | None = None
    tests: int | None = None
    ci: int | None = None
    security: int | None = None
    overall: int | None = None
    """Sur la totalité des findings, tous groupes confondus — pas la moyenne des colonnes."""


class ScoreHistoryEntry(BaseModel):
    """Score d'un repository à la date d'un snapshot passé."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    collected_at: datetime
    score: Score | None


def compute_score(findings: Sequence[_StatusedFinding]) -> Score | None:
    """Calcule le score d'un repository à partir de ses findings.

    Args:
        findings: findings d'un snapshot (ou du dernier connu). ``None`` est
            renvoyé si la séquence est vide : le repository n'a jamais été
            scanné, un chiffre à zéro serait faux.
    """
    if not findings:
        return None

    return Score(
        docs=_group_score(findings, SCORE_GROUPS["docs"]),
        tests=_group_score(findings, SCORE_GROUPS["tests"]),
        ci=_group_score(findings, SCORE_GROUPS["ci"]),
        security=_group_score(findings, SCORE_GROUPS["security"]),
        overall=_percentage(findings),
    )


def _group_score(findings: Sequence[_StatusedFinding], rules: tuple[str, ...]) -> int | None:
    """Pourcentage de règles satisfaites parmi celles d'un groupe, ou ``None``."""
    matching = [finding for finding in findings if finding.rule in rules]
    return _percentage(matching)


def _percentage(findings: Sequence[_StatusedFinding]) -> int | None:
    """Pourcentage de findings satisfaits (statut ``ok``), ou ``None`` si aucun."""
    if not findings:
        return None
    satisfied = sum(1 for finding in findings if finding.status == "ok")
    return round(100 * satisfied / len(findings))
