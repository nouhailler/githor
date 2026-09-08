"""Orchestration du conseiller IA : priorisation, prompt, génération (V0.4).

**Githor priorise, Ollama rédige.** L'ordre des recommandations est calculé
ici, déterministe, depuis les constats déjà ouverts — jamais laissé à
l'appréciation du modèle. Ollama ne fait que rédiger un titre et une
recommandation pour chaque constat, dans l'ordre donné : rien de ce qu'il
écrit ne peut porter sur un fait qui ne soit pas déjà dans la liste.

Comme :mod:`githor.analysis.audit`, ce module ne connaît ni la base de
données ni la CLI : il reçoit un contexte déjà collecté et rend un résultat.
Les constats et le dépôt sont acceptés sous la forme la plus large possible
(:class:`Protocol`) afin d'accepter aussi bien les objets ORM lus en base que
les modèles normalisés — ce module ne choisit pas leur provenance.
"""

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from githor.logging import get_logger
from githor.models.advice import Advice, AdviceItem
from githor.models.finding import SEVERITY_ORDER, Severity
from githor.scoring import Score
from githor.storage.code import AuditMetrics
from githor.utils.dates import utc_now

logger = get_logger("analysis.advisor")


class _Finding(Protocol):
    """Ce que la priorisation a besoin de lire d'un constat, stocké ou exporté."""

    rule: str
    severity: str
    status: str
    message: str


class _Repository(Protocol):
    """Ce que le prompt a besoin de lire d'un repository."""

    full_name: str
    description: str | None


class _Generator(Protocol):
    """Ce que la génération a besoin d'un client Ollama — pas plus.

    Découple ce module de :mod:`githor.ollama` : un faux générateur suffit à
    tester ``generate_advice`` sans réseau ni serveur Ollama.
    """

    def generate(self, prompt: str, *, model: str) -> str: ...


def rank_open_findings(findings: Sequence[_Finding]) -> list[_Finding]:
    """Trie les constats ouverts du plus grave au moins grave.

    C'est **toute** la priorisation : aucune règle nouvelle, la même
    hiérarchie de gravité que ``reports/markdown.py`` et ``exporters/``.
    """
    opened = [finding for finding in findings if finding.status == "open"]

    def key(finding: _Finding) -> tuple[int, str]:
        try:
            position = SEVERITY_ORDER.index(Severity(finding.severity))
        except ValueError:
            position = len(SEVERITY_ORDER)
        return (position, finding.rule)

    return sorted(opened, key=key)


def build_prompt(
    repository: _Repository,
    ranked: Sequence[_Finding],
    *,
    score: Score | None,
    code: AuditMetrics | None,
) -> str:
    """Construit un prompt déterministe, qui cite chaque constat littéralement.

    Le score et les métriques de code ne sont donnés qu'en **contexte** : ils
    aident à mieux rédiger les recommandations, mais n'en créent aucune de
    plus. Seuls les constats listés donnent lieu à une recommandation.
    """
    lines = [f"Dépôt : {repository.full_name}"]
    if repository.description:
        lines.append(f"Description : {repository.description}")

    if score is not None:
        lines.append(
            "Score actuel sur 100, « - » si non évalué : "
            f"docs={_cell(score.docs)} tests={_cell(score.tests)} ci={_cell(score.ci)} "
            f"security={_cell(score.security)} overall={_cell(score.overall)}"
        )

    if code is not None:
        complexity = "-" if code.average_complexity is None else str(code.average_complexity)
        lines.append(
            f"Code analysé : {code.lines.code} lignes, complexité moyenne {complexity}, "
            f"{code.tests.functions} fonction(s) de test, "
            f"{len(code.dependencies)} dépendance(s) déclarée(s)."
        )

    lines += [
        "",
        "Voici les constats ouverts de ce dépôt, du plus grave au moins grave :",
    ]
    lines += [
        f"{index}. [{finding.rule}] ({finding.severity}) {finding.message}"
        for index, finding in enumerate(ranked, start=1)
    ]

    lines += [
        "",
        "Pour CHAQUE constat ci-dessus, dans le MÊME ORDRE, rédige un titre court "
        "et une recommandation concrète en français. Ne mentionne aucun fait qui "
        "ne soit pas dans cette liste ou dans le contexte donné plus haut.",
        "Réponds uniquement avec un tableau JSON valide, sans texte autour, de la "
        'forme [{"title": "...", "recommendation": "..."}, ...], avec exactement '
        f"{len(ranked)} élément(s), dans l'ordre donné.",
    ]
    return "\n".join(lines)


def generate_advice(
    client: _Generator,
    *,
    repository: _Repository,
    findings: Sequence[_Finding],
    score: Score | None = None,
    code: AuditMetrics | None = None,
    model: str,
    generated_at: datetime | None = None,
) -> Advice | None:
    """Génère les recommandations d'un dépôt.

    Returns:
        ``None`` si le dépôt n'a aucun constat ouvert : il n'y a rien à
        recommander, un ``Advice`` vide serait un chiffre inventé.
    """
    ranked = rank_open_findings(findings)
    if not ranked:
        return None

    prompt = build_prompt(repository, ranked, score=score, code=code)
    raw = client.generate(prompt, model=model)
    moment = generated_at or utc_now()

    items = _parse(raw, ranked)
    if items is not None:
        return Advice(generated_at=moment, model=model, items=items)

    logger.warning(
        "Réponse Ollama non structurée pour %s : conservée telle quelle, sans association.",
        repository.full_name,
    )
    return Advice(
        generated_at=moment,
        model=model,
        items=(AdviceItem(rank=1, source_rule=None, title="Recommandations", recommendation=raw),),
        degraded=True,
    )


def _parse(raw: str, ranked: Sequence[_Finding]) -> tuple[AdviceItem, ...] | None:
    """Associe la réponse d'Ollama aux constats, un à un — ou ``None`` si impossible."""
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(payload, list) or len(payload) != len(ranked):
        return None

    items: list[AdviceItem] = []
    for rank, (entry, finding) in enumerate(zip(payload, ranked, strict=True), start=1):
        if not isinstance(entry, dict):
            return None
        title = entry.get("title")
        recommendation = entry.get("recommendation")
        if not isinstance(title, str) or not isinstance(recommendation, str):
            return None
        items.append(
            AdviceItem(
                rank=rank, source_rule=finding.rule, title=title, recommendation=recommendation
            )
        )
    return tuple(items)


def _cell(value: int | None) -> str:
    """Une case vide plutôt qu'un zéro trompeur, comme dans ``githor.scoring``."""
    return "-" if value is None else str(value)
