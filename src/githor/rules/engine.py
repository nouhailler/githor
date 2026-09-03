"""Évaluation du catalogue de règles sur un repository."""

from collections.abc import Sequence

from githor.logging import get_logger
from githor.models.finding import Finding
from githor.rules.base import Rule, RuleContext
from githor.rules.catalog import RULES

logger = get_logger("rules")


def evaluate(context: RuleContext, rules: Sequence[Rule] = RULES) -> list[Finding]:
    """Applique les règles à un repository et retourne un constat par règle.

    Chaque règle produit exactement un constat, satisfait ou ouvert : la table
    ``findings`` conserve ainsi, pour chaque snapshot, l'état complet de ce qui
    a été vérifié — et pas seulement ce qui manquait.

    Args:
        context: données déjà collectées pour le repository.
        rules: catalogue à appliquer ; celui du V0.1 par défaut.
    """
    findings = [rule.evaluate(context) for rule in rules]
    opened = sum(1 for finding in findings if finding.is_open)
    logger.debug(
        "%s : %s règle(s) évaluée(s), %s constat(s) ouvert(s).",
        context.repository.full_name,
        len(findings),
        opened,
    )
    return findings


def open_findings(findings: Sequence[Finding]) -> list[Finding]:
    """Ne retient que les constats ouverts."""
    return [finding for finding in findings if finding.is_open]
