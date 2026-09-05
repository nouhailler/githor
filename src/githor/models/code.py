"""Modèles normalisés de l'analyse de code.

Ces modèles décrivent **ce qui a été lu sur disque**, indépendamment du langage
et de l'outil qui l'a mesuré. Ils obéissent aux mêmes règles que le reste de
Githor : une valeur absente vaut mieux qu'un chiffre faux, et tout constat doit
pouvoir citer le fichier qui l'a motivé.

Une analyse est une **mesure datée**, au même titre qu'un snapshot : elle porte
le commit sur lequel elle a porté, afin que deux analyses puissent être
comparées sans ambiguïté.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

_FROZEN = ConfigDict(extra="forbid", frozen=True)


class ImportKind(StrEnum):
    """Origine d'un import, telle que l'analyse a pu l'établir."""

    STDLIB = "stdlib"
    """Module de la bibliothèque standard, connu de l'interpréteur."""

    LOCAL = "local"
    """Module du dépôt lui-même, import relatif compris."""

    THIRD_PARTY = "third_party"
    """Tout le reste : ce qui doit être installé pour que le code s'exécute."""


class DependencyScope(StrEnum):
    """Rôle d'une dépendance dans le projet qui la déclare."""

    RUNTIME = "runtime"
    """Nécessaire à l'exécution : son absence casse le programme."""

    DEVELOPMENT = "development"
    """Nécessaire au développement seul : tests, linters, outillage."""

    OPTIONAL = "optional"
    """Attachée à un extra : installée seulement si on le demande."""


class LineCounts(BaseModel):
    """Décompte de lignes d'un fichier ou d'un ensemble de fichiers.

    Les trois catégories sont exclusives et leur somme fait ``total`` : un
    décompte qui ne se vérifie pas n'est pas un décompte.
    """

    model_config = _FROZEN

    total: int = 0
    code: int = 0
    comment: int = 0
    blank: int = 0

    def __add__(self, other: "LineCounts") -> "LineCounts":
        """Additionne deux décomptes, pour agréger un dépôt fichier par fichier."""
        return LineCounts(
            total=self.total + other.total,
            code=self.code + other.code,
            comment=self.comment + other.comment,
            blank=self.blank + other.blank,
        )

    @property
    def comment_ratio(self) -> float | None:
        """Part commentée du contenu non vide, ``None`` s'il n'y a rien à commenter.

        Le dénominateur exclut les lignes vides : commenter n'a de sens que
        rapporté à ce qui est écrit.
        """
        written = self.code + self.comment
        return round(self.comment / written * 100, 1) if written else None


class Import(BaseModel):
    """Un import relevé dans un module."""

    model_config = _FROZEN

    module: str
    """Module importé, en notation pointée ; ``.`` pour un import relatif nu."""

    kind: ImportKind
    line: int

    @property
    def root(self) -> str:
        """Premier segment du module — ce qui, pour une dépendance, s'installe."""
        return self.module.split(".", 1)[0]


class FunctionAnalysis(BaseModel):
    """Fonction ou méthode relevée dans un module."""

    model_config = _FROZEN

    name: str
    """Nom qualifié dans le module : ``Classe.méthode`` pour une méthode."""

    line: int
    complexity: int = 1
    """Complexité cyclomatique de McCabe : un chemin, plus un par embranchement."""

    arguments: int = 0
    is_async: bool = False
    has_docstring: bool = False


class ClassAnalysis(BaseModel):
    """Classe relevée dans un module."""

    model_config = _FROZEN

    name: str
    line: int
    methods: int = 0
    has_docstring: bool = False


