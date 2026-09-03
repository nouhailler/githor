"""Rendu Markdown d'un rapport individuel.

Le document suit l'ordre dans lequel on interroge un projet qu'on redécouvre :
ce qu'il est, de quoi il est fait, ce qu'on y a vérifié, ce qui lui manque, et
depuis quand on l'observe.

Les vérifications sont rendues à partir des **constats enregistrés**, jamais du
catalogue courant : un rapport tiré d'un snapshot ancien montre ce qui avait été
vérifié ce jour-là, et non ce que Githor saurait vérifier aujourd'hui.
"""

from typing import TYPE_CHECKING

from githor.exporters.dataset import FindingExport, ReleaseExport, RepositoryExport
from githor.models.finding import SEVERITY_ORDER, Status
from githor.rules.catalog import CATEGORIES, CATEGORY_LABELS, rule_labels
from githor.utils.markdown import ABSENT, escape_cell, format_moment, severity_label

if TYPE_CHECKING:  # pragma: no cover — importé seulement pour le typage
    from githor.reports import Report

PRESENT_MARK = "✓"
MISSING_MARK = "✗"


def render_report(report: "Report") -> str:
    """Rend le rapport d'un repository sous forme de document Markdown."""
    repository = report.repository

    lines: list[str] = [f"# {escape_cell(repository.full_name)}", ""]
    if repository.description:
        lines += [escape_cell(repository.description), ""]
    lines += _preamble(report)

    if repository.snapshot is None:
        lines += [
            "Aucun snapshot enregistré pour ce dépôt : "
            f"lancez `githor scan {repository.name}` pour en produire un.",
            "",
        ]
        return "\n".join(lines).rstrip() + "\n"

    lines += _overview(repository)
    lines += _languages(repository)
    lines += _checks(repository)
    lines += _releases(repository)
    lines += _findings(repository)
    lines += _history(report)
    return "\n".join(lines).rstrip() + "\n"


def _preamble(report: "Report") -> list[str]:
    """Rappelle d'où viennent les chiffres : un snapshot daté, pas l'état de GitHub."""
    repository = report.repository
    snapshot = repository.snapshot

    origin = (
        f"d'après le snapshot du {format_moment(snapshot.collected_at)}"
        if snapshot
        else "aucun snapshot enregistré"
    )
    attributes = [repository.visibility]
    if repository.fork:
        attributes.append("fork")
    if repository.archived:
        attributes.append("archivé")
    attributes.append(f"branche `{escape_cell(repository.default_branch)}`")

    return [
        f"*Rapport Githor {report.githor_version} — "
        f"généré le {format_moment(report.generated_at)}, {origin}.*",
        "",
        f"<{repository.url}> — {' · '.join(attributes)}",
        "",
    ]


def _overview(repository: RepositoryExport) -> list[str]:
    """Mesures principales du dernier snapshot, dans le format du cahier des charges."""
    snapshot = repository.snapshot
    if snapshot is None:  # pragma: no cover — écarté par render_report
        return []
    metrics = repository.metrics
    # GitHub compte les pull requests parmi ses issues ouvertes ; Githor les
    # sépare, mais un snapshot ancien peut ne pas les avoir distinguées.
    open_prs = ABSENT if snapshot.open_prs is None else str(snapshot.open_prs)

    return [
        "## Vue d'ensemble",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Files | {metrics.files} |",
        f"| Directories | {metrics.directories} |",
        f"| Languages | {metrics.languages} |",
        f"| Size (kB) | {snapshot.size_kb} |",
        f"| Stars | {snapshot.stars} |",
        f"| Forks | {snapshot.forks} |",
        f"| Watchers | {snapshot.watchers} |",
        f"| Open issues | {metrics.open_issues} |",
        f"| Closed issues | {metrics.closed_issues} |",
        f"| Open pull requests | {open_prs} |",
        f"| Releases | {metrics.releases} |",
        f"| Commits (30 d) | {metrics.commits_30_days} |",
        f"| Commits (90 d) | {metrics.commits_90_days} |",
        f"| Last commit | {format_moment(metrics.last_commit_at) or ABSENT} |",
        f"| Last push | {format_moment(repository.pushed_at) or ABSENT} |",
        f"| Created | {format_moment(repository.created_at) or ABSENT} |",
        "",
    ]


