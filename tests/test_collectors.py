"""Tests des collectors (étape 6) : normalisation, filtrage, dates.

Les réponses GitHub sont mockées : aucun appel réseau, aucun jeton réel.
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError
from pytest_httpx import HTTPXMock

from githor.collectors.repositories import (
    collect_repositories,
    keep_repository,
    normalise_repository,
)
from githor.config import ScanConfig
from githor.github.client import DEFAULT_API_URL, GitHubClient
from githor.github.errors import InvalidResponseError
from githor.models.repository import Repository
from githor.utils.dates import parse_datetime

FULL_PAYLOAD: dict[str, Any] = {
    "id": 1296269,
    "name": "Architecturor",
    "full_name": "nouhailler/Architecturor",
    "owner": {"login": "nouhailler", "id": 42},
    "description": "Un projet d'architecture",
    "html_url": "https://github.com/nouhailler/Architecturor",
    "clone_url": "https://github.com/nouhailler/Architecturor.git",
    "ssh_url": "git@github.com:nouhailler/Architecturor.git",
    "visibility": "public",
    "private": False,
    "default_branch": "main",
    "created_at": "2026-01-15T10:30:00Z",
    "updated_at": "2026-08-24T18:05:12Z",
    "pushed_at": "2026-08-24T17:59:00Z",
    "size": 4096,
    "language": "TypeScript",
    "fork": False,
    "archived": False,
    "disabled": False,
    "has_issues": True,
    "has_projects": False,
    "has_wiki": True,
    "has_pages": True,
    "has_discussions": False,
    "open_issues_count": 4,
    "stargazers_count": 12,
    "forks_count": 3,
    "watchers_count": 12,
    "license": {"key": "mit", "spdx_id": "MIT", "name": "MIT License"},
    "topics": ["architecture", "pwa"],
}

MINIMAL_PAYLOAD: dict[str, Any] = {
    "id": 7,
    "name": "minimal",
    "full_name": "nouhailler/minimal",
    "owner": {"login": "nouhailler"},
    "html_url": "https://github.com/nouhailler/minimal",
}


def payload(**overrides: Any) -> dict[str, Any]:
    """Construit une charge utile complète, éventuellement modifiée."""
    return {**FULL_PAYLOAD, **overrides}


# ── Normalisation ────────────────────────────────────────────────────────────


def test_full_payload_is_fully_normalised() -> None:
    repository = normalise_repository(FULL_PAYLOAD)

    assert repository.github_id == 1296269
    assert repository.name == "Architecturor"
    assert repository.full_name == "nouhailler/Architecturor"
    assert repository.owner == "nouhailler"
    assert repository.description == "Un projet d'architecture"
    assert repository.clone_url.endswith(".git")
    assert repository.ssh_url.startswith("git@")
    assert repository.visibility == "public"
    assert repository.default_branch == "main"
    assert repository.size_kb == 4096
    assert repository.language == "TypeScript"
    assert repository.open_issues_count == 4
    assert repository.stars == 12
    assert repository.forks == 3
    assert repository.watchers == 12
    assert repository.license == "MIT"
    assert repository.topics == ("architecture", "pwa")
    assert repository.has_issues is True
    assert repository.has_projects is False


def test_github_identifier_is_preserved() -> None:
    """L'identifiant GitHub survit à un renommage : il ne doit jamais être perdu."""
    renamed = normalise_repository(payload(name="Nouveau", full_name="nouhailler/Nouveau"))

    assert renamed.github_id == FULL_PAYLOAD["id"]


def test_minimal_payload_falls_back_on_defaults() -> None:
    repository = normalise_repository(MINIMAL_PAYLOAD)

    assert repository.description is None
    assert repository.language is None
    assert repository.license is None
    assert repository.topics == ()
    assert repository.size_kb == 0
    assert repository.stars == 0
    assert repository.default_branch == "main"
    assert repository.created_at is None


@pytest.mark.parametrize("missing", ["id", "name", "full_name"])
def test_missing_identity_field_is_reported(missing: str) -> None:
    incomplete = {key: value for key, value in FULL_PAYLOAD.items() if key != missing}

    with pytest.raises(InvalidResponseError, match="incomplet"):
        normalise_repository(incomplete)


def test_non_object_payload_is_reported() -> None:
    with pytest.raises(InvalidResponseError, match="objet JSON"):
        normalise_repository(["pas un objet"])  # type: ignore[arg-type]


def test_wrong_type_is_reported() -> None:
    with pytest.raises(InvalidResponseError, match="illisible"):
        normalise_repository(payload(id="pas un entier"))


def test_null_license_becomes_none() -> None:
    assert normalise_repository(payload(license=None)).license is None


