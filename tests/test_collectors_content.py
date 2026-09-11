"""Tests des signaux de contenu (étape 43) : mentions légales, à propos, lien
vers swinux.ch, mise à jour automatique.

Les réponses GitHub sont mockées : aucun appel réseau, aucun jeton réel.
"""

import base64
import json
from typing import Any

from pytest_httpx import HTTPXMock

from githor.collectors.content import collect_content_signals
from githor.github.client import DEFAULT_API_URL, GitHubClient
from githor.models.repository import Repository

FULL_NAME = "nouhailler/Astror"


def astror(**overrides: Any) -> Repository:
    """Construit un repository minimal, éventuellement modifié."""
    defaults: dict[str, Any] = {
        "github_id": 1,
        "name": "Astror",
        "full_name": FULL_NAME,
        "owner": "nouhailler",
        "html_url": f"https://github.com/{FULL_NAME}",
        "default_branch": "main",
    }
    return Repository(**{**defaults, **overrides})


def respond(
    httpx_mock: HTTPXMock, path: str, *, text: str | None = None, ref: str = "main"
) -> None:
    """Enregistre la réponse de l'API contents pour un chemin donné."""
    url = f"{DEFAULT_API_URL}/repos/{FULL_NAME}/contents/{path}?ref={ref}"
    if text is None:
        httpx_mock.add_response(url=url, status_code=404, json={"message": "Not Found"})
        return
    encoded = base64.b64encode(text.encode()).decode()
    httpx_mock.add_response(url=url, json={"encoding": "base64", "content": encoded})


def markers(**present: str) -> dict[str, str | None]:
    """Construit une table de marqueurs, les absents valant ``None``."""
    names = ("readme", "legal_notice", "about_page", "index_html", "package_json")
    return {name: present.get(name) for name in names}


# ── Mentions légales / à propos ──────────────────────────────────────────────


def test_legal_notice_detected_via_dedicated_page(httpx_mock: HTTPXMock) -> None:
    marks = markers(readme="README.md", legal_notice="mentions-legales.html")
    respond(httpx_mock, "README.md", text="# Astror")
    respond(httpx_mock, "mentions-legales.html", text="<h1>Mentions légales</h1>")

    with GitHubClient("ghp_test") as client:
        signals = collect_content_signals(client, astror(), marks)

    assert signals.get("legal_notice") is True


def test_legal_notice_detected_via_readme_keyword(httpx_mock: HTTPXMock) -> None:
    marks = markers(readme="README.md")
    respond(httpx_mock, "README.md", text="## Mentions légales\n\nPatrick Nouhailler.")

    with GitHubClient("ghp_test") as client:
        signals = collect_content_signals(client, astror(), marks)

    assert signals.get("legal_notice") is True


def test_about_detected_via_readme_keyword_case_insensitive(httpx_mock: HTTPXMock) -> None:
    marks = markers(readme="README.md")
    respond(httpx_mock, "README.md", text="## À PROPOS\n\nCe projet sert à...")

    with GitHubClient("ghp_test") as client:
        signals = collect_content_signals(client, astror(), marks)

    assert signals.get("about") is True


def test_legal_notice_and_about_absent(httpx_mock: HTTPXMock) -> None:
    marks = markers(readme="README.md")
    respond(httpx_mock, "README.md", text="# Astror\n\nRien d'autre.")

    with GitHubClient("ghp_test") as client:
        signals = collect_content_signals(client, astror(), marks)

    assert signals.get("legal_notice") is False
    assert signals.get("about") is False


def test_no_readme_at_all_leaves_signals_false(httpx_mock: HTTPXMock) -> None:
    with GitHubClient("ghp_test") as client:
        signals = collect_content_signals(client, astror(), markers())

    assert signals.get("legal_notice") is False
    assert signals.get("about") is False
    assert signals.get("swinux_link") is False
    assert signals.get("auto_update") is False


# ── Lien vers swinux.ch ──────────────────────────────────────────────────────


def test_swinux_link_detected_via_homepage(httpx_mock: HTTPXMock) -> None:
    with GitHubClient("ghp_test") as client:
        signals = collect_content_signals(client, astror(homepage="https://swinux.ch"), markers())

    assert signals.get("swinux_link") is True


def test_swinux_link_detected_via_index_html(httpx_mock: HTTPXMock) -> None:
    marks = markers(index_html="index.html")
    respond(httpx_mock, "index.html", text='<a href="https://swinux.ch">Swinux</a>')

    with GitHubClient("ghp_test") as client:
        signals = collect_content_signals(client, astror(), marks)

    assert signals.get("swinux_link") is True


def test_swinux_link_absent(httpx_mock: HTTPXMock) -> None:
    marks = markers(readme="README.md")
    respond(httpx_mock, "README.md", text="# Astror")

    with GitHubClient("ghp_test") as client:
        signals = collect_content_signals(client, astror(), marks)

    assert signals.get("swinux_link") is False


# ── Mise à jour automatique ──────────────────────────────────────────────────


def test_auto_update_detected_via_known_dependency(httpx_mock: HTTPXMock) -> None:
    marks = markers(package_json="package.json")
    respond(
        httpx_mock,
        "package.json",
        text=json.dumps({"devDependencies": {"vite-plugin-pwa": "^0.20.0"}}),
    )

    with GitHubClient("ghp_test") as client:
        signals = collect_content_signals(client, astror(), marks)

    assert signals.get("auto_update") is True


def test_auto_update_absent_without_known_dependency(httpx_mock: HTTPXMock) -> None:
    marks = markers(package_json="package.json")
    respond(httpx_mock, "package.json", text=json.dumps({"dependencies": {"react": "^18.0.0"}}))

    with GitHubClient("ghp_test") as client:
        signals = collect_content_signals(client, astror(), marks)

    assert signals.get("auto_update") is False


def test_auto_update_absent_without_package_json(httpx_mock: HTTPXMock) -> None:
    with GitHubClient("ghp_test") as client:
        signals = collect_content_signals(client, astror(), markers())

    assert signals.get("auto_update") is False


def test_malformed_package_json_is_not_an_error(httpx_mock: HTTPXMock) -> None:
    marks = markers(package_json="package.json")
    respond(httpx_mock, "package.json", text="{ceci n'est pas du JSON")

    with GitHubClient("ghp_test") as client:
        signals = collect_content_signals(client, astror(), marks)

    assert signals.get("auto_update") is False
