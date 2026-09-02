"""Résolution du jeton d'accès GitHub.

Deux sources, dans cet ordre :

1. la variable d'environnement ``GITHUB_TOKEN`` ;
2. la CLI officielle ``gh``, interrogée avec ``gh auth token``, si elle est
   installée et authentifiée.

Le second recours évite d'avoir à exporter le jeton en permanence — ce qui
serait risqué : ``gh`` lit lui-même ``GITHUB_TOKEN`` et la préfère à son
trousseau, si bien qu'un jeton restreint exporté dans le shell casserait
``git push``. Il reste désactivable par configuration.

Le jeton n'est jamais journalisé, ni affiché, ni écrit sur disque : seule sa
provenance est rendue visible à l'utilisateur.
"""

import subprocess
from dataclasses import dataclass, field
from enum import StrEnum

from githor.config import GITHUB_TOKEN_ENV, get_github_token
from githor.github.errors import AuthenticationError
from githor.logging import get_logger

logger = get_logger("github.token")

GH_COMMAND = ("gh", "auth", "token")
GH_TIMEOUT_SECONDS = 10.0


class TokenSource(StrEnum):
    """Origine du jeton effectivement utilisé."""

    ENVIRONMENT = "environnement"
    GH_CLI = "gh CLI"


@dataclass(frozen=True)
class ResolvedToken:
    """Jeton GitHub et sa provenance.

    ``value`` est exclu de la représentation textuelle : un ``repr`` accidentel
    dans un log ou une traceback ne doit jamais divulguer le secret.
    """

    value: str = field(repr=False)
    source: TokenSource

    @property
    def description(self) -> str:
        """Phrase courte décrivant la provenance, sans révéler le jeton."""
        if self.source is TokenSource.ENVIRONMENT:
            return f"configuré ({GITHUB_TOKEN_ENV})"
        return "configuré (gh CLI)"


def token_from_gh_cli() -> str | None:
    """Demande son jeton à la CLI ``gh``.

    Returns:
        Le jeton, ou ``None`` si ``gh`` est absent, non authentifié ou muet.
        Aucune de ces situations n'est une erreur à ce stade.
    """
    try:
        result = subprocess.run(  # noqa: S603 — commande fixe, sans shell
            GH_COMMAND,
            capture_output=True,
            text=True,
            timeout=GH_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError:
        logger.debug("La CLI gh n'est pas installée.")
        return None
    except subprocess.TimeoutExpired:
        logger.warning("« gh auth token » n'a pas répondu en %.0f s.", GH_TIMEOUT_SECONDS)
        return None
    except OSError as exc:
        logger.debug("La CLI gh n'a pas pu être exécutée : %s", exc)
        return None

    if result.returncode != 0:
        # La sortie n'est jamais journalisée : elle contiendrait le jeton.
        logger.debug("« gh auth token » a échoué (code %s).", result.returncode)
        return None

    return result.stdout.strip() or None


def find_token(*, allow_gh_cli: bool = True) -> ResolvedToken | None:
    """Cherche un jeton sans échouer s'il n'y en a pas.

    Args:
        allow_gh_cli: autorise le recours à ``gh auth token``.
    """
    from_env = get_github_token()
    if from_env:
        logger.debug("Jeton GitHub lu depuis %s.", GITHUB_TOKEN_ENV)
        return ResolvedToken(value=from_env, source=TokenSource.ENVIRONMENT)

    if not allow_gh_cli:
        return None

    from_gh = token_from_gh_cli()
    if from_gh:
        logger.debug("Jeton GitHub obtenu auprès de la CLI gh.")
        return ResolvedToken(value=from_gh, source=TokenSource.GH_CLI)

    return None


def require_token(*, allow_gh_cli: bool = True) -> ResolvedToken:
    """Retourne un jeton, ou échoue avec un message actionnable.

    Raises:
        AuthenticationError: si aucune source ne fournit de jeton.
    """
    token = find_token(allow_gh_cli=allow_gh_cli)
    if token is not None:
        return token

    remedies = [f'  export {GITHUB_TOKEN_ENV}="votre_token"']
    if allow_gh_cli:
        remedies.insert(0, "  gh auth login")
    else:
        remedies.append("  ou réactivez use_gh_cli dans la configuration")

    raise AuthenticationError(
        "Aucun jeton GitHub disponible.\nPour en fournir un :\n" + "\n".join(remedies)
    )