def test_visibility_falls_back_on_the_private_flag() -> None:
    without_visibility = {key: value for key, value in FULL_PAYLOAD.items() if key != "visibility"}

    assert normalise_repository({**without_visibility, "private": True}).visibility == "private"
    assert normalise_repository({**without_visibility, "private": False}).visibility == "public"


def test_repository_model_is_immutable() -> None:
    """Un modèle normalisé est une donnée figée, pas un état modifiable."""
    normalised = normalise_repository(FULL_PAYLOAD)

    with pytest.raises(ValidationError):
        normalised.stars = 99  # type: ignore[misc]


# ── Dates ────────────────────────────────────────────────────────────────────


def test_dates_are_parsed_as_aware_utc() -> None:
    repository = normalise_repository(FULL_PAYLOAD)

    assert repository.created_at == datetime(2026, 1, 15, 10, 30, tzinfo=UTC)
    assert repository.pushed_at is not None
    assert repository.pushed_at.tzinfo is not None


def test_empty_repository_has_no_push_date() -> None:
    assert normalise_repository(payload(pushed_at=None)).pushed_at is None


def test_unreadable_date_is_reported() -> None:
    with pytest.raises(InvalidResponseError, match="illisible"):
        normalise_repository(payload(created_at="hier"))


def test_parse_datetime_handles_absence_and_offsets() -> None:
    assert parse_datetime(None) is None
    assert parse_datetime("  ") is None
    assert parse_datetime("2026-08-01T12:00:00Z") == datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    # Une date décalée est ramenée en UTC.
    assert parse_datetime("2026-08-01T14:00:00+02:00") == datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


# ── Filtrage ─────────────────────────────────────────────────────────────────


def repository(**overrides: Any) -> Repository:
    """Construit un repository normalisé, éventuellement modifié."""
    return normalise_repository(payload(**overrides))


@pytest.mark.parametrize(
    ("flags", "scan", "kept"),
    [
        ({}, ScanConfig(), True),
        ({"fork": True}, ScanConfig(), False),
        ({"fork": True}, ScanConfig(include_forks=True), True),
        ({"archived": True}, ScanConfig(), False),
        ({"archived": True}, ScanConfig(include_archived=True), True),
        ({"fork": True, "archived": True}, ScanConfig(include_forks=True), False),
        (
            {"fork": True, "archived": True},
            ScanConfig(include_forks=True, include_archived=True),
            True,
        ),
    ],
)
def test_scope_rules(flags: dict[str, Any], scan: ScanConfig, kept: bool) -> None:
    assert keep_repository(repository(**flags), scan) is kept


# ── Collecte de bout en bout ─────────────────────────────────────────────────


def test_collection_sorts_filters_and_counts(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        json=[
            payload(id=3, name="zeta", full_name="nouhailler/zeta"),
            payload(id=1, name="alpha", full_name="nouhailler/alpha"),
            payload(id=2, name="un-fork", full_name="nouhailler/un-fork", fork=True),
            payload(id=4, name="ancien", full_name="nouhailler/ancien", archived=True),
        ]
    )

    with GitHubClient("ghp_test") as client:
        collection = collect_repositories(client, ScanConfig())

    assert [repo.full_name for repo in collection.repositories] == [
        "nouhailler/alpha",
        "nouhailler/zeta",
    ]
    assert collection.excluded_forks == 1
    assert collection.excluded_archived == 1
    assert collection.total_seen == 4


def test_collection_requests_a_stable_order(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json=[])

    with GitHubClient("ghp_test") as client:
        collect_repositories(client, ScanConfig())

    request = httpx_mock.get_request()
    assert request is not None
    assert request.url.path == "/user/repos"
    assert request.url.params["sort"] == "full_name"
    assert request.url.params["per_page"] == "100"


def test_collection_follows_pagination(httpx_mock: HTTPXMock) -> None:
    page_two = f"{DEFAULT_API_URL}/user/repos?page=2"
    httpx_mock.add_response(
        url=f"{DEFAULT_API_URL}/user/repos?sort=full_name&direction=asc&per_page=100",
        json=[payload(id=1, name="a", full_name="nouhailler/a")],
        headers={"link": f'<{page_two}>; rel="next"'},
    )
    httpx_mock.add_response(url=page_two, json=[payload(id=2, name="b", full_name="nouhailler/b")])

    with GitHubClient("ghp_test") as client:
        collection = collect_repositories(client, ScanConfig())

    assert len(collection.repositories) == 2


def test_a_single_malformed_repository_stops_the_collection(httpx_mock: HTTPXMock) -> None:
    """Mieux vaut échouer visiblement que produire un inventaire silencieusement faux."""
    httpx_mock.add_response(json=[payload(), {"name": "sans identifiant"}])

    with GitHubClient("ghp_test") as client, pytest.raises(InvalidResponseError):
        collect_repositories(client, ScanConfig())
