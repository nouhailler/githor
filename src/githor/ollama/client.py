"""Client HTTP bas niveau pour l'API d'un serveur Ollama local.

Ce module ne connaît ni la base de données, ni la CLI, ni les modèles de
Githor : il expose un seul appel — générer du texte à partir d'un prompt — et
traduit les échecs en erreurs explicites. Contrairement à ``github.client``,
il n'y a ni authentification, ni quota, ni pagination : Ollama tourne en
local, sur la même machine que Githor.
"""

from typing import Any, Self

import httpx

from githor.logging import get_logger
from githor.ollama.errors import (
    InvalidResponseError,
    ModelNotFoundError,
    OllamaTimeoutError,
    OllamaUnavailableError,
)

logger = get_logger("ollama.client")

DEFAULT_HOST = "http://localhost:11434"

DEFAULT_TIMEOUT_SECONDS = 180.0
"""L'inférence locale peut prendre plusieurs minutes ; un timeout court la couperait à tort."""

DEFAULT_CONNECT_TIMEOUT_SECONDS = 5.0
"""Le serveur est local : soit il répond aussitôt, soit il ne tourne pas."""


class OllamaClient:
    """Accès à un serveur Ollama local.

    S'utilise de préférence comme gestionnaire de contexte, afin que la
    connexion HTTP sous-jacente soit fermée :

    ```python
    with OllamaClient(host) as client:
        text = client.generate("...", model="llama3.1")
    ```
    """

    def __init__(
        self, host: str = DEFAULT_HOST, *, timeout: float = DEFAULT_TIMEOUT_SECONDS
    ) -> None:
        """Prépare le client.

        Args:
            host: racine du serveur Ollama, par exemple ``http://localhost:11434``.
            timeout: délai maximal, en secondes, accordé à une génération.
        """
        self.host = host.rstrip("/")
        self._client = httpx.Client(
            base_url=self.host,
            timeout=httpx.Timeout(timeout, connect=min(timeout, DEFAULT_CONNECT_TIMEOUT_SECONDS)),
        )

    # ── Cycle de vie ─────────────────────────────────────────────────────────

    def close(self) -> None:
        """Ferme la connexion HTTP sous-jacente."""
        self._client.close()

    def __enter__(self) -> Self:
        """Entre dans le gestionnaire de contexte."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Ferme le client en sortie de contexte."""
        self.close()

    # ── Appel public ─────────────────────────────────────────────────────────

    def generate(self, prompt: str, *, model: str, format: str | None = "json") -> str:  # noqa: A002
        """Demande une génération à Ollama et retourne le texte produit.

        Par défaut, la réponse est contrainte au format JSON par Ollama
        lui-même (``format: "json"``) : décoder ce texte plus loin est la
        responsabilité de l'appelant, ce client ne fait que le transporter.
        ``format=None`` l'omet, pour une réponse en prose libre — utile à un
        conseiller qui répond à une question plutôt qu'il ne structure une
        liste de recommandations.

        Args:
            prompt: texte envoyé au modèle.
            model: nom du modèle Ollama à interroger.
            format: format demandé à Ollama, ``"json"`` par défaut ; ``None``
                pour laisser le modèle répondre librement.

        Raises:
            OllamaUnavailableError: serveur injoignable ou en erreur.
            ModelNotFoundError: modèle absent du serveur.
            OllamaTimeoutError: délai dépassé.
            InvalidResponseError: enveloppe HTTP illisible ou incomplète.
        """
        logger.debug("POST %s/api/generate (modèle %s)", self.host, model)
        payload: dict[str, object] = {"model": model, "prompt": prompt, "stream": False}
        if format is not None:
            payload["format"] = format

        try:
            response = self._client.post("/api/generate", json=payload)
        except httpx.TimeoutException as exc:
            raise OllamaTimeoutError(
                f"Délai dépassé lors de l'appel à Ollama ({self.host}) pour le modèle « {model} »."
            ) from exc
        except httpx.TransportError as exc:
            raise OllamaUnavailableError(
                f"Impossible de joindre Ollama sur {self.host}.\n"
                "Vérifiez que le serveur tourne : ollama serve"
            ) from exc

        if response.status_code == 404:
            raise ModelNotFoundError(
                f"Modèle « {model} » introuvable sur {self.host}.\n"
                f"Téléchargez-le : ollama pull {model}"
            )
        if not response.is_success:
            raise OllamaUnavailableError(
                f"Ollama a répondu HTTP {response.status_code} pour {self.host}."
            )

        return self._response_text(response)

    def _response_text(self, response: httpx.Response) -> str:
        """Extrait le champ ``response`` de l'enveloppe JSON d'Ollama."""
        try:
            payload: Any = response.json()
        except ValueError as exc:
            raise InvalidResponseError(
                f"Réponse JSON illisible depuis Ollama ({self.host})."
            ) from exc

        text = payload.get("response") if isinstance(payload, dict) else None
        if not isinstance(text, str):
            raise InvalidResponseError(
                f"Réponse Ollama sans champ « response » exploitable depuis {self.host}."
            )
        return text
