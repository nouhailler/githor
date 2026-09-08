"""Conseiller de projets multi-dépôts, en langage naturel (§35, cahier des charges).

Contrairement à :mod:`githor.analysis.advisor` (une liste de constats connus,
transformés un à un en recommandation), une question libre ne se laisse pas
découper en éléments à associer un par un : il n'y a pas de structure de
sortie fixe à valider. Le principe reste le même que partout ailleurs dans
Githor : Ollama ne voit que des faits déjà vérifiés, jamais une supposition.
Githor résume tout le parc en texte — depuis le même :class:`Dataset` que
``githor export``/``compare`` — et demande à Ollama de répondre uniquement à
partir de ce résumé, en citant les dépôts concernés.

Ce module ne connaît ni la base de données ni la CLI : il reçoit un
``Dataset`` déjà construit et rend une réponse.
"""

from typing import Protocol

from githor.exporters.dataset import Dataset, RepositoryExport
from githor.logging import get_logger
from githor.utils.markdown import ABSENT, format_moment

logger = get_logger("analysis.portfolio_advisor")


class _Generator(Protocol):
    """Ce que la génération a besoin d'un client Ollama — pas plus.

    Découple ce module de :mod:`githor.ollama`, sur le même principe que
    ``analysis.advisor._Generator`` : un faux générateur suffit à tester
    ``answer_question`` sans réseau ni serveur Ollama.
    """

    def generate(self, prompt: str, *, model: str, format: str | None = "json") -> str: ...  # noqa: A002


def summarize_repository(export: RepositoryExport) -> str:
    """Résume un dépôt en une ligne, assez complète pour répondre à une question dessus.

    Sans quoi Ollama devrait deviner ce qui compte selon la question posée ;
    en donnant toujours les mêmes faits, la réponse reste traçable à ce que
    Githor a réellement mesuré, quelle que soit la question.
    """
    language = export.snapshot.primary_language if export.snapshot else None
    score = export.score

    open_rules = ", ".join(finding.rule for finding in export.open_findings)
    tests = "inconnu (jamais audité)"
    if export.code is not None:
        tests = f"{export.code.test_files} fichier(s)" if export.code.test_files else "aucun"

    return (
        f"- {export.full_name}"
        f" | langage : {language or ABSENT}"
        f" | score global : {_cell(score.overall) if score else ABSENT}"
        f" (docs={_cell(score.docs if score else None)},"
        f" tests={_cell(score.tests if score else None)},"
        f" ci={_cell(score.ci if score else None)},"
        f" security={_cell(score.security if score else None)})"
        f" | constats ouverts : {open_rules or 'aucun'}"
        f" | tests locaux : {tests}"
        f" | dernière activité : {format_moment(export.pushed_at) or ABSENT}"
        f"{' | archivé' if export.archived else ''}"
    )


def build_question_prompt(question: str, dataset: Dataset) -> str:
    """Construit le prompt : un résumé de chaque dépôt, puis la question posée."""
    lines = [
        f"Voici l'état de {dataset.repository_count} dépôt(s) enregistré(s), "
        "un par ligne, tel que mesuré par Githor :",
        "",
        *(summarize_repository(export) for export in dataset.repositories),
        "",
        f"Question : {question}",
        "",
        "Réponds en français, uniquement à partir des dépôts listés ci-dessus. "
        "Cite leur nom complet (propriétaire/dépôt) quand tu en mentionnes un. "
        "Si l'information demandée ne figure pas dans cette liste, dis-le "
        "clairement plutôt que de l'inventer.",
    ]
    return "\n".join(lines)


def answer_question(client: _Generator, *, question: str, dataset: Dataset, model: str) -> str:
    """Pose une question en langage naturel sur l'ensemble du parc.

    La réponse est demandée en prose libre (``format=None``) : il n'y a rien
    à structurer ni à associer, contrairement à ``generate_advice``.
    """
    prompt = build_question_prompt(question, dataset)
    logger.debug("Question posée sur %s dépôt(s) : %s", dataset.repository_count, question)
    return client.generate(prompt, model=model, format=None)


def _cell(value: int | None) -> str:
    """Une case vide plutôt qu'un zéro trompeur, comme dans ``githor.scoring``."""
    return ABSENT if value is None else str(value)
