from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppConfig
from app.db.models import Base, Device


class DatabaseManager:
    def __init__(self, config: AppConfig):
        self.config = config
        self.remote_engine = self._create_engine(config.mysql_url)
        self.primary_engine = self._create_engine(config.primary_db_url)
        self.local_engine = self._create_engine(config.sqlite_url)
        self.RemoteSession = sessionmaker(bind=self.remote_engine, expire_on_commit=False)
        self.PrimarySession = sessionmaker(bind=self.primary_engine, expire_on_commit=False)
        self.LocalSession = sessionmaker(bind=self.local_engine, expire_on_commit=False)
        self.primary_available = True
        self.init_schema()

    def _create_engine(self, url: str) -> Engine:
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        return create_engine(url, pool_pre_ping=True, future=True, connect_args=connect_args)

    def init_schema(self) -> None:
        Base.metadata.create_all(self.local_engine)
        self._migrate_engine(self.local_engine)
        try:
            Base.metadata.create_all(self.primary_engine)
            self._migrate_engine(self.primary_engine)
            with self.primary_session() as session:
                if not session.get(Device, self.config.device_id):
                    session.add(Device(id=self.config.device_id, location=self.config.device_location))
        except SQLAlchemyError:
            self.primary_available = False
            self.primary_engine = self.local_engine
            self.PrimarySession = self.LocalSession

    def _migrate_engine(self, engine: Engine) -> None:
        try:
            with engine.begin() as conn:
                dialect = engine.dialect.name
                if dialect == "sqlite":
                    columns = conn.execute(text("PRAGMA table_info(face_embeddings)")).mappings().all()
                    names = {row["name"] for row in columns}
                    if "image_snapshot_path" not in names:
                        conn.execute(text("ALTER TABLE face_embeddings ADD COLUMN image_snapshot_path TEXT"))
                elif dialect in {"mysql", "mariadb"}:
                    columns = conn.execute(
                        text(
                            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                            "WHERE TABLE_SCHEMA = DATABASE() "
                            "AND TABLE_NAME = 'face_embeddings' "
                            "AND COLUMN_NAME = 'image_snapshot_path'"
                        )
                    ).all()
                    if not columns:
                        conn.execute(text("ALTER TABLE face_embeddings ADD COLUMN image_snapshot_path TEXT NULL"))
        except SQLAlchemyError:
            pass

    @contextmanager
    def remote_session(self) -> Iterator[Session]:
        session = self.RemoteSession()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @contextmanager
    def primary_session(self) -> Iterator[Session]:
        session = self.PrimarySession()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @contextmanager
    def local_session(self) -> Iterator[Session]:
        session = self.LocalSession()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def can_reach_primary(self) -> bool:
        try:
            with self.remote_engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except SQLAlchemyError:
            return False

    def dispose(self) -> None:
        self.primary_engine.dispose()
        self.local_engine.dispose()
        self.remote_engine.dispose()