class ModuleAnalysis(BaseModel):
    """Un fichier analysé, et tout ce qu'on a su en tirer.

    Un fichier dont seules les lignes ont été comptées — parce qu'il n'est pas
    écrit en Python, parce qu'il est trop gros, parce qu'il ne se parse pas —
    reste un module analysé : ses listes sont simplement vides, et
    :attr:`parse_error` dit pourquoi lorsqu'il y a une raison.
    """

    model_config = _FROZEN

    path: str
    """Chemin relatif à la racine du dépôt, en séparateurs POSIX."""

    language: str
    size_bytes: int = 0
    lines: LineCounts = Field(default_factory=LineCounts)

    functions: tuple[FunctionAnalysis, ...] = ()
    classes: tuple[ClassAnalysis, ...] = ()
    imports: tuple[Import, ...] = ()

    has_docstring: bool = False
    """Vrai si le module porte une docstring de tête."""

    is_test: bool = False
    """Vrai si le chemin du fichier le désigne comme un fichier de test."""

    parse_error: str | None = None
    """Motif de l'échec d'analyse syntaxique, ``None`` si tout s'est bien passé."""

    @property
    def is_parsed(self) -> bool:
        """Vrai si le contenu a été compris, et pas seulement compté."""
        return self.parse_error is None and bool(
            self.functions or self.classes or self.imports or self.has_docstring
        )

    @property
    def max_complexity(self) -> int:
        """Complexité de la fonction la plus complexe, ``0`` s'il n'y en a aucune."""
        return max((item.complexity for item in self.functions), default=0)


class LanguageLines(BaseModel):
    """Poids d'un langage dans le dépôt, mesuré sur le code réellement présent.

    À ne pas confondre avec :class:`githor.models.snapshot.Language`, qui reprend
    la répartition en octets calculée par GitHub. Les deux peuvent diverger, et
    c'est précisément l'intérêt : l'une décrit ce que GitHub voit, l'autre ce
    qui est sur disque une fois les artefacts écartés.
    """

    model_config = _FROZEN

    language: str
    files: int = 0
    lines: LineCounts = Field(default_factory=LineCounts)


class Dependency(BaseModel):
    """Dépendance déclarée dans un manifeste du dépôt.

    Ce sont les dépendances **déclarées**, non celles réellement installées ni
    celles réellement importées. L'écart entre les trois est intéressant en
    lui-même, et le rendre visible suppose de ne pas les confondre.
    """

    model_config = _FROZEN

    name: str
    """Nom tel qu'il est écrit dans le manifeste."""

    ecosystem: str
    """Écosystème dont le nom relève : ``PyPI``, ``npm``, ``crates.io``, ``Go``."""

    scope: DependencyScope = DependencyScope.RUNTIME

    specifier: str | None = None
    """Contrainte de version, ``None`` si le manifeste n'en pose aucune."""

    source: str
    """Manifeste qui l'a déclarée : un constat doit citer ce qui le motive."""

    group: str | None = None
    """Nom de l'extra ou du groupe, quand la dépendance en relève."""


class TestSuite(BaseModel):
    """Ce que le dépôt contient en fait de tests.

    La détection est structurelle : elle repose sur les conventions de nommage
    des fichiers, et sur l'AST pour les fonctions Python. Rien n'est exécuté —
    Githor ne lance jamais les tests d'un dépôt qu'il analyse.
    """

    model_config = _FROZEN

    files: int = 0
    functions: int = 0
    """Fonctions de test relevées ; établi pour Python seulement."""

    frameworks: tuple[str, ...] = ()
    """Cadres de test reconnus, par import ou par dépendance déclarée."""

    directories: tuple[str, ...] = ()
    """Répertoires de tests trouvés dans l'arborescence."""

    @property
    def exists(self) -> bool:
        """Vrai si le dépôt porte au moins un fichier de test."""
        return self.files > 0


