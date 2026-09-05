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
from githor.models.code import DependencyScope
from githor.models.finding import SEVERITY_ORDER, Status
from githor.rules.catalog import CATEGORIES, CATEGORY_LABELS, rule_labels
from githor.utils.markdown import ABSENT, escape_cell, format_moment, severity_label

if TYPE_CHECKING:  # pragma: no cover — importé seulement pour le typage
    from githor.reports import Report
    from githor.storage.code import AuditMetrics

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
        # L'audit ne dépend pas du snapshot : s'il existe, il a sa place ici.
        lines += _code(report)
        return "\n".join(lines).rstrip() + "\n"

    lines += _overview(repository)
    lines += _languages(repository)
    lines += _code(report)
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


def _code(report: "Report") -> list[str]:
    """Ce que l'analyse locale a lu du code, si elle a eu lieu.

    Cette section est tirée de l'audit et non du snapshot : les deux sont des
    mesures distinctes, prises par des chemins différents, et l'une peut exister
    sans l'autre.
    """
    code = report.code
    if code is None:
        return [
            "## Code",
            "",
            f"Aucune analyse de code enregistrée : lancez `githor audit "
            f"{escape_cell(report.repository.name)}` pour en produire une.",
            "",
        ]

    lines = [
        "## Code",
        "",
        f"*D'après l'audit du {format_moment(code.analysed_at)}, "
        f"commit `{escape_cell(code.short_commit)}` sur `{escape_cell(code.branch)}`.*",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Files analysed | {code.files_analysed} |",
        f"| Lines of code | {code.lines.code} |",
        f"| Comment lines | {code.lines.comment} |",
        f"| Blank lines | {code.lines.blank} |",
        f"| Comment ratio | "
        f"{ABSENT if code.lines.comment_ratio is None else f'{code.lines.comment_ratio} %'} |",
        f"| Functions | {code.function_count} |",
        f"| Classes | {code.class_count} |",
        f"| Average complexity | {code.average_complexity or ABSENT} |",
        f"| Max complexity | {code.max_complexity or ABSENT} |",
        f"| Test files | {code.tests.files} |",
        f"| Test functions | {code.tests.functions} |",
        f"| Dependencies | {len(code.dependencies)} |",
        "",
    ]

    if code.files_binary or code.files_too_large or code.parse_errors:
        skipped = []
        if code.files_binary:
            skipped.append(f"{code.files_binary} binaire(s)")
        if code.files_too_large:
            skipped.append(f"{code.files_too_large} trop volumineux")
        if code.parse_errors:
            skipped.append(f"{code.parse_errors} non analysable(s)")
        lines += [f"Fichiers écartés : {', '.join(skipped)}.", ""]

    lines += _code_languages(code)
    lines += _most_complex(code)
    lines += _dependencies(code)
    return lines


def _code_languages(code: "AuditMetrics") -> list[str]:
    """Répartition mesurée sur le code présent, artefacts écartés.

    À distinguer de la section « Langages », qui reprend le calcul de GitHub :
    les deux peuvent diverger, et c'est l'intérêt de les donner toutes les deux.
    """
    if not code.languages:
        return []

    lines = [
        "### Langages du code analysé",
        "",
        "| Language | Files | Code | Comment |",
        "|---|---:|---:|---:|",
    ]
    lines += [
        f"| {escape_cell(item.language)} | {item.files} | "
        f"{item.lines.code} | {item.lines.comment} |"
        for item in code.languages
    ]
    return [*lines, ""]


def _most_complex(code: "AuditMetrics") -> list[str]:
    """Fonctions les plus complexes, citées avec leur fichier et leur ligne."""
    if not code.most_complex or code.max_complexity <= 1:
        return []

    lines = [
        "### Fonctions les plus complexes",
        "",
        "| Complexity | Function | File |",
        "|---:|---|---|",
    ]
    lines += [
        f"| {item.complexity} | `{escape_cell(item.name)}` | "
        f"`{escape_cell(item.path)}`:{item.line} |"
        for item in code.most_complex
    ]
    return [*lines, ""]


def _dependencies(code: "AuditMetrics") -> list[str]:
    """Dépendances déclarées, avec le manifeste qui les déclare."""
    if not code.dependencies:
        return []

    labels = {
        DependencyScope.RUNTIME: "exécution",
        DependencyScope.DEVELOPMENT: "développement",
        DependencyScope.OPTIONAL: "optionnelle",
    }

    lines = [
        "### Dépendances déclarées",
        "",
        "| Package | Constraint | Scope | Ecosystem | Declared in |",
        "|---|---|---|---|---|",
    ]
    lines += [
        f"| {escape_cell(item.name)} | {escape_cell(item.specifier or ABSENT)} | "
        f"{labels[item.scope]} | {escape_cell(item.ecosystem)} | "
        f"`{escape_cell(item.source)}` |"
        for item in code.dependencies
    ]
    return [*lines, ""]


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
