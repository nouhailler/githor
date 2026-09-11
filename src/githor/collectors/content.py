"""Signaux dérivés du contenu de quelques fichiers ciblés.

Exception bornée à l'invariant du V0.1 (« un marqueur ne lit qu'un nom,
jamais un contenu », cf. :mod:`githor.collectors.structure`) : certains
constats — une mention légale, une section « à propos », un lien vers un
site — ne portent pas un nom de fichier prévisible, seulement du texte. Le
contenu récupéré reste strictement borné à :data:`CONTENT_MARKERS` : jamais
l'arborescence entière, jamais plus d'un appel par marqueur présent.
"""

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from githor.github.client import GitHubClient
from githor.github.repositories import get_content
from githor.models.repository import Repository

CONTENT_MARKERS: tuple[str, ...] = (
    "readme",
    "legal_notice",
    "about_page",
    "index_html",
    "package_json",
)
"""Marqueurs dont le contenu est récupéré, quand ils sont présents."""

LEGAL_NOTICE_KEYWORDS: tuple[str, ...] = ("mentions légales", "mentions legales")
ABOUT_KEYWORDS: tuple[str, ...] = ("à propos", "a propos")
SWINUX_DOMAIN = "swinux.ch"

AUTO_UPDATE_DEPENDENCIES: tuple[str, ...] = (
    "vite-plugin-pwa",
    "workbox-",
    "register-service-worker",
)
"""Dépendances connues assurant une mise à jour automatique côté client.

Une dépendance plutôt qu'un nom de fichier (repéré à titre d'exemple dans
Astror, ``src/pwaUpdate.js`` → ``registerSW()``, sans que ce projet ne fasse
référence) : cela généralise à des projets qui n'organisent pas leur code de
la même façon.
"""


@dataclass(frozen=True)
class ContentSignals:
    """Signaux booléens dérivés du contenu récupéré pour un repository."""

    signals: Mapping[str, bool]

    def get(self, name: str) -> bool:
        """Vaut ``True`` si le signal a été détecté, ``False`` sinon ou s'il est inconnu."""
        return self.signals.get(name, False)


def collect_content_signals(
    client: GitHubClient, repository: Repository, markers: Mapping[str, str | None]
) -> ContentSignals:
    """Récupère le contenu des marqueurs présents et en dérive des signaux.

    Args:
        client: client GitHub authentifié.
        repository: repository normalisé (pour ``full_name``, ``default_branch``
            et ``homepage``).
        markers: marqueurs déjà relevés par :func:`githor.collectors.structure.detect_markers`.
    """
    texts: dict[str, str] = {}
    for marker in CONTENT_MARKERS:
        path = markers.get(marker)
        if path is None:
            continue
        content = get_content(client, repository.full_name, path, repository.default_branch)
        if content is not None:
            texts[marker] = content

    haystacks = [texts[name] for name in ("readme", "index_html") if name in texts]

    signals = {
        "legal_notice": markers.get("legal_notice") is not None
        or _contains_any(haystacks, LEGAL_NOTICE_KEYWORDS),
        "about": markers.get("about_page") is not None or _contains_any(haystacks, ABOUT_KEYWORDS),
        "swinux_link": _mentions_swinux(repository.homepage, texts.values()),
        "auto_update": _has_auto_update_dependency(texts.get("package_json")),
    }
    return ContentSignals(signals)


def _contains_any(haystacks: list[str], keywords: tuple[str, ...]) -> bool:
    lowered = [haystack.casefold() for haystack in haystacks]
    return any(keyword in haystack for haystack in lowered for keyword in keywords)


def _mentions_swinux(homepage: str | None, texts: Iterable[str]) -> bool:
    if homepage is not None and SWINUX_DOMAIN in homepage:
        return True
    return any(SWINUX_DOMAIN in text for text in texts)


def _has_auto_update_dependency(package_json: str | None) -> bool:
    if package_json is None:
        return False
    try:
        payload = json.loads(package_json)
    except ValueError:
        return False
    if not isinstance(payload, dict):
        return False

    names = {*payload.get("dependencies", {}), *payload.get("devDependencies", {})}
    return any(
        name.startswith(dependency) for name in names for dependency in AUTO_UPDATE_DEPENDENCIES
    )
