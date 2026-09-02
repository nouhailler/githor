"""Tests du client GitHub (étape 4) : en-têtes, pagination, quota, erreurs, retries.

Aucun appel réseau n'est effectué : le transport httpx est intégralement mocké,
et aucun token réel n'est nécessaire.
"""

from collections.abc import Iterator

import httpx
import pytest
from pytest_httpx import HTTPXMock

from githor.github.client import (
    DEFAULT_API_URL,
    GITHUB_API_VERSION,
    GitHubClient,
    RateLimit,
)
from githor.github.errors import (
    APIUnavailableError,
    AuthenticationError,
    GitHubError,
    InvalidResponseError,
    NotFoundError,
    PermissionError,
    RateLimitError,
    TimeoutError,
    UnexpectedStatusError,
)
from githor.utils.dates import utc_now

TOKEN = "ghp_jeton_de_test"

# Quota confortable, très loin du seuil d'alerte.
HEALTHY_QUOTA = {
    "x-ratelimit-limit": "5000",
    "x-ratelimit-remaining": "4980",
    "x-ratelimit-used": "20",
    "x-ratelimit-reset": "4102444800",  # 2100-01-01
}


@pytest.fixture
def client() -> Iterator[GitHubClient]:
    """Client dont l'attente entre tentatives est neutralisée."""
    slept: list[float] = []
    instance = GitHubClient(TOKEN, max_retries=2, sleep=slept.append)
    instance.slept = slept  # type: ignore[attr-defined]
    with instance:
        yield instance


# ── Authentification et en-têtes ─────────────────────────────────────────────


def test_empty_token_is_refused() -> None:
    with pytest.raises(AuthenticationError):
        GitHubClient("")


def test_request_carries_authentication_and_api_version(
    httpx_mock: HTTPXMock, client: GitHubClient
) -> None:
    httpx_mock.add_response(json={"login": "nouhailler"}, headers=HEALTHY_QUOTA)

    assert client.get("/user") == {"login": "nouhailler"}

    request = httpx_mock.get_request()
    assert request is not None
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert request.headers["accept"] == "application/vnd.github+json"
    assert request.headers["x-github-api-version"] == GITHUB_API_VERSION
    assert request.headers["user-agent"].startswith("githor/")
    assert str(request.url) == f"{DEFAULT_API_URL}/user"


def test_api_url_is_configurable(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json={})

    with GitHubClient(TOKEN, api_url="https://github.example.com/api/v3/") as client:
        client.get("/user")

    request = httpx_mock.get_request()
    assert request is not None
    assert str(request.url) == "https://github.example.com/api/v3/user"


def test_query_parameters_are_forwarded(httpx_mock: HTTPXMock, client: GitHubClient) -> None:
    httpx_mock.add_response(json=[])

    client.get("/user/repos", params={"type": "owner"})

    request = httpx_mock.get_request()
    assert request is not None
    assert request.url.params["type"] == "owner"


# ── Pagination ───────────────────────────────────────────────────────────────


def test_pagination_follows_the_link_header(httpx_mock: HTTPXMock, client: GitHubClient) -> None:
    page_two = f"{DEFAULT_API_URL}/user/repos?page=2"
    httpx_mock.add_response(
        url=f"{DEFAULT_API_URL}/user/repos?per_page=100",
        json=[{"name": "a"}, {"name": "b"}],
        headers={"link": f'<{page_two}>; rel="next"'},
    )
    httpx_mock.add_response(url=page_two, json=[{"name": "c"}])

    names = [repo["name"] for repo in client.get_paginated("/user/repos")]

    assert names == ["a", "b", "c"]
    assert len(httpx_mock.get_requests()) == 2


def test_pagination_stops_without_next_link(httpx_mock: HTTPXMock, client: GitHubClient) -> None:
    httpx_mock.add_response(json=[{"name": "a"}])

    assert list(client.get_paginated("/user/repos")) == [{"name": "a"}]
    assert len(httpx_mock.get_requests()) == 1


def test_pagination_requests_the_configured_page_size(
    httpx_mock: HTTPXMock, client: GitHubClient
) -> None:
    httpx_mock.add_response(json=[])

    list(client.get_paginated("/user/repos", page_size=30, params={"sort": "pushed"}))

    request = httpx_mock.get_request()
    assert request is not None
    assert request.url.params["per_page"] == "30"
    assert request.url.params["sort"] == "pushed"


