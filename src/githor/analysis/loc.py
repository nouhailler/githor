"""Décompte des lignes : code, commentaire, vide.

Le comptage est **syntaxique et non lexical** : il reconnaît les préfixes de
commentaire et les délimiteurs de bloc déclarés pour le langage, sans analyser
la grammaire. Deux limites en découlent, assumées et documentées.

**Une chaîne contenant un délimiteur peut tromper le compteur.** ``url = "//x"``
n'est pas un commentaire, mais la ligne porte du code de toute façon : elle est
comptée comme code, et l'erreur reste sans effet. Un ``/*`` à l'intérieur d'une
chaîne, en revanche, ouvrirait à tort un bloc. Le cas est rare et le prix d'une
analyse lexicale complète, par langage, serait sans commune mesure.

**Une docstring Python est du code.** Elle est évaluée, attachée à l'objet et
lisible à l'exécution : la compter comme un commentaire fausserait la part
commentée dans un sens flatteur. Seul ``#`` ouvre un commentaire en Python.
"""

from githor.analysis.languages import Language
from githor.models.code import LineCounts


def count_lines(content: str, language: Language) -> LineCounts:
    """Compte les lignes d'un contenu selon la syntaxe de commentaires du langage.

    Une ligne appartient à une seule catégorie, dans cet ordre : vide si elle ne
    porte rien, commentaire si tout ce qu'elle porte en est un, code sinon. Une
    ligne mêlant code et commentaire est donc du code — c'est le code qui s'y
    exécute.

    Args:
        content: contenu du fichier, décodé.
        language: langage reconnu pour ce fichier.

    Returns:
        Un décompte dont les trois catégories somment le total.
    """
    counts = {"code": 0, "comment": 0, "blank": 0}
    closing: str | None = None

    for raw in content.splitlines():
        line = raw.strip()

        if closing is not None:
            counts["comment"] += 1
            after = _after(line, closing)
            if after is not None:
                closing = None
                if after:
                    # Du code suit la fin du bloc : la ligne porte du code.
                    counts["comment"] -= 1
                    counts["code"] += 1
            continue

        if not line:
            counts["blank"] += 1
            continue

        category, closing = _classify(line, language)
        counts[category] += 1

    return LineCounts(
        total=counts["code"] + counts["comment"] + counts["blank"],
        code=counts["code"],
        comment=counts["comment"],
        blank=counts["blank"],
    )


def _classify(line: str, language: Language) -> tuple[str, str | None]:
    """Classe une ligne non vide, et dit quel bloc de commentaire elle laisse ouvert.

    Returns:
        La catégorie de la ligne, et le délimiteur fermant attendu si un bloc
        reste ouvert à la fin de la ligne.
    """
    lowered = line.lower()
    if any(lowered.startswith(prefix) for prefix in language.line_comments):
        return "comment", None

    for opening, ending in language.block_comments:
        if not line.startswith(opening):
            continue
        rest = line[len(opening) :]
        after = _after(rest, ending)
        if after is None:
            return "comment", ending
        # Bloc ouvert et refermé sur la même ligne : ce qui suit décide.
        return ("code" if after else "comment"), None

    return "code", None


def _after(line: str, closing: str) -> str | None:
    """Retourne ce qui suit le délimiteur fermant, ou ``None`` s'il est absent."""
    index = line.find(closing)
    if index < 0:
        return None
    return line[index + len(closing) :].strip()
