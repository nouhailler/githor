"""Tests des collectors (étape 6) : normalisation, filtrage, dates.

Les réponses GitHub sont mockées : aucun appel réseau, aucun jeton réel.
"""

import base64
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError
from pytest_httpx import HTTPXMock

from githor.collectors.activity import collect_activity, normalise_commit, summarise
from githor.collectors.issues import collect_issues, normalise_issue, sort_out
from githor.collectors.languages import collect_languages, compute_breakdown
from githor.collectors.releases import collect_releases, normalise_release
from githor.collectors.repositories import (
    collect_repositories,
    keep_repository,
    normalise_repository,
)
from githor.collectors.structure import collect_structure, detect_markers, normalise_tree
from githor.config import ScanConfig
from githor.github.client import DEFAULT_API_URL, GitHubClient
from githor.github.errors import InvalidResponseError
from githor.github.repositories import get_content
from githor.models.repository import Repository
from githor.utils.dates import parse_datetime, utc_now

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


# ── Langages ─────────────────────────────────────────────────────────────────


def test_percentages_are_computed_and_sorted() -> None:
    breakdown = compute_breakdown({"CSS": 178, "TypeScript": 724, "HTML": 71, "JSON": 27})

    assert [language.language for language in breakdown] == ["TypeScript", "CSS", "HTML", "JSON"]
    assert breakdown[0].percentage == 72.4
    assert breakdown[0].bytes == 724
    assert sum(language.percentage for language in breakdown) == pytest.approx(100.0, abs=0.2)


def test_raw_byte_counts_are_preserved() -> None:
    """Les octets bruts sont conservés : le pourcentage est une commodité, pas la source."""
    assert compute_breakdown({"Python": 12345})[0].bytes == 12345


def test_empty_or_zero_breakdown_is_empty() -> None:
    assert compute_breakdown({}) == []
    assert compute_breakdown({"Python": 0}) == []


def test_languages_of_an_empty_repository(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=409, json={"message": "Git Repository is empty."})

    with GitHubClient("ghp_test", max_retries=0) as client:
        assert collect_languages(client, "nouhailler/vide") == []


def test_languages_are_fetched_and_normalised(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json={"Python": 750, "HTML": 250})

    with GitHubClient("ghp_test") as client:
        breakdown = collect_languages(client, "nouhailler/x")

    assert [(entry.language, entry.percentage) for entry in breakdown] == [
        ("Python", 75.0),
        ("HTML", 25.0),
    ]


# ── Structure ────────────────────────────────────────────────────────────────

TREE = {
    "truncated": False,
    "tree": [
        {"path": "README.md", "type": "blob", "size": 120},
        {"path": "LICENSE", "type": "blob", "size": 1000},
        {"path": ".github", "type": "tree"},
        {"path": ".github/workflows", "type": "tree"},
        {"path": ".github/workflows/ci.yml", "type": "blob", "size": 200},
        {"path": "tests", "type": "tree"},
        {"path": "tests/test_x.py", "type": "blob", "size": 300},
        {"path": "src", "type": "tree"},
        {"path": "src/app.py", "type": "blob", "size": 900},
        {"path": "pyproject.toml", "type": "blob", "size": 400},
    ],
}


def test_tree_is_normalised_with_counts() -> None:
    structure = normalise_tree(TREE)

    assert structure.file_count == 6
    assert structure.directory_count == 4
    assert structure.truncated is False
    assert structure.files[0].path == "README.md"
    assert structure.files[0].size == 120


def test_markers_name_the_path_that_satisfied_them() -> None:
    """Un constat doit pouvoir dire sur quel fichier il se fonde."""
    markers = normalise_tree(TREE).markers

    assert markers["readme"] == "README.md"
    assert markers["license"] == "LICENSE"
    assert markers["github_workflows"] == ".github/workflows"
    assert markers["tests"] == "tests"
    assert markers["src"] == "src"
    assert markers["pyproject"] == "pyproject.toml"


def test_absent_markers_are_none() -> None:
    markers = normalise_tree(TREE).markers

    assert markers["changelog"] is None
    assert markers["contributing"] is None
    assert markers["docs"] is None
    assert markers["dockerfile"] is None
    assert markers["dependabot"] is None


def test_marker_detection_ignores_case() -> None:
    markers = detect_markers(["Readme.MD", "Licence"])

    assert markers["readme"] == "Readme.MD"
    assert markers["license"] is None  # « Licence » n'est pas une orthographe reconnue


