"""Fixtures communes à la suite de tests."""

import logging
from pathlib import Path

import pytest

from githor import config as config_module
from githor.github import client as client_module
from githor.github import token as token_module
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


@pytest.fixture(autouse=True)
def isolate_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Isole les tests de la machine qui les exécute.

    Ni la configuration personnelle de l'utilisateur ni son token GitHub ne
    doivent influencer les résultats.
    """
    monkeypatch.delenv(config_module.GITHUB_TOKEN_ENV, raising=False)
    monkeypatch.delenv(config_module.CONFIG_PATH_ENV, raising=False)
    monkeypatch.setattr(config_module, "USER_CONFIG_PATH", tmp_path / "inexistant" / "config.toml")


@pytest.fixture(autouse=True)
def neutralise_gh_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empêche la suite d'interroger la vraie CLI ``gh``.

    Sans ce garde-fou, un test lancé sur une machine où ``gh`` est authentifié
    récupérerait un jeton GitHub réel : les tests ne doivent jamais en dépendre.
    Les tests qui portent sur ce chemin remplacent eux-mêmes ``subprocess.run``.
    """

    def refuse(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("gh est neutralisé pendant les tests")

    monkeypatch.setattr(token_module.subprocess, "run", refuse)


@pytest.fixture(autouse=True)
def never_really_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralise l'attente entre deux tentatives du client GitHub.

    Une requête non mockée déclencherait sinon les vraies temporisations de
    reprise, et la suite mettrait des dizaines de secondes à échouer.
    """
    monkeypatch.setattr(client_module.time, "sleep", lambda _: None)
