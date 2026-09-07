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
    "score_docs",
    "score_tests",
    "score_ci",
    "score_security",
    "score_overall",
    "code_analysed",
    "code_commit",
    "code_files",
    "code_lines",
    "code_comment_ratio",
    "code_language",
    "code_functions",
    "code_classes",
    "code_complexity_avg",
    "code_complexity_max",
    "code_test_files",
    "code_test_functions",
    "code_dependencies",
    "url",
)
"""Colonnes du CSV, dans l'ordre : identité, mesure, activité, constats, code.

Les colonnes ``code_*`` sont vides pour un dépôt jamais analysé localement — une
case vide et non un zéro, qui laisserait croire à une mesure faite.
"""


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
        *_score_columns(repository),
        *_code_columns(repository),
        repository.url,
    ]


def _score_columns(repository: RepositoryExport) -> list[str]:
    """Colonnes de score, vides pour un dépôt jamais scanné — pas des zéros."""
    score = repository.score
    if score is None:
        return [""] * 5

    return [
        _percentage(score.docs),
        _percentage(score.tests),
        _percentage(score.ci),
        _percentage(score.security),
        _percentage(score.overall),
    ]


def _percentage(value: int | None) -> str:
    """Une case vide pour un groupe sans règle évaluée, jamais un zéro trompeur."""
    return "" if value is None else str(value)


def _code_columns(repository: RepositoryExport) -> list[str]:
    """Colonnes issues de l'analyse locale, vides si elle n'a pas eu lieu."""
    code = repository.code
    if code is None:
        return [""] * 13

    return [
        isoformat(code.analysed_at),
        code.commit[:7],
        str(code.files_analysed),
        str(code.lines_code),
        "" if code.comment_ratio is None else str(code.comment_ratio),
        code.primary_language or "",
        str(code.functions),
        str(code.classes),
        "" if code.average_complexity is None else str(code.average_complexity),
        str(code.max_complexity),
        str(code.test_files),
        str(code.test_functions),
        str(code.dependencies),
    ]


def _boolean(value: bool) -> str:
    """Écrit un booléen en minuscules, lisible par un tableur comme par un script."""
    return "true" if value else "false"
