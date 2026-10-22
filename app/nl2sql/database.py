from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, MetaData, create_engine, event, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class OperationalDatabase:
    """SQLAlchemy unit-of-work boundary for application metadata."""

    def __init__(self, url: str):
        if not url:
            raise ValueError("Operational database URL is required")
        self.url = url
        parsed = make_url(url)
        connect_args: dict[str, object] = {}
        if parsed.get_backend_name() == "sqlite":
            connect_args = {"check_same_thread": False, "timeout": 30}
            database_path = parsed.database
            if database_path and database_path != ":memory:":
                Path(database_path).expanduser().resolve().parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )
        self.engine = create_engine(
            url,
            pool_pre_ping=True,
            connect_args=connect_args,
        )
        if parsed.get_backend_name() == "sqlite":
            event.listen(self.engine, "connect", self._configure_sqlite)
        self.session_factory = sessionmaker(
            bind=self.engine,
            class_=Session,
            autoflush=False,
            expire_on_commit=False,
        )

    @staticmethod
    def _configure_sqlite(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA journal_mode=WAL")
        finally:
            cursor.close()

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def create_schema_for_tests(self) -> None:
        # Importing the mappings here makes this helper reliable even when a test
        # constructs OperationalDatabase before importing any repository module.
        from . import db_models as _db_models  # noqa: F401

        Base.metadata.create_all(self.engine)

    def drop_schema_for_tests(self) -> None:
        from . import db_models as _db_models  # noqa: F401

        Base.metadata.drop_all(self.engine)

    def ping(self) -> bool:
        with self.engine.connect() as connection:
            return connection.execute(text("SELECT 1")).scalar_one() == 1

    def status(self) -> dict[str, object]:
        inspector = inspect(self.engine)
        tables = sorted(inspector.get_table_names())
        revision = None
        if "alembic_version" in tables:
            with self.engine.connect() as connection:
                revision = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one_or_none()
        return {
            "connected": self.ping(),
            "dialect": self.engine.dialect.name,
            "migration_revision": revision,
            "tables": tables,
        }

    def dispose(self) -> None:
        self.engine.dispose()


def database_from_sqlite_path(path: Path) -> OperationalDatabase:
    resolved = path.expanduser().resolve()
    return OperationalDatabase(f"sqlite:///{resolved.as_posix()}")


def get_engine(database: OperationalDatabase) -> Engine:
    return database.engine
