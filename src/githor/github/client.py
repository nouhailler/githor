"""Client HTTP bas niveau pour l'API REST GitHub.

Ce module ne connaît ni la base de données, ni la CLI, ni les modèles de
Githor : il expose des appels HTTP authentifiés, paginés et tolérants aux
pannes passagères, et traduit les échecs en erreurs explicites.

Deux garde-fous structurent le comportement :

- les tentatives sont bornées (``max_retries``), avec une attente
  exponentielle ; le client ne boucle jamais indéfiniment ;
- lorsque le quota d'appels est épuisé, le client **échoue immédiatement** en
  indiquant l'heure de réinitialisation, plutôt que d'attendre une heure.
"""

import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Self

import httpx

from githor import __version__
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
from githor.logging import get_logger
from githor.utils.dates import from_epoch, utc_now

logger = get_logger("github.client")

DEFAULT_API_URL = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"
USER_AGENT = f"githor/{__version__} (+https://github.com/nouhailler/Githor)"

DEFAULT_TIMEOUT = 30.0
DEFAULT_CONNECT_TIMEOUT = 10.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_PAGE_SIZE = 100
DEFAULT_MAX_PAGES = 100

BACKOFF_BASE_SECONDS = 1.0
BACKOFF_MAX_SECONDS = 30.0

# En dessous de ce ratio du quota, l'utilisateur est averti.
LOW_RATE_LIMIT_RATIO = 0.1


@dataclass(frozen=True)
class RateLimit:
    """État du quota d'appels renvoyé par GitHub."""

    limit: int
    remaining: int
    used: int
    reset_at: datetime

    @classmethod
    def from_headers(cls, headers: Mapping[str, str]) -> "RateLimit | None":
        """Construit un quota depuis les en-têtes ``X-RateLimit-*``.

        Returns:
            ``None`` si les en-têtes sont absents ou illisibles ; leur absence
            n'est pas une erreur, toutes les routes n'en fournissent pas.
        """
        lowered = {key.lower(): value for key, value in headers.items()}
        try:
            limit = int(lowered["x-ratelimit-limit"])
            remaining = int(lowered["x-ratelimit-remaining"])
            reset_at = from_epoch(float(lowered["x-ratelimit-reset"]))
        except (KeyError, TypeError, ValueError):
            return None

        try:
            used = int(lowered["x-ratelimit-used"])
        except (KeyError, TypeError, ValueError):
            used = max(0, limit - remaining)

        return cls(limit=limit, remaining=remaining, used=used, reset_at=reset_at)

    @property
    def is_exhausted(self) -> bool:
        """Vrai si plus aucune requête n'est autorisée avant la réinitialisation."""
        return self.remaining <= 0

    @property
    def is_low(self) -> bool:
        """Vrai si le quota restant passe sous le seuil d'alerte."""
        return self.remaining <= max(1, int(self.limit * LOW_RATE_LIMIT_RATIO))

    @property
    def seconds_until_reset(self) -> float:
        """Secondes restantes avant la réinitialisation du quota, jamais négatif."""
        return max(0.0, (self.reset_at - utc_now()).total_seconds())


