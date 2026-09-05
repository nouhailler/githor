"""Reconnaissance des langages et syntaxe de leurs commentaires.

La table est **déclarative** : un langage s'ajoute par une entrée, jamais par
une branche de code. Elle sert deux usages distincts — nommer le langage d'un
fichier, et savoir ce qui, dans ce fichier, est un commentaire.

Aucune détection par contenu : l'extension, et à défaut le nom du fichier,
suffisent. Deviner le langage d'un fichier en le lisant reviendrait à produire
un chiffre qu'on ne saurait pas justifier.
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Language:
    """Un langage et ce qu'il faut savoir pour compter ses lignes."""

    name: str

    line_comments: tuple[str, ...] = ()
    """Préfixes ouvrant un commentaire jusqu'à la fin de la ligne."""

    block_comments: tuple[tuple[str, str], ...] = ()
    """Paires ouvrant/fermant un commentaire multiligne."""

    analysable: bool = False
    """Vrai si Githor sait faire plus que compter les lignes de ce langage."""


PYTHON = Language(
    name="Python",
    line_comments=("#",),
    analysable=True,
)

_C_STYLE = (("/*", "*/"),)

LANGUAGES: dict[str, Language] = {
    ".py": PYTHON,
    ".pyi": PYTHON,
    ".pyw": PYTHON,
    ".js": Language("JavaScript", ("//",), _C_STYLE),
    ".mjs": Language("JavaScript", ("//",), _C_STYLE),
    ".cjs": Language("JavaScript", ("//",), _C_STYLE),
    ".jsx": Language("JavaScript", ("//",), _C_STYLE),
    ".ts": Language("TypeScript", ("//",), _C_STYLE),
    ".tsx": Language("TypeScript", ("//",), _C_STYLE),
    ".mts": Language("TypeScript", ("//",), _C_STYLE),
    ".cts": Language("TypeScript", ("//",), _C_STYLE),
    ".java": Language("Java", ("//",), _C_STYLE),
    ".kt": Language("Kotlin", ("//",), _C_STYLE),
    ".kts": Language("Kotlin", ("//",), _C_STYLE),
    ".scala": Language("Scala", ("//",), _C_STYLE),
    ".swift": Language("Swift", ("//",), _C_STYLE),
    ".go": Language("Go", ("//",), _C_STYLE),
    ".rs": Language("Rust", ("//",), _C_STYLE),
    ".c": Language("C", ("//",), _C_STYLE),
    ".h": Language("C", ("//",), _C_STYLE),
    ".cpp": Language("C++", ("//",), _C_STYLE),
    ".cc": Language("C++", ("//",), _C_STYLE),
    ".cxx": Language("C++", ("//",), _C_STYLE),
    ".hpp": Language("C++", ("//",), _C_STYLE),
    ".cs": Language("C#", ("//",), _C_STYLE),
    ".php": Language("PHP", ("//", "#"), _C_STYLE),
    ".rb": Language("Ruby", ("#",), (("=begin", "=end"),)),
    ".pl": Language("Perl", ("#",)),
    ".pm": Language("Perl", ("#",)),
    ".lua": Language("Lua", ("--",), (("--[[", "]]"),)),
    ".r": Language("R", ("#",)),
    ".jl": Language("Julia", ("#",), (("#=", "=#"),)),
    ".sh": Language("Shell", ("#",)),
    ".bash": Language("Shell", ("#",)),
    ".zsh": Language("Shell", ("#",)),
    ".fish": Language("Shell", ("#",)),
    ".ps1": Language("PowerShell", ("#",), (("<#", "#>"),)),
    ".bat": Language("Batch", ("rem ", "::")),
    ".cmd": Language("Batch", ("rem ", "::")),
    ".sql": Language("SQL", ("--",), _C_STYLE),
    ".html": Language("HTML", (), (("<!--", "-->"),)),
    ".htm": Language("HTML", (), (("<!--", "-->"),)),
    ".vue": Language("Vue", ("//",), (("/*", "*/"), ("<!--", "-->"))),
    ".svelte": Language("Svelte", ("//",), (("/*", "*/"), ("<!--", "-->"))),
    ".xml": Language("XML", (), (("<!--", "-->"),)),
    ".svg": Language("SVG", (), (("<!--", "-->"),)),
    ".css": Language("CSS", (), _C_STYLE),
    ".scss": Language("SCSS", ("//",), _C_STYLE),
    ".sass": Language("Sass", ("//",)),
    ".less": Language("Less", ("//",), _C_STYLE),
    ".yml": Language("YAML", ("#",)),
    ".yaml": Language("YAML", ("#",)),
    ".toml": Language("TOML", ("#",)),
    ".ini": Language("INI", ("#", ";")),
    ".cfg": Language("INI", ("#", ";")),
    ".conf": Language("INI", ("#", ";")),
    ".json": Language("JSON"),
    ".md": Language("Markdown", (), (("<!--", "-->"),)),
    ".markdown": Language("Markdown", (), (("<!--", "-->"),)),
    ".rst": Language("reStructuredText"),
    ".txt": Language("Texte"),
    ".tex": Language("TeX", ("%",)),
    ".vim": Language("Vim script", ('"',)),
    ".el": Language("Emacs Lisp", (";",)),
    ".nix": Language("Nix", ("#",), (("/*", "*/"),)),
    ".tf": Language("Terraform", ("#", "//"), _C_STYLE),
    ".proto": Language("Protocol Buffers", ("//",), _C_STYLE),
    ".gradle": Language("Gradle", ("//",), _C_STYLE),
}
"""Extension, en minuscules, vers le langage correspondant."""

FILENAMES: dict[str, Language] = {
    "dockerfile": Language("Dockerfile", ("#",)),
    "containerfile": Language("Dockerfile", ("#",)),
    "makefile": Language("Makefile", ("#",)),
    "gnumakefile": Language("Makefile", ("#",)),
    "justfile": Language("Just", ("#",)),
    "rakefile": Language("Ruby", ("#",)),
    "gemfile": Language("Ruby", ("#",)),
    "vagrantfile": Language("Ruby", ("#",)),
    "cmakelists.txt": Language("CMake", ("#",)),
}
"""Fichiers reconnus à leur nom, faute d'extension parlante."""

UNKNOWN = Language(name="Autre")
"""Langage attribué à ce qui n'est reconnu ni par extension ni par nom."""


def language_of(path: Path) -> Language:
    """Reconnaît le langage d'un fichier.

    Le nom complet l'emporte sur l'extension : ``Dockerfile.dev`` reste un
    Dockerfile, et un ``Makefile`` n'a pas d'extension du tout.

    Args:
        path: chemin du fichier ; seul son nom est consulté.
    """
    name = path.name.lower()
    if name in FILENAMES:
        return FILENAMES[name]

    suffix = path.suffix.lower()
    if suffix in LANGUAGES:
        return LANGUAGES[suffix]

    # « Dockerfile.dev », « Makefile.local » : le radical porte le sens.
    stem = name.split(".", 1)[0]
    if stem in FILENAMES:
        return FILENAMES[stem]

    return UNKNOWN


def is_analysable(path: Path) -> bool:
    """Dit si Githor sait faire plus que compter les lignes de ce fichier."""
    return language_of(path).analysable
