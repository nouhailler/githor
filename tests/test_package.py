"""Vérifications minimales du packaging : le paquet et ses couches sont importables."""

import importlib

import pytest

import githor


def test_version_is_exposed() -> None:
    assert githor.__version__ == "0.1.0"


@pytest.mark.parametrize(
    "module",
    [
        "githor.github",
        "githor.models",
        "githor.collectors",
        "githor.storage",
        "githor.exporters",
        "githor.utils",
    ],
)
def test_layers_are_importable(module: str) -> None:
    assert importlib.import_module(module) is not None
