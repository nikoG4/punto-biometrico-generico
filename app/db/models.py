from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, Float, ForeignKey, LargeBinary, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class AttendanceType(str, Enum):
    IN = "IN"
    OUT = "OUT"


class Employee(Base):
    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    document_id: Mapped[str | None] = mapped_column(String(80), unique=True)
    external_id: Mapped[str | None] = mapped_column(String(80), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)

    embeddings: Mapped[list["FaceEmbedding"]] = relationship(back_populates="employee")
    attendance_logs: Mapped[list["AttendanceLog"]] = relationship(back_populates="employee")


class FaceEmbedding(Base):
    __tablename__ = "face_embeddings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), nullable=False, index=True)
    embedding: Mapped[bytes] = mapped_column(LargeBinary(length=(4 * 1024 * 1024)), nullable=False)
    dimension: Mapped[int] = mapped_column(nullable=False, default=512)
    provider: Mapped[str] = mapped_column(String(40), nullable=False, default="insightface")
    image_snapshot_path: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)

    employee: Mapped[Employee] = relationship(back_populates="embeddings")


class AttendanceLog(Base):
    __tablename__ = "attendance_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True, nullable=False)
    type: Mapped[AttendanceType] = mapped_column(SAEnum(AttendanceType), nullable=False)
    device_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    image_snapshot_path: Mapped[str | None] = mapped_column(Text)
    synced: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    employee: Mapped[Employee] = relationship(back_populates="attendance_logs")


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    location: Mapped[str | None] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)


class OfflineEvent(Base):
    __tablename__ = "offline_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime)
