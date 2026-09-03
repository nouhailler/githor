"""Export Markdown : un inventaire lisible par un humain.

Là où le JSON conserve tout et le CSV se trie, le Markdown se lit. Il répond à
trois questions dans l'ordre : de quoi mon parc est-il fait, que manque-t-il le
plus souvent, et que manque-t-il à chaque projet.
"""

from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime

from githor.exporters.dataset import Dataset, RepositoryExport
from githor.models.finding import SEVERITY_LABELS, SEVERITY_ORDER, Severity


def render_markdown(dataset: Dataset) -> str:
    """Rend le jeu de données sous forme de document Markdown."""
    lines: list[str] = [
        "# Inventaire Githor",
        "",
        f"*Généré le {_moment(dataset.generated_at)} par Githor {dataset.githor_version} — "
        f"{dataset.repository_count} repository(s), {dataset.findings_open} constat(s) ouvert(s).*",
        "",
        "Les mesures proviennent du dernier snapshot de chaque dépôt, "
        "et non de l'état de GitHub à l'instant de l'export.",
        "",
    ]
    lines += _overview(dataset.repositories)
    lines += _recurring_findings(dataset.repositories)
    lines += _details(dataset.repositories)
    return "\n".join(lines).rstrip() + "\n"


def _overview(repositories: Iterable[RepositoryExport]) -> list[str]:
    """Tableau récapitulatif de tous les dépôts."""
    lines = [
        "## Vue d'ensemble",
        "",
        "| Repository | Langage | ★ | Fichiers | Commits 90j | Issues | Constats |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for repository in repositories:
        snapshot = repository.snapshot
        metrics = repository.metrics
        lines.append(
            f"| [{_escape(repository.full_name)}]({repository.url}) "
            f"| {_escape(snapshot.primary_language if snapshot else None) or '—'} "
            f"| {snapshot.stars if snapshot else 0} "
            f"| {metrics.files} "
            f"| {metrics.commits_90_days} "
            f"| {metrics.open_issues} "
            f"| {metrics.findings_open} |"
        )
    lines.append("")
    return lines


def _recurring_findings(repositories: Iterable[RepositoryExport]) -> list[str]:
    """Constats les plus fréquents : par quoi commencer si l'on veut agir une fois pour toutes."""
    counter: Counter[tuple[str, str]] = Counter()
    for repository in repositories:
        for finding in repository.open_findings:
            counter[(finding.rule, finding.severity)] += 1

    if not counter:
        return ["## Ce qui manque le plus souvent", "", "Aucun constat ouvert.", ""]

    def rank(item: tuple[tuple[str, str], int]) -> tuple[int, int, str]:
        (rule, severity), count = item
        order = (
            SEVERITY_ORDER.index(Severity(severity))
            if severity in Severity
            else len(SEVERITY_ORDER)
        )
        return (-count, order, rule)

    lines = [
        "## Ce qui manque le plus souvent",
        "",
        "| Règle | Gravité | Dépôts concernés |",
        "|---|---|---:|",
    ]
    for (rule, severity), count in sorted(counter.items(), key=rank):
        lines.append(f"| `{rule}` | {_severity_label(severity)} | {count} |")
    lines.append("")
    return lines


def _details(repositories: Iterable[RepositoryExport]) -> list[str]:
    """Une section par repository : mesures, langages, constats ouverts."""
    lines = ["## Détail par repository", ""]
    for repository in repositories:
        lines += _repository_section(repository)
    return lines


def _repository_section(repository: RepositoryExport) -> list[str]:
    """Section d'un repository."""
    snapshot = repository.snapshot
    metrics = repository.metrics

    lines = [f"### {_escape(repository.full_name)}", ""]
    if repository.description:
        lines += [_escape(repository.description), ""]

    if snapshot is None:
        lines += ["Aucun snapshot enregistré.", ""]
        return lines

    lines += [
        "| Metric | Value |",
        "|---|---:|",
        f"| Files | {metrics.files} |",
        f"| Directories | {metrics.directories} |",
        f"| Languages | {metrics.languages} |",
        f"| Size (kB) | {snapshot.size_kb} |",
        f"| Stars | {snapshot.stars} |",
        f"| Forks | {snapshot.forks} |",
        f"| Open issues | {metrics.open_issues} |",
        f"| Closed issues | {metrics.closed_issues} |",
        f"| Releases | {metrics.releases} |",
        f"| Commits (30 d) | {metrics.commits_30_days} |",
        f"| Commits (90 d) | {metrics.commits_90_days} |",
        f"| Last commit | {_moment(metrics.last_commit_at) or '—'} |",
        f"| Snapshot | {_moment(snapshot.collected_at)} |",
        "",
    ]

    if repository.languages:
        lines.append(
            "**Langages** — "
            + ", ".join(
                f"{_escape(language.language)} {language.percentage:.1f} %"
                for language in repository.languages
            )
        )
        lines.append("")

    lines += _open_findings(repository)
    return lines


def _open_findings(repository: RepositoryExport) -> list[str]:
    """Constats ouverts d'un repository, groupés par gravité."""
    opened = repository.open_findings
    if not opened:
        return ["**Aucun constat ouvert.**", ""]

    lines = [f"**Constats ouverts ({len(opened)})**", ""]
    for severity in SEVERITY_ORDER:
        group = [finding for finding in opened if finding.severity == severity]
        for finding in group:
            recommendation = (
                f" — *{_escape(finding.recommendation)}*" if finding.recommendation else ""
            )
            lines.append(
                f"- **{SEVERITY_LABELS[severity]}** · {_escape(finding.message)}"
                f" (`{finding.rule}`){recommendation}"
            )
    lines.append("")
    return lines


def _moment(moment: datetime | None) -> str:
    """Formate une date pour un lecteur humain, à la minute près."""
    if moment is None:
        return ""
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def _severity_label(severity: str) -> str:
    """Traduit une gravité en libellé lisible, sans supposer qu'elle soit connue."""
    if severity in Severity:
        return SEVERITY_LABELS[Severity(severity)]
    return severity


def _escape(text: str | None) -> str:
    """Neutralise ce qui casserait un tableau Markdown : barres verticales et retours ligne."""
    if not text:
        return ""
    return text.replace("|", "\\|").replace("\n", " ").strip()
