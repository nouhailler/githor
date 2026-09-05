"""Schéma SQLite : tables, clés étrangères et index.

Deux principes gouvernent ce schéma :

- **un repository n'est pas son état**. ``repositories`` décrit le projet ;
  ``repository_snapshots`` décrit ce qu'il était à une date donnée. Un nouveau
  scan ajoute un snapshot, il n'écrase jamais le précédent ;
- **les données rattachées à un instant appartiennent au snapshot**. Langages
  et fichiers pendent du snapshot, pas du repository, afin que leur évolution
  reste lisible. Commits, releases et issues pendent du repository : ce sont
  des faits datés, qui ne se réécrivent pas d'un scan à l'autre.

L'analyse de code de la V0.2 suit la même règle : ``code_audits`` est une mesure
datée de plus, et tout ce qui en découle en pend. Elle n'ajoute **que des tables
neuves** et ne touche à aucune colonne existante — c'est la seule évolution que
``create_all`` sache appliquer à une base déjà créée, et donc la seule possible
tant qu'aucun outil de migration n'est en place.

L'audit pend du repository et non du snapshot : il se lit sur un clone local et
n'exige aucun appel à GitHub. L'y rattacher obligerait à scanner avant
d'analyser, alors que rien ne le demande.

Ce qui est écrit ici, ce sont des **faits** : un module, une fonction, un
import, une dépendance déclarée. Aucune métrique agrégée n'est stockée — elles
se recalculent à la lecture, faute de quoi un chiffre finirait par diverger de
sa source. Les seuls décomptes conservés sont ceux dont rien ne porte trace
ailleurs : les fichiers binaires et les fichiers trop gros n'ont pas de ligne à
eux, et ne pourraient donc pas être recomptés.
"""

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Dialect,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class UTCDateTime(TypeDecorator[datetime]):
    """Type date/heure garantissant l'UTC à l'écriture comme à la lecture.

    SQLite ne conserve pas le fuseau : une date consciente écrite telle quelle
    reviendrait naïve, et toute comparaison d'historique deviendrait fausse
    selon le fuseau de la machine. Ce type convertit en UTC avant écriture et
    rattache l'UTC à la lecture.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """Normalise en UTC naïf avant écriture."""
        if value is None:
            return None
        if value.tzinfo is None:
            return value
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """Rattache l'UTC à la lecture."""
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    """Base déclarative commune à toutes les tables."""


class RepositoryRow(Base):
    """Le projet lui-même : ce qui ne change pas d'un scan à l'autre."""

    __tablename__ = "repositories"

    id: Mapped[int] = mapped_column(primary_key=True)

    github_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    """Identifiant GitHub : seule identité qui survit à un renommage."""

    full_name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    owner: Mapped[str] = mapped_column(String(255), index=True)
    description: Mapped[str | None] = mapped_column(Text)

    url: Mapped[str] = mapped_column(String(512))
    default_branch: Mapped[str] = mapped_column(String(255), default="main")
    visibility: Mapped[str] = mapped_column(String(32), default="public", index=True)

    created_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    pushed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)

    archived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    fork: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    snapshots: Mapped[list["RepositorySnapshotRow"]] = relationship(
        back_populates="repository", cascade="all, delete-orphan", passive_deletes=True
    )
    commits: Mapped[list["CommitRow"]] = relationship(
        back_populates="repository", cascade="all, delete-orphan", passive_deletes=True
    )
    releases: Mapped[list["ReleaseRow"]] = relationship(
        back_populates="repository", cascade="all, delete-orphan", passive_deletes=True
    )
    issues: Mapped[list["IssueRow"]] = relationship(
        back_populates="repository", cascade="all, delete-orphan", passive_deletes=True
    )
    code_audits: Mapped[list["CodeAuditRow"]] = relationship(
        back_populates="repository", cascade="all, delete-orphan", passive_deletes=True
    )


