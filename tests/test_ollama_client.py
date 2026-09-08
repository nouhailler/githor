"""Tests du client Ollama (étape 24).

Aucun appel réseau n'est effectué : le transport httpx est intégralement
mocké, comme pour le client GitHub. Ollama n'a ni authentification ni quota :
les tests portent sur la génération, ses erreurs, et rien d'autre.
"""

import json
from collections.abc import Iterator

import httpx
import pytest
from pytest_httpx import HTTPXMock

from githor.ollama.client import DEFAULT_HOST, OllamaClient
from githor.ollama.errors import (
    InvalidResponseError,
    ModelNotFoundError,
    OllamaTimeoutError,
    OllamaUnavailableError,
)

MODEL = "llama3.1"


@pytest.fixture
def client() -> Iterator[OllamaClient]:
    with OllamaClient() as instance:
        yield instance


def test_client_targets_localhost_by_default() -> None:
    with OllamaClient() as instance:
        assert instance.host == DEFAULT_HOST


def test_a_trailing_slash_in_the_host_is_removed() -> None:
    with OllamaClient("http://localhost:11434/") as instance:
        assert instance.host == "http://localhost:11434"


def test_generate_returns_the_response_field(httpx_mock: HTTPXMock, client: OllamaClient) -> None:
    httpx_mock.add_response(json={"response": '{"ok": true}'})

    assert client.generate("Bonjour", model=MODEL) == '{"ok": true}'


def test_generate_requests_json_and_disables_streaming(
    httpx_mock: HTTPXMock, client: OllamaClient
) -> None:
    httpx_mock.add_response(json={"response": "{}"})

    client.generate("Bonjour", model=MODEL)

    request = httpx_mock.get_request()
    assert request is not None
    assert request.url.path == "/api/generate"
    assert json.loads(request.content) == {
        "model": MODEL,
        "prompt": "Bonjour",
        "stream": False,
        "format": "json",
    }


def test_generate_omits_format_when_none_is_requested(
    httpx_mock: HTTPXMock, client: OllamaClient
) -> None:
    """Une réponse en prose libre — le conseiller de parc, par exemple — n'a pas à être du JSON."""
    httpx_mock.add_response(json={"response": "Une réponse en texte libre."})

    text = client.generate("Bonjour", model=MODEL, format=None)

    assert text == "Une réponse en texte libre."
    request = httpx_mock.get_request()
    assert request is not None
    assert "format" not in json.loads(request.content)


def test_an_unreachable_server_is_reported_clearly(
    httpx_mock: HTTPXMock, client: OllamaClient
) -> None:
    httpx_mock.add_exception(httpx.ConnectError("injoignable"))

    with pytest.raises(OllamaUnavailableError, match="ollama serve"):
        client.generate("Bonjour", model=MODEL)


def test_a_missing_model_is_reported_clearly(httpx_mock: HTTPXMock, client: OllamaClient) -> None:
    httpx_mock.add_response(status_code=404, json={"error": f"model '{MODEL}' not found"})

    with pytest.raises(ModelNotFoundError, match=f"ollama pull {MODEL}"):
        client.generate("Bonjour", model=MODEL)


def test_a_timeout_is_reported_clearly(httpx_mock: HTTPXMock, client: OllamaClient) -> None:
    httpx_mock.add_exception(httpx.ReadTimeout("trop long"))

    with pytest.raises(OllamaTimeoutError):
        client.generate("Bonjour", model=MODEL)


def test_a_server_error_is_reported(httpx_mock: HTTPXMock, client: OllamaClient) -> None:
    httpx_mock.add_response(status_code=500)

    with pytest.raises(OllamaUnavailableError, match="500"):
        client.generate("Bonjour", model=MODEL)


def test_unreadable_json_is_reported(httpx_mock: HTTPXMock, client: OllamaClient) -> None:
    httpx_mock.add_response(content=b"pas du json")

    with pytest.raises(InvalidResponseError):
        client.generate("Bonjour", model=MODEL)


def test_a_response_without_a_response_field_is_reported(
    httpx_mock: HTTPXMock, client: OllamaClient
) -> None:
    httpx_mock.add_response(json={"done": True})

    with pytest.raises(InvalidResponseError):
        client.generate("Bonjour", model=MODEL)
