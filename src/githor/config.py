"""Chargement et validation de la configuration.

La configuration est un fichier TOML, lu avec ``tomllib`` (bibliothèque
standard). Elle ne contient **jamais** de secret : le token GitHub est lu
exclusivement dans la variable d'environnement ``GITHUB_TOKEN``.

Ordre de recherche du fichier, du plus prioritaire au moins prioritaire :

1. le chemin passé explicitement (option ``--config``) ;
2. la variable d'environnement ``GITHOR_CONFIG`` ;
3. ``config/config.toml`` sous le répertoire de travail ;
4. ``~/.config/githor/config.toml``.

Si aucun fichier n'est trouvé, les valeurs par défaut s'appliquent : Githor
fonctionne sans configuration.

Les chemins relatifs (base SQLite, répertoire d'export, espace de travail des
clones) sont résolus depuis le **répertoire de travail courant**, sans exception
ni cas particulier.
"""

import os
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from githor.errors import ConfigError
from githor.logging import get_logger

logger = get_logger("config")

GITHUB_TOKEN_ENV = "GITHUB_TOKEN"
CONFIG_PATH_ENV = "GITHOR_CONFIG"

PROJECT_CONFIG_PATH = Path("config/config.toml")
USER_CONFIG_PATH = Path.home() / ".config" / "githor" / "config.toml"

_STRICT = ConfigDict(extra="forbid")


class GitHubConfig(BaseModel):
    """Paramètres d'accès à l'API GitHub."""

    model_config = _STRICT

    api_url: str = "https://api.github.com"

    use_gh_cli: bool = True
    """Autorise le repli sur ``gh auth token`` quand ``GITHUB_TOKEN`` est absent."""

    @field_validator("api_url")
    @classmethod
    def _check_api_url(cls, value: str) -> str:
        url = value.strip().rstrip("/")
        if not url.startswith(("http://", "https://")):
            raise ValueError("doit commencer par http:// ou https://")
        return url


class ScanConfig(BaseModel):
    """Périmètre, profondeur et fraîcheur des scans."""

    model_config = _STRICT

    include_forks: bool = False
    include_archived: bool = False
    commit_history_days: int = Field(default=90, ge=1, le=3650)

    snapshot_freshness_hours: int = Field(default=0, ge=0, le=8760)
    """Durée pendant laquelle un snapshot déjà pris dispense d'en reprendre un.

    Un dépôt mesuré il y a moins que cette durée est ignoré par le scan : ni
    appel à GitHub, ni nouveau snapshot. La valeur par défaut, ``0``, ne dispense
    de rien — chaque scan mesure tout, ce qui préserve l'historique. La relever
    économise le quota lors de scans rapprochés.
    """


class AuditConfig(BaseModel):
    """Miroir local et périmètre de l'analyse de code."""

    model_config = _STRICT

    workspace: Path = Path("data/repos")
    """Racine sous laquelle les dépôts sont clonés, un répertoire par propriétaire."""

    clone_depth: int = Field(default=1, ge=0, le=100000)
    """Profondeur du clone superficiel ; ``0`` récupère tout l'historique.

    L'analyse porte sur l'état du code, jamais sur son passé : un seul commit
    suffit. La relever ne sert qu'à qui veut inspecter le miroir à la main.
    """

    git_timeout_seconds: int = Field(default=300, ge=1, le=3600)
    """Délai accordé à chaque commande ``git``, clone compris."""

    max_file_bytes: int = Field(default=1_000_000, ge=1024, le=100_000_000)
    """Au-delà de cette taille, un fichier est compté mais pas analysé.

    Un fichier d'un mégaoctet n'est plus du code écrit à la main : c'est un
    minifié, une donnée embarquée ou un artefact. L'analyser coûterait cher et
    fausserait toutes les moyennes.
    """


class StorageConfig(BaseModel):
    """Emplacement de la base SQLite."""

    model_config = _STRICT

    database: Path = Path("data/githor.db")


class ExportConfig(BaseModel):
    """Emplacement des exports générés."""

    model_config = _STRICT

    directory: Path = Path("data/exports")


