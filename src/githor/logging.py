"""Configuration du logging applicatif.

La CLI doit rester lisible : par défaut seuls les avertissements et les erreurs
sont affichés. ``--debug`` abaisse le niveau et ajoute l'origine des messages.

Les logs sont écrits sur ``stderr`` afin que ``stdout`` reste réservé aux
données produites par les commandes (exports, rapports).
"""

import logging

from rich.console import Console
from rich.logging import RichHandler

LOGGER_NAME = "githor"


def get_logger(name: str | None = None) -> logging.Logger:
    """Retourne le logger de l'application, ou l'un de ses enfants.

    Args:
        name: suffixe du logger enfant, par exemple ``"github.client"``.
    """
    if name:
        return logging.getLogger(f"{LOGGER_NAME}.{name}")
    return logging.getLogger(LOGGER_NAME)


def setup_logging(*, debug: bool = False) -> None:
    """Configure le logger racine de l'application.

    Args:
        debug: si vrai, niveau ``DEBUG`` et affichage du module d'origine ;
            sinon niveau ``WARNING`` pour ne pas polluer la sortie CLI.
    """
    level = logging.DEBUG if debug else logging.WARNING

    handler = RichHandler(
        console=Console(stderr=True),
        show_time=debug,
        show_path=debug,
        rich_tracebacks=debug,
        markup=False,
    )
    handler.setFormatter(logging.Formatter("%(message)s", datefmt="[%X]"))

    logger = get_logger()
    logger.setLevel(level)
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.propagate = False

    # httpx journalise chaque requête en INFO : trop bavard hors mode debug.
    logging.getLogger("httpx").setLevel(logging.DEBUG if debug else logging.WARNING)