class GitHubClient:
    """Accès authentifié à l'API REST GitHub.

    S'utilise de préférence comme gestionnaire de contexte, afin que la
    connexion HTTP sous-jacente soit fermée :

    ```python
    with GitHubClient(token) as client:
        user = client.get("/user")
    ```
    """

    def __init__(
        self,
        token: str,
        *,
        api_url: str = DEFAULT_API_URL,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Prépare le client.

        Args:
            token: jeton d'accès GitHub ; jamais journalisé ni affiché.
            api_url: racine de l'API, pour viser une instance GitHub Enterprise.
            timeout: délai maximal, en secondes, pour une requête.
            max_retries: nombre de nouvelles tentatives après un échec passager.
            sleep: fonction d'attente, remplaçable dans les tests.

        Raises:
            AuthenticationError: si aucun token n'est fourni.
        """
        if not token:
            raise AuthenticationError("Aucun token GitHub n'a été fourni au client.")

        self.api_url = api_url.rstrip("/")
        self.max_retries = max(0, max_retries)
        self.rate_limit: RateLimit | None = None

        self._sleep = sleep
        self._low_quota_reported = False
        self._client = httpx.Client(
            base_url=self.api_url,
            timeout=httpx.Timeout(timeout, connect=min(timeout, DEFAULT_CONNECT_TIMEOUT)),
            follow_redirects=True,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": GITHUB_API_VERSION,
                "User-Agent": USER_AGENT,
            },
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

    # ── Appels publics ───────────────────────────────────────────────────────

    def get(self, path: str, *, params: Mapping[str, Any] | None = None) -> Any:
        """Exécute un GET et retourne le JSON décodé.

        Args:
            path: chemin relatif (``/user``) ou URL absolue.
            params: paramètres de requête.

        Raises:
            GitHubError: pour toute erreur d'authentification, d'accès, de
                quota, de délai ou de réponse inattendue.
        """
        return self._decode(self._request(path, params=params))

    def get_paginated(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        page_size: int = DEFAULT_PAGE_SIZE,
        max_pages: int = DEFAULT_MAX_PAGES,
    ) -> Iterator[Any]:
        """Parcourt une collection paginée et produit ses éléments un à un.

        La pagination suit l'en-tête ``Link`` fourni par GitHub, et non un
        compteur de pages calculé localement.

        Args:
            path: chemin relatif de la collection.
            params: paramètres de requête de la première page.
            page_size: taille de page demandée (``per_page``).
            max_pages: borne de sécurité ; au-delà, le parcours s'arrête avec
                un avertissement plutôt que de continuer sans fin.

        Yields:
            Les éléments de chaque page, dans l'ordre renvoyé par GitHub.

        Raises:
            InvalidResponseError: si une page n'est pas une liste JSON.
        """
        query: Mapping[str, Any] | None = {**(params or {}), "per_page": page_size}
        url = path

        for page in range(1, max_pages + 1):
            response = self._request(url, params=query)
            payload = self._decode(response)

            if not isinstance(payload, list):
                raise InvalidResponseError(
                    f"Réponse paginée inattendue pour {url} : une liste JSON était attendue."
                )

            logger.debug("Page %s de %s : %s élément(s)", page, path, len(payload))
            yield from payload

            next_link = response.links.get("next")
            if not next_link:
                return

            # L'URL suivante porte déjà ses propres paramètres.
            url = next_link["url"]
            query = None

        logger.warning(
            "Pagination de %s interrompue après %s pages (borne de sécurité) : "
            "les éléments suivants sont ignorés.",
            path,
            max_pages,
        )

    def get_rate_limit(self) -> RateLimit:
        """Interroge ``/rate_limit`` et retourne l'état du quota principal.

        Cette route ne consomme pas de quota.

        Raises:
            InvalidResponseError: si la réponse n'a pas la structure attendue.
        """
        payload = self.get("/rate_limit")
        try:
            core = payload["resources"]["core"]
            return RateLimit(
                limit=int(core["limit"]),
                remaining=int(core["remaining"]),
                used=int(core.get("used", 0)),
                reset_at=from_epoch(float(core["reset"])),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidResponseError(
                "Réponse inattendue de /rate_limit : quota principal introuvable."
            ) from exc

    # ── Mécanique interne ────────────────────────────────────────────────────

    def _request(self, url: str, *, params: Mapping[str, Any] | None = None) -> httpx.Response:
        """Exécute un GET avec un nombre borné de tentatives."""
        attempts = self.max_retries + 1

        for attempt in range(1, attempts + 1):
            last = attempt == attempts
            try:
                logger.debug("GET %s (tentative %s/%s)", url, attempt, attempts)
                response = self._client.get(url, params=params)
            except httpx.TimeoutException as exc:
                if last:
                    raise TimeoutError(
                        f"Délai dépassé lors de l'appel à {url} après {attempts} tentative(s)."
                    ) from exc
                self._wait(attempt, reason="délai dépassé")
                continue
            except httpx.TransportError as exc:
                if last:
                    raise APIUnavailableError(f"API GitHub injoignable ({url}) : {exc}") from exc
                self._wait(attempt, reason="erreur réseau")
                continue

            self._record_rate_limit(response)

            if response.is_success:
                return response

            retry_after = self._reject_or_delay(response)

            if last:
                raise self._exhausted(response, attempts)
            self._wait(attempt, reason=f"HTTP {response.status_code}", delay=retry_after)

        # Inatteignable : la boucle retourne ou lève systématiquement.
        raise APIUnavailableError(f"Échec de l'appel à {url}.")

    def _exhausted(self, response: httpx.Response, attempts: int) -> GitHubError:
        """Construit l'erreur finale, une fois toutes les tentatives consommées."""
        status = response.status_code
        url = response.request.url

        if status == 429 or (status == 403 and _retry_after(response) is not None):
            quota = self.rate_limit
            return RateLimitError(
                f"GitHub limite le rythme des appels (HTTP {status}) et continue de refuser "
                f"{url} après {attempts} tentative(s). Réessayez plus tard.",
                reset_at=quota.reset_at if quota else None,
            )

        return APIUnavailableError(
            f"API GitHub indisponible ({url}) : HTTP {status} "
            f"après {attempts} tentative(s). {_detail(response)}".strip()
        )

    def _reject_or_delay(self, response: httpx.Response) -> float | None:
        """Lève une erreur définitive, ou retourne le délai avant nouvelle tentative.

        Returns:
            Le délai imposé par ``Retry-After`` s'il est présent, sinon ``None``
            pour laisser jouer l'attente exponentielle.

        Raises:
            GitHubError: si l'échec est définitif et ne doit pas être retenté.
        """
        status = response.status_code
        retry_after = _retry_after(response)
        quota = self.rate_limit

        # Quota principal épuisé : réessayer ne servirait à rien avant la
        # réinitialisation, qui peut être à une heure de là.
        if status in (403, 429) and quota is not None and quota.is_exhausted:
            raise RateLimitError(
                f"Quota GitHub épuisé ({quota.used}/{quota.limit} requêtes). "
                f"Réinitialisation à {quota.reset_at:%H:%M:%S} UTC "
                f"(dans {quota.seconds_until_reset / 60:.0f} min).",
                reset_at=quota.reset_at,
            )

        # Limite secondaire : GitHub demande explicitement d'attendre.
        if status == 429 or (status == 403 and retry_after is not None):
            logger.warning("Limite secondaire GitHub atteinte, nouvelle tentative différée.")
            return retry_after

        if status == 401:
            raise AuthenticationError(
                "Token GitHub refusé (HTTP 401) : il est invalide, expiré ou révoqué. "
                f"{_detail(response)}".strip()
            )
        if status == 403:
            raise PermissionError(
                f"Accès refusé par GitHub (HTTP 403) pour {response.request.url}. "
                f"{_detail(response)}".strip()
            )
        if status == 404:
            raise NotFoundError(
                f"Ressource introuvable : {response.request.url} "
                "(inexistante, supprimée, ou invisible pour ce token)."
            )
        if status >= 500:
            return retry_after

        raise UnexpectedStatusError(
            f"Réponse inattendue de GitHub : HTTP {status} pour {response.request.url}. "
            f"{_detail(response)}".strip()
        )

    def _decode(self, response: httpx.Response) -> Any:
        """Décode le corps JSON d'une réponse réussie."""
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise InvalidResponseError(
                f"Réponse JSON illisible depuis {response.request.url}."
            ) from exc

    def _record_rate_limit(self, response: httpx.Response) -> None:
        """Mémorise le quota et avertit une seule fois lorsqu'il devient bas."""
        quota = RateLimit.from_headers(response.headers)
        if quota is None:
            return

        self.rate_limit = quota

        if not quota.is_low:
            self._low_quota_reported = False
        elif not self._low_quota_reported:
            self._low_quota_reported = True
            logger.warning(
                "Quota GitHub bas : %s/%s requêtes restantes, réinitialisation dans %.0f min.",
                quota.remaining,
                quota.limit,
                quota.seconds_until_reset / 60,
            )

    def _wait(self, attempt: int, *, reason: str, delay: float | None = None) -> None:
        """Attend avant une nouvelle tentative, en respectant ``Retry-After``."""
        if delay is None:
            delay = min(BACKOFF_BASE_SECONDS * 2 ** (attempt - 1), BACKOFF_MAX_SECONDS)
        delay = min(delay, BACKOFF_MAX_SECONDS)
        logger.warning("%s ; nouvelle tentative dans %.1f s.", reason.capitalize(), delay)
        self._sleep(delay)


def _retry_after(response: httpx.Response) -> float | None:
    """Lit l'en-tête ``Retry-After`` en secondes, s'il est exploitable."""
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        # La forme « date HTTP » existe mais GitHub renvoie des secondes.
        return None


def _detail(response: httpx.Response) -> str:
    """Extrait le message d'erreur fourni par GitHub, s'il y en a un."""
    try:
        payload = response.json()
    except ValueError:
        return ""
    if isinstance(payload, dict) and isinstance(payload.get("message"), str):
        return str(payload["message"])
    return ""