def _languages(repository: RepositoryExport) -> list[str]:
    """Répartition des langages, du plus lourd au plus léger."""
    lines = ["## Langages", ""]
    if not repository.languages:
        return [*lines, "Aucun langage relevé par GitHub sur ce dépôt.", ""]

    lines += [
        f"- {escape_cell(language.language)} — {language.percentage:.1f} % ({language.bytes} o)"
        for language in repository.languages
    ]
    lines.append("")
    return lines


def _checks(repository: RepositoryExport) -> list[str]:
    """Une section par catégorie : ce qui est là, ce qui manque."""
    grouped: dict[str, list[FindingExport]] = {category: [] for category in CATEGORIES}
    for finding in repository.findings:
        grouped.setdefault(finding.category, []).append(finding)

    labels = rule_labels()
    # Le catalogue va du plus attendu au plus accessoire : les constats, eux,
    # reviennent triés par identifiant. On rétablit l'ordre du catalogue.
    positions = {identifier: rank for rank, identifier in enumerate(labels)}

    lines: list[str] = []
    for category, findings in grouped.items():
        if not findings:
            continue
        findings.sort(key=lambda finding: positions.get(finding.rule, len(positions)))
        lines += [
            f"## {CATEGORY_LABELS.get(category, category)}",
            "",
            "| Item | Status |",
            "|---|---|",
        ]
        lines += [
            f"| {escape_cell(labels.get(finding.rule, finding.rule))} "
            f"| {PRESENT_MARK if finding.status == Status.OK else MISSING_MARK} |"
            for finding in findings
        ]
        lines.append("")

    if not lines:
        return [
            "## Vérifications",
            "",
            "Aucun constat enregistré pour ce snapshot.",
            "",
        ]
    return lines


def _releases(repository: RepositoryExport) -> list[str]:
    """Releases connues, de la plus récente à la plus ancienne."""
    if not repository.releases:
        return []

    lines = [
        "## Releases",
        "",
        "| Tag | Nom | Publiée le | État |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| `{escape_cell(release.tag)}` "
        f"| {escape_cell(release.name) or ABSENT} "
        f"| {format_moment(release.published_at) or ABSENT} "
        f"| {_release_state(release)} |"
        for release in repository.releases
    ]
    lines.append("")
    return lines


def _release_state(release: ReleaseExport) -> str:
    """Qualifie une release : brouillon, préversion, ou publiée."""
    if release.draft:
        return "brouillon"
    if release.prerelease:
        return "préversion"
    return "publiée"


def _findings(repository: RepositoryExport) -> list[str]:
    """Constats ouverts, groupés par gravité, avec la recommandation associée."""
    opened = repository.open_findings
    if not opened:
        return ["## Constats", "", "Aucun constat ouvert.", ""]

    # Une gravité inconnue — base écrite par une version ultérieure — est rendue
    # après les autres plutôt que passée sous silence.
    known = tuple(str(severity) for severity in SEVERITY_ORDER)
    unknown = sorted({finding.severity for finding in opened} - set(known))

    lines = ["## Constats", ""]
    for severity in (*known, *unknown):
        group = [finding for finding in opened if finding.severity == severity]
        if not group:
            continue
        lines += [f"### {severity_label(severity)}", ""]
        for finding in group:
            recommendation = (
                f" — *{escape_cell(finding.recommendation)}*" if finding.recommendation else ""
            )
            lines.append(f"- {escape_cell(finding.message)} (`{finding.rule}`){recommendation}")
        lines.append("")
    return lines


def _history(report: "Report") -> list[str]:
    """Rappelle depuis quand le dépôt est suivi : un rapport n'est qu'une date parmi d'autres."""
    if report.snapshots <= 1:
        return ["## Historique", "", "Premier snapshot : aucun historique à comparer.", ""]

    return [
        "## Historique",
        "",
        f"{report.snapshots} snapshots conservés, "
        f"du {format_moment(report.first_snapshot_at)} à aujourd'hui. "
        "Les mesures précédentes restent en base : elles ne sont jamais écrasées.",
        "",
    ]
