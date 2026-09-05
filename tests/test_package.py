"""Vérifications minimales du packaging : le paquet et ses couches sont importables."""

import importlib
import re
import tomllib
from pathlib import Path

import pytest

import githor

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def test_version_is_exposed() -> None:
    assert SEMVER.match(githor.__version__)


def test_the_version_matches_the_packaging_metadata() -> None:
    """Les deux numéros doivent bouger ensemble.

    Le vérifier vaut mieux que de figer la version dans le test : un numéro
    codé en dur oblige à modifier le test à chaque publication, et ne dit rien
    du seul écart qui compte réellement.
    """
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    declared = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]

    assert githor.__version__ == declared


@pytest.mark.parametrize(
    "module",
    [
        "githor.github",
        "githor.models",
        "githor.collectors",
        "githor.storage",
        "githor.exporters",
        "githor.reports",
        "githor.rules",
        "githor.vcs",
        "githor.analysis",
        "githor.utils",
    ],
)
def test_layers_are_importable(module: str) -> None:
    assert importlib.import_module(module) is not None
