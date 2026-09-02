"""Fixtures communes à la suite de tests."""

import logging

import pytest

from githor.logging import LOGGER_NAME


@pytest.fixture(autouse=True)
def reset_application_logger() -> None:
    """Remet le logger applicatif à zéro entre deux tests.

    ``setup_logging`` modifie un état global : sans ce nettoyage, l'ordre
    d'exécution des tests influencerait leurs résultats.
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.handlers.clear()
    logger.setLevel(logging.NOTSET)
