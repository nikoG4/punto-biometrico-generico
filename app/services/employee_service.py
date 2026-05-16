from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_, select

from app.db.models import Employee
from app.db.session import DatabaseManager


class EmployeeService:
    def __init__(self, db: DatabaseManager):
        self.db = db

    def create_employee(self, name: str, document_id: str | None = None, external_id: str | None = None) -> Employee:
        with self.db.primary_session() as session:
            employee = Employee(name=name.strip(), document_id=document_id or None, external_id=external_id or None)
            session.add(employee)
            session.flush()
            return employee

    def create_pending_employee(self) -> Employee:
        suffix = datetime.now().strftime("%Y%m%d%H%M%S")
        return self.create_employee(f"Pendiente de vincular {suffix}")

    def get_or_create_rrhh_employee(
        self, rrhh_id: int, name: str, marker_code: str | None = None, source_prefix: str = "SCT"
    ) -> Employee:
        external_id = f"{source_prefix}:{rrhh_id}"
        legacy_external_id = f"RRHH:{rrhh_id}"
        with self.db.primary_session() as session:
            employee = session.scalar(
                select(Employee).where(Employee.external_id.in_([external_id, legacy_external_id]))
            )
            if employee:
                employee.name = name.strip()
                employee.document_id = marker_code or employee.document_id
                employee.external_id = external_id
                return employee

            if marker_code:
                employee = session.scalar(select(Employee).where(Employee.document_id == marker_code))
                if employee:
                    employee.name = name.strip()
                    employee.external_id = external_id
                    return employee

            employee = Employee(name=name.strip(), document_id=marker_code or None, external_id=external_id)
            session.add(employee)
            session.flush()
            return employee

    def search(self, query: str, limit: int = 20) -> list[Employee]:
        pattern = f"%{query.strip()}%"
        with self.db.primary_session() as session:
            stmt = (
                select(Employee)
                .where(
                    Employee.active.is_(True),
                    or_(
                        Employee.name.like(pattern),
                        Employee.document_id.like(pattern),
                        Employee.external_id.like(pattern),
                    ),
                )
                .order_by(Employee.name)
                .limit(limit)
            )
            return list(session.scalars(stmt))

    def get(self, employee_id: int) -> Employee | None:
        with self.db.primary_session() as session:
            return session.get(Employee, employee_id)
