"""Collecte des issues d'un repository.

GitHub renvoie les pull requests dans la même collection que les issues : elles
s'en distinguent par la présence d'une clé ``pull_request``. Githor stocke les
issues et se contente de compter les pull requests ouvertes — confondre les deux
fausserait toute mesure de la charge de maintenance.
"""

from collections.abc import Sequence
from typing import Any

from githor.github.client import GitHubClient
from githor.github.errors import EmptyRepositoryError, NotFoundError
from githor.github.repositories import MAX_ISSUE_PAGES, list_issues
from githor.logging import get_logger
from githor.models.issue import Issue, IssueCollection
from githor.utils.dates import parse_datetime

logger = get_logger("collectors.issues")

PAGE_SIZE = 100
ISSUE_CEILING = MAX_ISSUE_PAGES * PAGE_SIZE
"""Nombre d'issues au-delà duquel la collecte est considérée comme tronquée."""


def is_pull_request(payload: dict[str, Any]) -> bool:
    """Dit si une entrée de la collection ``issues`` est en réalité une pull request."""
    return "pull_request" in payload


def normalise_issue(payload: dict[str, Any]) -> Issue | None:
    """Traduit une issue GitHub en modèle normalisé.

    Returns:
        ``None`` si la charge utile est inexploitable : une issue illisible ne
        doit pas interrompre la collecte de tout un dépôt.
    """
    github_id = payload.get("id")
    number = payload.get("number")
    if not isinstance(github_id, int) or not isinstance(number, int):
        return None

    def date(field: str) -> Any:
        try:
            return parse_datetime(payload.get(field))
        except ValueError:
            return None

    title = payload.get("title")
    state = payload.get("state")
    return Issue(
        github_id=github_id,
        number=number,
        title=title if isinstance(title, str) else None,
        state=state if isinstance(state, str) else "open",
        created_at=date("created_at"),
        updated_at=date("updated_at"),
        closed_at=date("closed_at"),
    )


def sort_out(payloads: Sequence[dict[str, Any]]) -> IssueCollection:
    """Sépare les issues des pull requests dans une collection brute."""
    issues: list[Issue] = []
    open_pull_requests = 0
    ignored = 0

    for payload in payloads:
        if not isinstance(payload, dict):
            ignored += 1
            continue
        if is_pull_request(payload):
            open_pull_requests += int(payload.get("state") == "open")
            continue
        issue = normalise_issue(payload)
        if issue is None:
            ignored += 1
            continue
        issues.append(issue)

    if ignored:
        logger.warning("%s entrée(s) illisible(s) ignorée(s) dans les issues.", ignored)

    return IssueCollection(
        issues=tuple(issues),
        open_pull_requests=open_pull_requests,
        truncated=len(payloads) >= ISSUE_CEILING,
    )


def collect_issues(client: GitHubClient, full_name: str) -> IssueCollection:
    """Récupère les issues d'un repository et en écarte les pull requests.

    Un dépôt dont le suivi d'issues est désactivé produit une collection vide :
    ce n'est pas une erreur.
    """
    try:
        payloads = list(list_issues(client, full_name))
    except (EmptyRepositoryError, NotFoundError) as exc:
        logger.debug("Issues indisponibles pour %s : %s", full_name, exc)
        return IssueCollection()

    collection = sort_out(payloads)
    if collection.truncated:
        logger.warning(
            "Issues de %s tronquées à %s : les décomptes sont des minorants.",
            full_name,
            ISSUE_CEILING,
        )
    return collection