class Config(BaseModel):
    """Configuration effective de l'application.

    Ne contient aucun secret : voir :func:`get_github_token`.
    """

    model_config = _STRICT

    github: GitHubConfig = Field(default_factory=GitHubConfig)
    scan: ScanConfig = Field(default_factory=ScanConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    export: ExportConfig = Field(default_factory=ExportConfig)

    source: Path | None = Field(default=None, exclude=True)
    """Fichier dont la configuration est issue, ``None`` si valeurs par défaut."""

    def resolved(self, base_dir: Path) -> "Config":
        """Retourne une copie dont les chemins relatifs sont rendus absolus.

        Args:
            base_dir: répertoire de référence, typiquement le répertoire courant.
        """
        base = base_dir.resolve()
        return self.model_copy(
            update={
                "storage": self.storage.model_copy(
                    update={"database": _absolute(self.storage.database, base)}
                ),
                "export": self.export.model_copy(
                    update={"directory": _absolute(self.export.directory, base)}
                ),
                "audit": self.audit.model_copy(
                    update={"workspace": _absolute(self.audit.workspace, base)}
                ),
            }
        )


def _absolute(path: Path, base: Path) -> Path:
    """Rend ``path`` absolu en le rattachant à ``base`` s'il est relatif."""
    return path if path.is_absolute() else (base / path).resolve()


def find_config_file(base_dir: Path | None = None) -> Path | None:
    """Cherche un fichier de configuration selon l'ordre documenté.

    Args:
        base_dir: répertoire de travail à inspecter ; le répertoire courant par défaut.

    Returns:
        Le premier fichier existant, ou ``None`` si aucun n'est trouvé.
    """
    base = base_dir or Path.cwd()

    candidates: list[Path] = []
    from_env = os.environ.get(CONFIG_PATH_ENV, "").strip()
    if from_env:
        candidates.append(Path(from_env).expanduser())
    candidates.append(base / PROJECT_CONFIG_PATH)
    candidates.append(USER_CONFIG_PATH)

    for candidate in candidates:
        if candidate.is_file():
            logger.debug("Configuration trouvée : %s", candidate)
            return candidate

    logger.debug("Aucun fichier de configuration trouvé, valeurs par défaut utilisées")
    return None


def _read_toml(path: Path) -> dict[str, Any]:
    """Lit un fichier TOML et traduit les échecs en :class:`ConfigError`."""
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Configuration TOML invalide dans {path} : {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"Configuration illisible : {path} ({exc.strerror})") from exc


def _format_validation_error(source: Path | None, exc: ValidationError) -> str:
    """Transforme une erreur Pydantic en message lisible pour l'utilisateur."""
    origin = f"dans {source}" if source else "dans les valeurs par défaut"
    details = "\n".join(
        f"  - {'.'.join(str(part) for part in error['loc']) or '(racine)'} : {error['msg']}"
        for error in exc.errors()
    )
    return f"Configuration invalide {origin} :\n{details}"


def load_config(path: Path | None = None, *, base_dir: Path | None = None) -> Config:
    """Charge la configuration effective.

    Args:
        path: fichier explicite ; s'il est fourni, il doit exister.
        base_dir: répertoire de référence pour la recherche et la résolution
            des chemins relatifs ; le répertoire courant par défaut.

    Raises:
        ConfigError: fichier explicite manquant, TOML invalide ou valeur refusée.
    """
    base = base_dir or Path.cwd()

    if path is not None:
        source: Path | None = path.expanduser()
        if source is not None and not source.is_file():
            raise ConfigError(f"Fichier de configuration introuvable : {path}")
    else:
        source = find_config_file(base)

    data = _read_toml(source) if source is not None else {}

    try:
        config = Config.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(source, exc)) from exc

    return config.model_copy(update={"source": source}).resolved(base)


def get_github_token() -> str | None:
    """Retourne le token de l'environnement, ou ``None`` s'il n'est pas défini.

    Le token n'est jamais journalisé ni persisté. Pour la résolution complète,
    qui sait aussi interroger la CLI ``gh``, voir :mod:`githor.github.token`.
    """
    return os.environ.get(GITHUB_TOKEN_ENV, "").strip() or None
