"""Ouverture, création et cycle de vie de la base SQLite.

SQLite désactive les clés étrangères par défaut et ne les applique qu'après un
``PRAGMA foreign_keys = ON`` émis **sur chaque connexion** : ce module s'en
charge, sans quoi les suppressions en cascade déclarées dans le schéma
resteraient lettre morte.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event, inspect
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from githor.errors import StorageError
from githor.logging import get_logger
from githor.storage.tables import Base

logger = get_logger("storage.database")

IN_MEMORY_URL = "sqlite+pysqlite:///:memory:"


def _enable_sqlite_pragmas(connection: Any, _record: Any) -> None:
    """Active les clés étrangères et le journal WAL sur une connexion neuve."""
    cursor = connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA journal_mode = WAL")
    finally:
        cursor.close()


class Database:
    """Accès à la base SQLite de Githor.

    S'utilise de préférence comme gestionnaire de contexte :

    ```python
    with Database(path) as database:
        database.create_schema()
        with database.session() as session:
            session.add(row)
    ```
    """

    def __init__(self, path: Path, *, echo: bool = False) -> None:
        """Ouvre — et crée si nécessaire — la base au chemin indiqué.

        Args:
            path: fichier SQLite ; son répertoire parent est créé au besoin.
            echo: journalise le SQL émis, utile en mode debug.

        Raises:
            StorageError: si le répertoire ou le fichier est inaccessible.
        """
        self.path = path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageError(
                f"Impossible de créer le répertoire de la base {path.parent} : {exc.strerror}"
            ) from exc

        self._engine = self._build_engine(f"sqlite+pysqlite:///{path}", echo=echo)
        logger.debug("Base SQLite ouverte : %s", path)

    @classmethod
    def in_memory(cls, *, echo: bool = False) -> "Database":
        """Crée une base en mémoire, utilisée par les tests.

        La connexion est maintenue ouverte : sans cela, SQLite détruirait la
        base entre deux sessions.
        """
        instance = cls.__new__(cls)
        instance.path = Path(":memory:")
        instance._engine = instance._build_engine(
            IN_MEMORY_URL,
            echo=echo,
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        return instance

    @staticmethod
    def _build_engine(url: str, **options: Any) -> Engine:
        """Construit le moteur et y branche les pragmas SQLite."""
        engine = create_engine(url, **options)
        event.listen(engine, "connect", _enable_sqlite_pragmas)
        return engine

    @property
    def engine(self) -> Engine:
        """Moteur SQLAlchemy sous-jacent."""
        return self._engine

    def create_schema(self) -> None:
        """Crée les tables manquantes. L'opération est idempotente.

        Raises:
            StorageError: si la base est inaccessible ou verrouillée.
        """
        try:
            Base.metadata.create_all(self._engine)
        except SQLAlchemyError as exc:
            raise StorageError(f"Création du schéma impossible dans {self.path} : {exc}") from exc
        logger.debug("Schéma vérifié : %s table(s).", len(Base.metadata.tables))

    def table_names(self) -> list[str]:
        """Retourne les tables réellement présentes dans la base."""
        try:
            return sorted(inspect(self._engine).get_table_names())
        except SQLAlchemyError as exc:
            raise StorageError(f"Base illisible : {self.path} ({exc})") from exc

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Ouvre une session transactionnelle.

        La transaction est validée à la sortie normale du bloc, annulée en cas
        d'exception, et la session est systématiquement fermée.

        Raises:
            StorageError: si l'écriture échoue (base verrouillée, disque plein,
                contrainte violée).
        """
        factory = sessionmaker(bind=self._engine, expire_on_commit=False)
        session = factory()
        try:
            yield session
            session.commit()
        except SQLAlchemyError as exc:
            session.rollback()
            raise StorageError(f"Écriture impossible dans {self.path} : {exc}") from exc
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def close(self) -> None:
        """Libère les connexions du moteur."""
        self._engine.dispose()

    def __enter__(self) -> "Database":
        """Entre dans le gestionnaire de contexte."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Ferme la base en sortie de contexte."""
        self.close()
