"""Analyse syntaxique des modules Python : structure, complexité, imports.

Tout passe par le module ``ast`` de la bibliothèque standard : aucune
dépendance, et surtout aucune heuristique. Ce qui est rapporté ici est ce que
l'interpréteur lui-même lit dans le fichier.

L'analyse s'arrête à Python. Compter les fonctions d'un fichier TypeScript à
coups d'expressions régulières produirait un chiffre invérifiable ; conformément
au principe posé en V0.1, mieux vaut ne rien dire que dire faux. Les autres
langages sont donc comptés, pas disséqués.
"""

import ast
import sys

from githor.models.code import ClassAnalysis, FunctionAnalysis, Import, ImportKind

STDLIB_MODULES = frozenset(sys.stdlib_module_names)
"""Modules de la bibliothèque standard, tels que l'interpréteur les connaît.

Cette liste dépend de la version de Python qui exécute Githor, non de celle du
dépôt analysé. L'écart est marginal et la solution de rechange — embarquer une
liste figée — vieillirait sans prévenir.
"""

_BRANCHING = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.ExceptHandler,
    ast.With,
    ast.AsyncWith,
    ast.Assert,
    ast.IfExp,
    ast.match_case,
)
"""Nœuds ajoutant chacun un chemin d'exécution au sens de McCabe."""


class SyntaxErrorInModule(Exception):
    """Le fichier n'est pas du Python valide pour cet interpréteur."""


def parse_module(source: str, *, path: str) -> ast.Module:
    """Analyse syntaxiquement un module Python.

    Args:
        source: contenu du fichier.
        path: chemin, repris dans le message d'erreur.

    Raises:
        SyntaxErrorInModule: si le fichier ne se parse pas.
    """
    try:
        return ast.parse(source, filename=path)
    except SyntaxError as exc:
        raise SyntaxErrorInModule(f"ligne {exc.lineno or 0} : {exc.msg}") from exc
    except ValueError as exc:
        # Un octet nul dans la source, par exemple : ast.parse le refuse.
        raise SyntaxErrorInModule(str(exc)) from exc


def complexity_of(node: ast.AST) -> int:
    """Calcule la complexité cyclomatique de McCabe d'un nœud.

    Un chemin d'exécution au départ, plus un par embranchement : conditions,
    boucles, gestionnaires d'exception, cas de ``match``, expressions
    conditionnelles, et chaque terme supplémentaire d'un ``and`` ou d'un ``or``,
    qui court-circuite et crée donc bien un chemin.

    Les fonctions imbriquées ne sont **pas** décomptées : leur complexité leur
    appartient et leur est attribuée séparément, faute de quoi elle serait
    comptée deux fois.

    Args:
        node: fonction, ou tout nœud dont on veut la complexité.
    """
    total = 1

    for child in _own_body(node):
        if isinstance(child, _BRANCHING):
            total += 1
        elif isinstance(child, ast.BoolOp):
            total += len(child.values) - 1
        elif isinstance(child, ast.comprehension):
            total += 1 + len(child.ifs)

    return total


def _own_body(node: ast.AST) -> list[ast.AST]:
    """Parcourt un nœud sans entrer dans les fonctions et classes imbriquées.

    ``ast.walk`` descendrait dans les définitions internes, dont la complexité
    est mesurée pour elles-mêmes : les additionner reviendrait à charger deux
    fois la même branche.
    """
    nested = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    found: list[ast.AST] = []
    stack: list[ast.AST] = list(ast.iter_child_nodes(node))

    while stack:
        current = stack.pop()
        found.append(current)
        if not isinstance(current, nested):
            stack.extend(ast.iter_child_nodes(current))

    return found


def _argument_count(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Compte les paramètres déclarés, ``*args`` et ``**kwargs`` compris."""
    arguments = node.args
    return (
        len(arguments.posonlyargs)
        + len(arguments.args)
        + len(arguments.kwonlyargs)
        + int(arguments.vararg is not None)
        + int(arguments.kwarg is not None)
    )


def collect_functions(tree: ast.Module) -> tuple[FunctionAnalysis, ...]:
    """Relève toutes les fonctions du module, méthodes comprises.

    Une méthode est nommée ``Classe.méthode`` : c'est sous ce nom qu'un rapport
    doit la citer pour être utile.
    """
    found: list[FunctionAnalysis] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found.append(
                    FunctionAnalysis(
                        name=f"{prefix}{child.name}",
                        line=child.lineno,
                        complexity=complexity_of(child),
                        arguments=_argument_count(child),
                        is_async=isinstance(child, ast.AsyncFunctionDef),
                        has_docstring=ast.get_docstring(child) is not None,
                    )
                )
                # Une fonction imbriquée est une fonction : elle est relevée
                # pour elle-même, sous le nom de celle qui la contient.
                visit(child, f"{prefix}{child.name}.")

    visit(tree, "")
    return tuple(found)


def collect_classes(tree: ast.Module) -> tuple[ClassAnalysis, ...]:
    """Relève les classes du module, avec leur nombre de méthodes directes."""
    found: list[ClassAnalysis] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                methods = sum(
                    1
                    for item in child.body
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                )
                found.append(
                    ClassAnalysis(
                        name=f"{prefix}{child.name}",
                        line=child.lineno,
                        methods=methods,
                        has_docstring=ast.get_docstring(child) is not None,
                    )
                )
                visit(child, f"{prefix}{child.name}.")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, prefix)

    visit(tree, "")
    return tuple(found)


def has_module_docstring(tree: ast.Module) -> bool:
    """Dit si le module porte une docstring de tête."""
    return ast.get_docstring(tree) is not None


def classify_import(module: str, *, level: int, local_roots: frozenset[str]) -> ImportKind:
    """Range un import parmi stdlib, local et tierce partie.

    Un import relatif est local par construction. Un module dont la racine est
    un paquet du dépôt est local lui aussi — c'est ce qui distingue
    ``import githor.config`` d'une vraie dépendance. Le reste est ce qu'il faut
    installer pour exécuter le code.

    Args:
        module: module importé, en notation pointée.
        level: nombre de points d'un import relatif ; ``0`` s'il est absolu.
        local_roots: modules et paquets de premier niveau du dépôt.
    """
    if level > 0:
        return ImportKind.LOCAL

    root = module.split(".", 1)[0]
    if root in local_roots:
        return ImportKind.LOCAL
    if root in STDLIB_MODULES:
        return ImportKind.STDLIB
    return ImportKind.THIRD_PARTY


def collect_imports(tree: ast.Module, *, local_roots: frozenset[str]) -> tuple[Import, ...]:
    """Relève les imports du module, y compris ceux faits dans une fonction.

    Un import local — dans une fonction, pour casser un cycle — reste un import :
    il conditionne l'exécution au même titre qu'un import de tête.
    """
    found: list[Import] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(
                Import(
                    module=alias.name,
                    kind=classify_import(alias.name, level=0, local_roots=local_roots),
                    line=node.lineno,
                )
                for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or "." * max(node.level, 1)
            found.append(
                Import(
                    module=module,
                    kind=classify_import(module, level=node.level, local_roots=local_roots),
                    line=node.lineno,
                )
            )

    return tuple(sorted(found, key=lambda item: (item.line, item.module)))