def test_pagination_is_bounded(httpx_mock: HTTPXMock, client: GitHubClient) -> None:
    """Une API qui renverrait toujours un lien suivant ne doit pas boucler sans fin."""
    httpx_mock.add_response(
        json=[{"name": "boucle"}],
        headers={"link": f'<{DEFAULT_API_URL}/user/repos?page=2>; rel="next"'},
        is_reusable=True,
    )

    items = list(client.get_paginated("/user/repos", max_pages=3))

    assert len(items) == 3
    assert len(httpx_mock.get_requests()) == 3


def test_pagination_rejects_a_non_list_page(httpx_mock: HTTPXMock, client: GitHubClient) -> None:
    httpx_mock.add_response(json={"message": "Not a list"})

    with pytest.raises(InvalidResponseError, match="liste JSON"):
        list(client.get_paginated("/user/repos"))


# ── Erreurs définitives ──────────────────────────────────────────────────────


def test_invalid_token_raises_authentication_error(
    httpx_mock: HTTPXMock, client: GitHubClient
) -> None:
    httpx_mock.add_response(status_code=401, json={"message": "Bad credentials"})

    with pytest.raises(AuthenticationError, match="401"):
        client.get("/user")

    assert len(httpx_mock.get_requests()) == 1  # jamais retenté


def test_missing_repository_raises_not_found(httpx_mock: HTTPXMock, client: GitHubClient) -> None:
    httpx_mock.add_response(status_code=404, json={"message": "Not Found"})

    with pytest.raises(NotFoundError, match="introuvable"):
        client.get("/repos/nouhailler/inexistant")

    assert len(httpx_mock.get_requests()) == 1


def test_forbidden_without_quota_raises_permission_error(
    httpx_mock: HTTPXMock, client: GitHubClient
) -> None:
    httpx_mock.add_response(
        status_code=403,
        json={"message": "Resource not accessible"},
        headers=HEALTHY_QUOTA,
    )

    with pytest.raises(PermissionError, match="refusé"):
        client.get("/repos/autrui/privé")


def test_unexpected_status_is_reported(httpx_mock: HTTPXMock, client: GitHubClient) -> None:
    httpx_mock.add_response(status_code=418, json={"message": "I'm a teapot"})

    with pytest.raises(UnexpectedStatusError, match="418"):
        client.get("/user")


def test_invalid_json_is_reported(httpx_mock: HTTPXMock, client: GitHubClient) -> None:
    httpx_mock.add_response(text="{ceci n'est pas du JSON", headers=HEALTHY_QUOTA)

    with pytest.raises(InvalidResponseError, match="illisible"):
        client.get("/user")


def test_empty_body_decodes_to_none(httpx_mock: HTTPXMock, client: GitHubClient) -> None:
    httpx_mock.add_response(status_code=204)

    assert client.get("/user") is None


def test_all_client_errors_derive_from_github_error(
    httpx_mock: HTTPXMock, client: GitHubClient
) -> None:
    httpx_mock.add_response(status_code=404, json={"message": "Not Found"})

    with pytest.raises(GitHubError):
        client.get("/absent")


# ── Quota ────────────────────────────────────────────────────────────────────


def test_rate_limit_is_recorded_from_headers(httpx_mock: HTTPXMock, client: GitHubClient) -> None:
    httpx_mock.add_response(json={}, headers=HEALTHY_QUOTA)

    client.get("/user")

    assert client.rate_limit is not None
    assert client.rate_limit.limit == 5000
    assert client.rate_limit.remaining == 4980
    assert client.rate_limit.used == 20


def test_missing_rate_limit_headers_are_not_an_error(
    httpx_mock: HTTPXMock, client: GitHubClient
) -> None:
    httpx_mock.add_response(json={})

    client.get("/user")

    assert client.rate_limit is None


def test_low_quota_is_reported_once(
    httpx_mock: HTTPXMock, client: GitHubClient, caplog: pytest.LogCaptureFixture
) -> None:
    low = {**HEALTHY_QUOTA, "x-ratelimit-remaining": "12", "x-ratelimit-used": "4988"}
    httpx_mock.add_response(json={}, headers=low, is_reusable=True)

    with caplog.at_level("WARNING", logger="githor.github.client"):
        client.get("/user")
        client.get("/user")

    warnings = [record for record in caplog.records if "Quota GitHub bas" in record.message]
    assert len(warnings) == 1


