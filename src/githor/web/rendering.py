"""Prépare les données d'un rapport pour les gabarits Jinja2.

Aucun calcul propre à cette couche : elle groupe et étiquette ce que
``RepositoryExport`` fournit déjà, sur le même principe que
``reports/markdown.py`` — les gabarits ne connaissent aucune règle métier, et
ce module ne connaît ni Flask ni les gabarits qui l'utilisent.
"""

from dataclasses import dataclass
from datetime import datetime

from markupsafe import Markup, escape

from githor.exporters.dataset import FindingExport, RepositoryExport
from githor.models.finding import SEVERITY_ORDER
from githor.rules.catalog import CATEGORIES, CATEGORY_LABELS, rule_labels
from githor.utils.markdown import format_moment, severity_label

HIGH_THRESHOLD = 70
MEDIUM_THRESHOLD = 40


@dataclass(frozen=True)
class CheckItem:
    """Une règle vérifiée, prête à l'affichage."""

    label: str
    satisfied: bool


@dataclass(frozen=True)
class CheckGroup:
    """Les règles d'une catégorie, dans l'ordre du catalogue."""

    label: str
    items: tuple[CheckItem, ...]


def checks_by_category(repository: RepositoryExport) -> list[CheckGroup]:
    """Groupe les constats par catégorie — ce qui est là, ce qui manque.

    Même tri que ``reports/markdown.py`` : le catalogue va du plus attendu au
    plus accessoire, les constats reviennent triés par identifiant, on
    rétablit l'ordre du catalogue.
    """
    grouped: dict[str, list[FindingExport]] = {category: [] for category in CATEGORIES}
    for finding in repository.findings:
        grouped.setdefault(finding.category, []).append(finding)

    labels = rule_labels()
    positions = {identifier: rank for rank, identifier in enumerate(labels)}

    groups: list[CheckGroup] = []
    for category, findings in grouped.items():
        if not findings:
            continue
        findings.sort(key=lambda finding: positions.get(finding.rule, len(positions)))
        items = tuple(
            CheckItem(
                label=labels.get(finding.rule, finding.rule), satisfied=finding.status == "ok"
            )
            for finding in findings
        )
        groups.append(CheckGroup(label=CATEGORY_LABELS.get(category, category), items=items))
    return groups


def open_findings_by_severity(
    repository: RepositoryExport,
) -> list[tuple[str, tuple[FindingExport, ...]]]:
    """Constats ouverts groupés par gravité, du plus grave au moins grave."""
    opened = repository.open_findings
    known = tuple(str(severity) for severity in SEVERITY_ORDER)
    unknown = sorted({finding.severity for finding in opened} - set(known))

    groups: list[tuple[str, tuple[FindingExport, ...]]] = []
    for severity in (*known, *unknown):
        group = tuple(finding for finding in opened if finding.severity == severity)
        if group:
            groups.append((severity_label(severity), group))
    return groups


def moment(value: datetime | None) -> str:
    """Une date lisible à la minute près, en UTC — un tiret plutôt qu'un vide muet."""
    return format_moment(value) or "—"


def score_cell(value: int | None) -> str:
    """Une case vide plutôt qu'un zéro trompeur, pour un groupe jamais évalué."""
    return "—" if value is None else f"{value} %"


def score_badge(value: int | None) -> Markup:
    """Le score global, mis en valeur par une pastille colorée.

    Réservé à la colonne Score : décorer aussi Docs/Tests/CI/Security
    surchargerait un tableau déjà dense.
    """
    if value is None:
        return Markup('<span class="score score-none">—</span>')
    if value >= HIGH_THRESHOLD:
        band = "high"
    elif value >= MEDIUM_THRESHOLD:
        band = "medium"
    else:
        band = "low"
    return Markup(f'<span class="score score-{band}">{escape(value)} %</span>')