class RepositorySnapshotRow(Base):
    """État mesuré d'un repository à une date donnée."""

    __tablename__ = "repository_snapshots"
    __table_args__ = (Index("ix_snapshots_repository_collected", "repository_id", "collected_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    collected_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)

    stars: Mapped[int] = mapped_column(Integer, default=0)
    forks: Mapped[int] = mapped_column(Integer, default=0)
    watchers: Mapped[int] = mapped_column(Integer, default=0)

    open_issues: Mapped[int] = mapped_column(Integer, default=0)
    open_prs: Mapped[int | None] = mapped_column(Integer)
    """Nul tant que les pull requests n'ont pas été comptées séparément."""

    size_kb: Mapped[int] = mapped_column(Integer, default=0)
    primary_language: Mapped[str | None] = mapped_column(String(64))
    default_branch: Mapped[str | None] = mapped_column(String(255))

    repository: Mapped[RepositoryRow] = relationship(back_populates="snapshots")
    languages: Mapped[list["LanguageRow"]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan", passive_deletes=True
    )
    files: Mapped[list["RepositoryFileRow"]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan", passive_deletes=True
    )


class LanguageRow(Base):
    """Part d'un langage dans un repository, à la date du snapshot."""

    __tablename__ = "languages"
    __table_args__ = (UniqueConstraint("snapshot_id", "language", name="uq_language_per_snapshot"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("repository_snapshots.id", ondelete="CASCADE"), index=True
    )

    language: Mapped[str] = mapped_column(String(64), index=True)
    bytes: Mapped[int] = mapped_column(Integer, default=0)
    """Valeur brute renvoyée par GitHub, conservée telle quelle."""

    percentage: Mapped[float] = mapped_column(default=0.0)
    """Part calculée, conservée pour éviter de la recalculer à chaque lecture."""

    snapshot: Mapped[RepositorySnapshotRow] = relationship(back_populates="languages")


class RepositoryFileRow(Base):
    """Entrée d'arborescence relevée lors d'un snapshot."""

    __tablename__ = "repository_files"
    __table_args__ = (UniqueConstraint("snapshot_id", "path", name="uq_file_per_snapshot"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("repository_snapshots.id", ondelete="CASCADE"), index=True
    )

    path: Mapped[str] = mapped_column(String(1024), index=True)
    type: Mapped[str] = mapped_column(String(16))
    """``blob`` pour un fichier, ``tree`` pour un répertoire."""

    size: Mapped[int | None] = mapped_column(Integer)

    snapshot: Mapped[RepositorySnapshotRow] = relationship(back_populates="files")


class CommitRow(Base):
    """Commit relevé dans la fenêtre d'historique configurée."""

    __tablename__ = "commits"
    __table_args__ = (UniqueConstraint("repository_id", "sha", name="uq_commit_per_repository"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )

    sha: Mapped[str] = mapped_column(String(40), index=True)
    author: Mapped[str | None] = mapped_column(String(255))
    message: Mapped[str | None] = mapped_column(Text)
    committed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)

    repository: Mapped[RepositoryRow] = relationship(back_populates="commits")


class ReleaseRow(Base):
    """Release publiée sur GitHub."""

    __tablename__ = "releases"
    __table_args__ = (UniqueConstraint("repository_id", "tag", name="uq_release_per_repository"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )

    tag: Mapped[str] = mapped_column(String(255), index=True)
    name: Mapped[str | None] = mapped_column(String(512))
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    draft: Mapped[bool] = mapped_column(Boolean, default=False)
    prerelease: Mapped[bool] = mapped_column(Boolean, default=False)

    repository: Mapped[RepositoryRow] = relationship(back_populates="releases")


class IssueRow(Base):
    """Issue GitHub, ouverte ou fermée."""

    __tablename__ = "issues"
    __table_args__ = (UniqueConstraint("repository_id", "number", name="uq_issue_per_repository"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )

    github_id: Mapped[int] = mapped_column(Integer, index=True)
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(32), index=True)

    created_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    repository: Mapped[RepositoryRow] = relationship(back_populates="issues")


class FindingRow(Base):
    """Constat produit par une règle, rattaché au snapshot qui l'a motivé.

    Un finding doit toujours pouvoir expliquer pourquoi il existe : ``rule``
    nomme la règle appliquée, ``message`` décrit le fait constaté.
    """

    __tablename__ = "findings"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "rule", name="uq_finding_per_snapshot"),
        Index("ix_findings_rule_severity", "rule", "severity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("repository_snapshots.id", ondelete="CASCADE"), index=True
    )

    category: Mapped[str] = mapped_column(String(64), index=True)
    rule: Mapped[str] = mapped_column(String(128), index=True)
    severity: Mapped[str] = mapped_column(String(16), index=True)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)

    message: Mapped[str] = mapped_column(Text)
    recommendation: Mapped[str | None] = mapped_column(Text)


class CodeAuditRow(Base):
    """Analyse locale du code d'un repository, à une date et sur un commit donnés.

    Comme un snapshot, un audit s'**ajoute** : deux analyses successives se
    comparent, elles ne se remplacent pas.
    """

    __tablename__ = "code_audits"
    __table_args__ = (Index("ix_code_audits_repository_analysed", "repository_id", "analysed_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )

    analysed_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    commit: Mapped[str] = mapped_column(String(40), index=True)
    """SHA analysé : sans lui, la mesure ne serait rattachable à aucun état."""

    branch: Mapped[str] = mapped_column(String(255))

    files_seen: Mapped[int] = mapped_column(Integer, default=0)
    files_binary: Mapped[int] = mapped_column(Integer, default=0)
    files_too_large: Mapped[int] = mapped_column(Integer, default=0)
    """Fichiers écartés, conservés ici faute d'avoir une ligne à eux."""

    repository: Mapped[RepositoryRow] = relationship(back_populates="code_audits")
    modules: Mapped[list["CodeModuleRow"]] = relationship(
        back_populates="audit", cascade="all, delete-orphan", passive_deletes=True
    )
    dependencies: Mapped[list["CodeDependencyRow"]] = relationship(
        back_populates="audit", cascade="all, delete-orphan", passive_deletes=True
    )


class CodeModuleRow(Base):
    """Fichier analysé lors d'un audit."""

    __tablename__ = "code_modules"
    __table_args__ = (
        UniqueConstraint("audit_id", "path", name="uq_module_per_audit"),
        Index("ix_code_modules_audit_language", "audit_id", "language"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    audit_id: Mapped[int] = mapped_column(
        ForeignKey("code_audits.id", ondelete="CASCADE"), index=True
    )

    path: Mapped[str] = mapped_column(String(1024), index=True)
    language: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)

    lines_total: Mapped[int] = mapped_column(Integer, default=0)
    lines_code: Mapped[int] = mapped_column(Integer, default=0)
    lines_comment: Mapped[int] = mapped_column(Integer, default=0)
    lines_blank: Mapped[int] = mapped_column(Integer, default=0)

    is_test: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    has_docstring: Mapped[bool] = mapped_column(Boolean, default=False)
    parse_error: Mapped[str | None] = mapped_column(Text)
    """Motif de l'échec d'analyse syntaxique ; nul quand le fichier a été compris."""

    audit: Mapped[CodeAuditRow] = relationship(back_populates="modules")
    functions: Mapped[list["CodeFunctionRow"]] = relationship(
        back_populates="module", cascade="all, delete-orphan", passive_deletes=True
    )
    classes: Mapped[list["CodeClassRow"]] = relationship(
        back_populates="module", cascade="all, delete-orphan", passive_deletes=True
    )
    imports: Mapped[list["CodeImportRow"]] = relationship(
        back_populates="module", cascade="all, delete-orphan", passive_deletes=True
    )


class CodeFunctionRow(Base):
    """Fonction ou méthode relevée dans un module."""

    __tablename__ = "code_functions"
    __table_args__ = (Index("ix_code_functions_module_complexity", "module_id", "complexity"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    module_id: Mapped[int] = mapped_column(
        ForeignKey("code_modules.id", ondelete="CASCADE"), index=True
    )

    name: Mapped[str] = mapped_column(String(512), index=True)
    """Nom qualifié dans le module : ``Classe.méthode`` pour une méthode."""

    line: Mapped[int] = mapped_column(Integer, default=0)
    complexity: Mapped[int] = mapped_column(Integer, default=1, index=True)
    arguments: Mapped[int] = mapped_column(Integer, default=0)
    is_async: Mapped[bool] = mapped_column(Boolean, default=False)
    has_docstring: Mapped[bool] = mapped_column(Boolean, default=False)

    module: Mapped[CodeModuleRow] = relationship(back_populates="functions")


class CodeClassRow(Base):
    """Classe relevée dans un module."""

    __tablename__ = "code_classes"

    id: Mapped[int] = mapped_column(primary_key=True)
    module_id: Mapped[int] = mapped_column(
        ForeignKey("code_modules.id", ondelete="CASCADE"), index=True
    )

    name: Mapped[str] = mapped_column(String(512), index=True)
    line: Mapped[int] = mapped_column(Integer, default=0)
    methods: Mapped[int] = mapped_column(Integer, default=0)
    has_docstring: Mapped[bool] = mapped_column(Boolean, default=False)

    module: Mapped[CodeModuleRow] = relationship(back_populates="classes")


class CodeImportRow(Base):
    """Import relevé dans un module."""

    __tablename__ = "code_imports"
    __table_args__ = (Index("ix_code_imports_module_kind", "module_id", "kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    module_id: Mapped[int] = mapped_column(
        ForeignKey("code_modules.id", ondelete="CASCADE"), index=True
    )

    module_name: Mapped[str] = mapped_column(String(512), index=True)
    """Module importé, en notation pointée."""

    root: Mapped[str] = mapped_column(String(255), index=True)
    """Premier segment : ce qui, pour une dépendance, s'installe."""

    kind: Mapped[str] = mapped_column(String(16), index=True)
    line: Mapped[int] = mapped_column(Integer, default=0)

    module: Mapped[CodeModuleRow] = relationship(back_populates="imports")


class CodeDependencyRow(Base):
    """Dépendance déclarée dans un manifeste du dépôt.

    Déclarée, et non installée ni importée : les trois sont distinctes, et
    c'est leur écart qui renseigne.
    """

    __tablename__ = "code_dependencies"
    __table_args__ = (
        UniqueConstraint(
            "audit_id", "ecosystem", "name", "scope", "source", name="uq_dependency_per_audit"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    audit_id: Mapped[int] = mapped_column(
        ForeignKey("code_audits.id", ondelete="CASCADE"), index=True
    )

    name: Mapped[str] = mapped_column(String(255), index=True)
    ecosystem: Mapped[str] = mapped_column(String(32), index=True)
    scope: Mapped[str] = mapped_column(String(16), index=True)
    specifier: Mapped[str | None] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(512))
    """Manifeste qui l'a déclarée."""

    group: Mapped[str | None] = mapped_column(String(128))

    audit: Mapped[CodeAuditRow] = relationship(back_populates="dependencies")
