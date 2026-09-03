"""Export CSV : une ligne par repository.

Le CSV ne sait pas imbriquer : les langages, les constats et les releases y
deviennent des décomptes. C'est le format des tableurs et des tris rapides ;
le JSON reste la source complète.
"""

import csv
import io

from githor.exporters.dataset import Dataset, RepositoryExport
from githor.utils.dates import isoformat

COLUMNS: tuple[str, ...] = (
    "repository",
    "owner",
    "visibility",
    "archived",
    "fork",
    "snapshot",
    "primary_language",
    "languages",
    "stars",
    "forks",
    "watchers",
    "open_issues_github",
    "open_prs",
    "issues_open",
    "issues_closed",
    "releases",
    "size_kb",
    "files",
    "directories",
    "commits_30_days",
    "commits_90_days",
    "last_commit",
    "pushed_at",
    "findings_open",
    "findings_high",
    "findings_medium",
    "findings_low",
    "url",
)
"""Colonnes du CSV, dans l'ordre : identité, mesure, activité, constats."""


def render_csv(dataset: Dataset) -> str:
    """Sérialise le jeu de données en CSV, une ligne par repository."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)
    for repository in dataset.repositories:
        writer.writerow(_row(repository))
    return buffer.getvalue()


def _row(repository: RepositoryExport) -> list[str]:
    """Aplatit un repository en une ligne de CSV."""
    snapshot = repository.snapshot
    metrics = repository.metrics
    return [
        repository.full_name,
        repository.owner,
        repository.visibility,
        _boolean(repository.archived),
        _boolean(repository.fork),
        isoformat(snapshot.collected_at) if snapshot else "",
        (snapshot.primary_language or "") if snapshot else "",
        str(metrics.languages),
        str(snapshot.stars) if snapshot else "",
        str(snapshot.forks) if snapshot else "",
        str(snapshot.watchers) if snapshot else "",
        str(snapshot.open_issues) if snapshot else "",
        str(snapshot.open_prs) if snapshot and snapshot.open_prs is not None else "",
        str(metrics.open_issues),
        str(metrics.closed_issues),
        str(metrics.releases),
        str(snapshot.size_kb) if snapshot else "",
        str(metrics.files),
        str(metrics.directories),
        str(metrics.commits_30_days),
        str(metrics.commits_90_days),
        isoformat(metrics.last_commit_at),
        isoformat(repository.pushed_at),
        str(metrics.findings_open),
        str(metrics.findings_high),
        str(metrics.findings_medium),
        str(metrics.findings_low),
        repository.url,
    ]


def _boolean(value: bool) -> str:
    """Écrit un booléen en minuscules, lisible par un tableur comme par un script."""
    return "true" if value else "false"
