from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select, update, delete, func
from sqlalchemy.orm import Session

from backend.core.models.users import User


class UsersRepoDB:
    def __init__(self, session: Session):
        self.session = session

    # Unified interface
    def create(self, data: Dict[str, Any]) -> User:
        user = User(**data)
        self.session.add(user)
        self.session.flush()
        return user

    def get(self, user_id: int) -> Optional[User]:
        return self.session.get(User, user_id)

    def get_by_email(self, email: str) -> Optional[User]:
        stmt = select(User).where(User.email == email)
        return self.session.execute(stmt).scalar_one_or_none()

    def list(
        self,
        *,
        email: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Tuple[List[User], int]:
        q = select(User)
        if email:
            q = q.where(User.email.ilike(f"%{email}%"))
        if status:
            q = q.where(User.status == status)
        cq = select(func.count()).select_from(q.subquery())
        total = self.session.execute(cq).scalar_one()
        q = q.order_by(User.created_at.desc()).limit(limit).offset(offset)
        rows = self.session.execute(q).scalars().all()
        return rows, total

    def update(self, user_id: int, data: Dict[str, Any]) -> Optional[User]:
        stmt = (
            update(User)
            .where(User.id == user_id)
            .values(**data)
            .execution_options(synchronize_session="fetch")
        )
        self.session.execute(stmt)
        return self.get(user_id)

    def delete(self, user_id: int, *, hard: bool = False) -> bool:
        if hard:
            stmt = delete(User).where(User.id == user_id)
            res = self.session.execute(stmt)
            return res.rowcount > 0
        else:
            self.update(user_id, {"status": "suspended"})
            return True

    def update_password(self, user_id: int, password_hash: str, algo: str) -> None:
        stmt = (
            update(User)
            .where(User.id == user_id)
            .values(password_hash=password_hash, password_algo=algo, password_updated_at=func.now())
        )
        self.session.execute(stmt)