def test_exhausted_quota_fails_immediately(httpx_mock: HTTPXMock, client: GitHubClient) -> None:
    """Le client n'attend jamais la réinitialisation : il s'arrête et l'annonce."""
    exhausted = {**HEALTHY_QUOTA, "x-ratelimit-remaining": "0", "x-ratelimit-used": "5000"}
    httpx_mock.add_response(status_code=403, json={"message": "rate limit"}, headers=exhausted)

    with pytest.raises(RateLimitError) as caught:
        client.get("/user")

    assert caught.value.reset_at is not None
    assert len(httpx_mock.get_requests()) == 1
    assert client.slept == []  # type: ignore[attr-defined]


def test_get_rate_limit_reads_the_core_quota(httpx_mock: HTTPXMock, client: GitHubClient) -> None:
    httpx_mock.add_response(
        json={"resources": {"core": {"limit": 5000, "remaining": 4321, "used": 679, "reset": 0}}}
    )

    quota = client.get_rate_limit()

    assert (quota.limit, quota.remaining, quota.used) == (5000, 4321, 679)


def test_get_rate_limit_rejects_an_unexpected_payload(
    httpx_mock: HTTPXMock, client: GitHubClient
) -> None:
    httpx_mock.add_response(json={"resources": {}})

    with pytest.raises(InvalidResponseError, match="rate_limit"):
        client.get_rate_limit()


def test_rate_limit_helpers() -> None:
    quota = RateLimit(limit=5000, remaining=0, used=5000, reset_at=utc_now())

    assert quota.is_exhausted
    assert quota.is_low
    assert quota.seconds_until_reset >= 0.0
    assert not RateLimit(limit=5000, remaining=4000, used=1000, reset_at=utc_now()).is_low


# ── Tentatives ───────────────────────────────────────────────────────────────


def test_server_error_is_retried_then_succeeds(httpx_mock: HTTPXMock, client: GitHubClient) -> None:
    httpx_mock.add_response(status_code=503, json={"message": "unavailable"})
    httpx_mock.add_response(json={"login": "nouhailler"}, headers=HEALTHY_QUOTA)

    assert client.get("/user") == {"login": "nouhailler"}
    assert len(httpx_mock.get_requests()) == 2
    assert client.slept == [1.0]  # type: ignore[attr-defined]


def test_retries_are_bounded(httpx_mock: HTTPXMock, client: GitHubClient) -> None:
    httpx_mock.add_response(status_code=500, json={"message": "boom"}, is_reusable=True)

    with pytest.raises(APIUnavailableError, match="tentative"):
        client.get("/user")

    assert len(httpx_mock.get_requests()) == 3  # 1 essai + 2 nouvelles tentatives
    assert client.slept == [1.0, 2.0]  # type: ignore[attr-defined]


def test_secondary_rate_limit_honours_retry_after(
    httpx_mock: HTTPXMock, client: GitHubClient
) -> None:
    httpx_mock.add_response(
        status_code=403,
        json={"message": "secondary rate limit"},
        headers={**HEALTHY_QUOTA, "retry-after": "7"},
    )
    httpx_mock.add_response(json={}, headers=HEALTHY_QUOTA)

    client.get("/user")

    assert client.slept == [7.0]  # type: ignore[attr-defined]


def test_timeout_is_retried_then_reported(httpx_mock: HTTPXMock, client: GitHubClient) -> None:
    httpx_mock.add_exception(httpx.ReadTimeout("trop long"), is_reusable=True)

    with pytest.raises(TimeoutError, match="Délai dépassé"):
        client.get("/user")

    assert len(httpx_mock.get_requests()) == 3


def test_network_failure_is_retried_then_reported(
    httpx_mock: HTTPXMock, client: GitHubClient
) -> None:
    httpx_mock.add_exception(httpx.ConnectError("injoignable"), is_reusable=True)

    with pytest.raises(APIUnavailableError, match="injoignable"):
        client.get("/user")

    assert len(httpx_mock.get_requests()) == 3


def test_retries_can_be_disabled(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=500, json={"message": "boom"})

    with GitHubClient(TOKEN, max_retries=0, sleep=lambda _: None) as client:
        with pytest.raises(APIUnavailableError):
            client.get("/user")

    assert len(httpx_mock.get_requests()) == 1


def test_persistent_secondary_rate_limit_reports_a_rate_limit_error(
    httpx_mock: HTTPXMock, client: GitHubClient
) -> None:
    """Un throttling qui persiste doit être nommé comme tel, pas comme une panne."""
    httpx_mock.add_response(
        status_code=429,
        json={"message": "too many requests"},
        headers={**HEALTHY_QUOTA, "retry-after": "2"},
        is_reusable=True,
    )

    with pytest.raises(RateLimitError, match="rythme"):
        client.get("/user")

    assert len(httpx_mock.get_requests()) == 3