class CodeAudit(BaseModel):
    """Résultat complet de l'analyse locale d'un dépôt, à une date donnée."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    analysed_at: datetime
    """Instant de l'analyse, en UTC."""

    commit: str
    """SHA du commit analysé : sans lui, la mesure ne serait rattachable à rien."""

    branch: str

    files_seen: int = 0
    """Fichiers rencontrés dans l'arborescence, exclusions déjà appliquées."""

    files_analysed: int = 0
    files_binary: int = 0
    files_too_large: int = 0
    """Fichiers écartés par ``audit.max_file_bytes`` : comptés, jamais analysés."""

    modules: tuple[ModuleAnalysis, ...] = ()
    languages: tuple[LanguageLines, ...] = ()
    dependencies: tuple[Dependency, ...] = ()
    tests: TestSuite = Field(default_factory=TestSuite)

    @property
    def lines(self) -> LineCounts:
        """Décompte de lignes de tout le dépôt."""
        total = LineCounts()
        for module in self.modules:
            total = total + module.lines
        return total

    @property
    def functions(self) -> tuple[FunctionAnalysis, ...]:
        """Toutes les fonctions relevées, tous modules confondus."""
        return tuple(function for module in self.modules for function in module.functions)

    @property
    def class_count(self) -> int:
        """Nombre de classes relevées."""
        return sum(len(module.classes) for module in self.modules)

    @property
    def parse_errors(self) -> tuple[ModuleAnalysis, ...]:
        """Modules qui n'ont pas pu être analysés syntaxiquement."""
        return tuple(module for module in self.modules if module.parse_error is not None)

    @property
    def average_complexity(self) -> float | None:
        """Complexité moyenne des fonctions, ``None`` s'il n'y en a aucune.

        Nulle plutôt que zéro : un dépôt sans fonction analysable n'a pas une
        complexité de zéro, il n'en a pas.
        """
        functions = self.functions
        if not functions:
            return None
        return round(sum(item.complexity for item in functions) / len(functions), 2)

    @property
    def max_complexity(self) -> int:
        """Complexité de la fonction la plus complexe du dépôt."""
        return max((item.complexity for item in self.functions), default=0)

    def most_complex(self, limit: int = 5) -> tuple[FunctionAnalysis, ...]:
        """Les fonctions les plus complexes, de la plus lourde à la plus légère.

        Args:
            limit: nombre de fonctions retenues.
        """
        ranked = sorted(self.functions, key=lambda item: (-item.complexity, item.name))
        return tuple(ranked[:limit])

    @property
    def test_modules(self) -> tuple[ModuleAnalysis, ...]:
        """Modules reconnus comme fichiers de test."""
        return tuple(module for module in self.modules if module.is_test)

    @property
    def source_modules(self) -> tuple[ModuleAnalysis, ...]:
        """Modules qui ne sont pas des fichiers de test."""
        return tuple(module for module in self.modules if not module.is_test)

    def dependencies_in_scope(self, scope: DependencyScope) -> tuple[Dependency, ...]:
        """Dépendances déclarées pour un rôle donné."""
        return tuple(item for item in self.dependencies if item.scope is scope)

    @property
    def undeclared_imports(self) -> tuple[str, ...]:
        """Modules tiers importés que nul manifeste ne déclare.

        Le rapprochement est **indicatif** et non normatif : un paquet
        s'installe souvent sous un nom différent de celui qu'il s'importe
        (``PyYAML`` fournit ``yaml``). Ce qui sort d'ici est une piste à
        vérifier, jamais un manquement établi — c'est pourquoi aucune règle
        n'en tire de constat.
        """
        declared = {_normalised(item.name) for item in self.dependencies}
        return tuple(
            sorted(
                root
                for root in self.imports_of_kind(ImportKind.THIRD_PARTY)
                if _normalised(root) not in declared
            )
        )

    def imports_of_kind(self, kind: ImportKind) -> tuple[str, ...]:
        """Racines des modules importés d'une origine donnée, dédoublonnées et triées."""
        return tuple(
            sorted(
                {
                    item.root
                    for module in self.modules
                    for item in module.imports
                    if item.kind is kind
                }
            )
        )


def _normalised(name: str) -> str:
    """Ramène un nom de paquet à une forme comparable.

    Les écosystèmes tolèrent plusieurs graphies du même paquet : PyPI traite
    ``ruamel.yaml``, ``ruamel-yaml`` et ``Ruamel_YAML`` comme un seul nom.
    """
    return name.strip().lower().replace("_", "-").replace(".", "-")