@pytest.mark.parametrize(
    ("path", "marker"),
    [
        ("docs/index.md", "docs"),
        ("doc/index.md", "docs"),
        ("test/test_a.py", "tests"),
        (".github/dependabot.yml", "dependabot"),
        ("compose.yaml", "compose"),
        ("docker-compose.yml", "compose"),
        ("Dockerfile", "dockerfile"),
        ("package-lock.json", "package_lock"),
        ("pnpm-lock.yaml", "package_lock"),
    ],
)
def test_marker_variants_are_recognised(path: str, marker: str) -> None:
    assert detect_markers([path])[marker] == path


def test_a_directory_name_is_not_matched_as_a_prefix() -> None:
    """``documentation/`` ne doit pas être confondu avec ``docs/``."""
    assert detect_markers(["documentation/index.md", "sources/app.py"])["docs"] is None


def test_truncated_tree_is_reported(
    caplog: pytest.LogCaptureFixture, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(json={"truncated": True, "tree": [{"path": "a", "type": "blob"}]})

    with (
        GitHubClient("ghp_test") as client,
        caplog.at_level("WARNING", logger="githor.collectors.structure"),
    ):
        structure = collect_structure(client, repository())

    assert structure.truncated is True
    assert any("tronquée" in record.message for record in caplog.records)


def test_structure_of_an_empty_repository(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=409, json={"message": "Git Repository is empty."})

    with GitHubClient("ghp_test", max_retries=0) as client:
        structure = collect_structure(client, repository())

    assert structure.files == []
    assert structure.file_count == 0


def test_structure_uses_the_default_branch(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json=TREE)

    with GitHubClient("ghp_test") as client:
        collect_structure(client, repository(default_branch="develop"))

    request = httpx_mock.get_request()
    assert request is not None
    assert request.url.path.endswith("/git/trees/develop")
    assert request.url.params["recursive"] == "1"


# ── Contenu (étape 41) ───────────────────────────────────────────────────────


def test_get_content_decodes_base64(httpx_mock: HTTPXMock) -> None:
    encoded = base64.b64encode(b"# Astror\n\nUn projet.").decode()
    httpx_mock.add_response(json={"encoding": "base64", "content": encoded})

    with GitHubClient("ghp_test") as client:
        content = get_content(client, "nouhailler/Astror", "README.md", "main")

    assert content == "# Astror\n\nUn projet."


def test_get_content_of_a_missing_file_is_none(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=404, json={"message": "Not Found"})

    with GitHubClient("ghp_test", max_retries=0) as client:
        assert get_content(client, "nouhailler/Astror", "legal.html", "main") is None


def test_get_content_of_a_directory_is_none(httpx_mock: HTTPXMock) -> None:
    """L'API renvoie une liste pour un répertoire : aucun encodage base64 à décoder."""
    httpx_mock.add_response(json=[{"name": "README.md", "type": "file"}])

    with GitHubClient("ghp_test") as client:
        assert get_content(client, "nouhailler/Astror", "docs", "main") is None


def test_get_content_of_binary_data_is_none(httpx_mock: HTTPXMock) -> None:
    encoded = base64.b64encode(b"\xff\xfe\x00\x01").decode()
    httpx_mock.add_response(json={"encoding": "base64", "content": encoded})

    with GitHubClient("ghp_test") as client:
        assert get_content(client, "nouhailler/Astror", "logo.png", "main") is None


def test_get_content_requests_the_given_ref(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json={"encoding": "base64", "content": base64.b64encode(b"x").decode()})

    with GitHubClient("ghp_test") as client:
        get_content(client, "nouhailler/Astror", "README.md", "develop")

    request = httpx_mock.get_request()
    assert request is not None
    assert request.url.params["ref"] == "develop"


# ── Activité ─────────────────────────────────────────────────────────────────

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


def commit_payload(sha: str, days_ago: int) -> dict[str, Any]:
    """Construit un commit GitHub daté d'il y a ``days_ago`` jours."""
    moment = NOW - timedelta(days=days_ago)
    return {
        "sha": sha,
        "commit": {
            "message": f"Commit {sha[:4]}",
            "author": {"name": "nouhailler", "date": moment.isoformat().replace("+00:00", "Z")},
        },
    }


def test_commit_is_normalised() -> None:
    commit = normalise_commit(commit_payload("a" * 40, 3))

    assert commit is not None
    assert commit.sha == "a" * 40
    assert commit.author == "nouhailler"
    assert commit.committed_at == NOW - timedelta(days=3)


def test_a_commit_without_sha_is_skipped() -> None:
    assert normalise_commit({"commit": {"message": "sans sha"}}) is None


def test_an_unreadable_date_does_not_lose_the_commit() -> None:
    commit = normalise_commit({"sha": "b" * 40, "commit": {"author": {"date": "avant-hier"}}})

    assert commit is not None
    assert commit.committed_at is None


def test_activity_counts_each_window() -> None:
    commits = [
        normalise_commit(commit_payload("a" * 40, 2)),
        normalise_commit(commit_payload("b" * 40, 20)),
        normalise_commit(commit_payload("c" * 40, 60)),
        normalise_commit(commit_payload("d" * 40, 85)),
    ]

    activity = summarise([c for c in commits if c], window_days=90, now=NOW)

    assert activity.total == 4
    assert activity.commits_30_days == 2
    assert activity.commits_90_days == 4
    assert activity.last_commit_at == NOW - timedelta(days=2)


def test_a_window_too_short_yields_no_count() -> None:
    """Mieux vaut une valeur absente qu'un décompte faux."""
    activity = summarise([], window_days=14, now=NOW)

    assert activity.commits_30_days is None
    assert activity.commits_90_days is None
    assert activity.window_days == 14


def test_activity_without_commits_has_no_last_commit() -> None:
    activity = summarise([], window_days=90, now=NOW)

    assert activity.last_commit_at is None
    assert activity.total == 0
    assert activity.commits_30_days == 0


def test_activity_of_an_empty_repository(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=409, json={"message": "Git Repository is empty."})

    with GitHubClient("ghp_test", max_retries=0) as client:
        activity = collect_activity(client, "nouhailler/vide", days=90)

    assert activity.total == 0
    assert activity.window_days == 90


def test_activity_requests_only_the_configured_window(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json=[commit_payload("a" * 40, 1)])

    with GitHubClient("ghp_test") as client:
        collect_activity(client, "nouhailler/x", days=30)

    request = httpx_mock.get_request()
    assert request is not None
    since = datetime.fromisoformat(request.url.params["since"].replace("Z", "+00:00"))
    assert 29 <= (utc_now() - since).days <= 30


# ── Releases et issues (étape 11) ────────────────────────────────────────────


def test_a_release_is_normalised_field_by_field() -> None:
    release = normalise_release(
        {
            "tag_name": "v1.2.0",
            "name": "Version 1.2",
            "published_at": "2026-08-01T10:00:00Z",
            "draft": False,
            "prerelease": True,
        }
    )

    assert release is not None
    assert release.tag == "v1.2.0"
    assert release.name == "Version 1.2"
    assert release.published_at == datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
    assert release.prerelease is True


def test_a_release_without_tag_is_ignored() -> None:
    """Une release illisible ne doit pas interrompre la collecte du dépôt."""
    assert normalise_release({"name": "Sans tag"}) is None


def test_a_draft_release_has_no_publication_date() -> None:
    release = normalise_release({"tag_name": "v2.0.0", "draft": True, "published_at": None})

    assert release is not None
    assert release.draft is True
    assert release.published_at is None


def test_pull_requests_are_separated_from_issues() -> None:
    """GitHub range les pull requests parmi les issues : Githor les sépare."""
    collection = sort_out(
        [
            {"id": 1, "number": 1, "title": "Bogue", "state": "open"},
            {"id": 2, "number": 2, "title": "Corrigé", "state": "closed"},
            {"id": 3, "number": 3, "title": "Une PR", "state": "open", "pull_request": {}},
            {"id": 4, "number": 4, "title": "PR fermée", "state": "closed", "pull_request": {}},
        ]
    )

    assert [issue.number for issue in collection.issues] == [1, 2]
    assert collection.open_issues == 1
    assert collection.closed_issues == 1
    assert collection.open_pull_requests == 1


def test_an_unreadable_issue_is_ignored_without_stopping_the_others() -> None:
    collection = sort_out([{"id": 1, "number": 1, "state": "open"}, {"title": "sans identifiant"}])

    assert len(collection.issues) == 1


def test_issue_dates_are_parsed_in_utc() -> None:
    issue = normalise_issue(
        {
            "id": 7,
            "number": 12,
            "title": "Titre",
            "state": "closed",
            "created_at": "2026-01-02T03:04:05Z",
            "closed_at": "2026-02-03T04:05:06Z",
        }
    )

    assert issue is not None
    assert issue.created_at == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert issue.closed_at == datetime(2026, 2, 3, 4, 5, 6, tzinfo=UTC)
    assert issue.is_open is False


def test_collect_releases_returns_an_empty_list_for_a_repository_without_release(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(url=re.compile(r".*/releases.*"), json=[])

    with GitHubClient("jeton") as client:
        assert collect_releases(client, "nouhailler/Architecturor") == []


def test_collect_issues_survives_a_repository_without_issue_tracker(
    httpx_mock: HTTPXMock,
) -> None:
    """Le suivi d'issues désactivé produit un 404 : une collection vide, pas une erreur."""
    httpx_mock.add_response(url=re.compile(r".*/issues.*"), status_code=404, json={})

    with GitHubClient("jeton") as client:
        collection = collect_issues(client, "nouhailler/Architecturor")

    assert collection.issues == ()
    assert collection.open_pull_requests == 0
