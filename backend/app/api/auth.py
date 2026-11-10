from __future__ import annotations

import time
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import audit, current_user, require
from app.api.schemas import LoginIn, TokenOut, UserIn, UserOut
from app.core.db import get_db
from app.core.security import create_token, hash_password, verify_password
from app.models import AuditLog, Role, User

router = APIRouter(prefix="/api/auth", tags=["auth"])

_attempts: dict[str, list[float]] = defaultdict(list)
MAX_ATTEMPTS, WINDOW_S = 5, 300


@router.post("/login", response_model=TokenOut)
def login(data: LoginIn, request: Request, db: Session = Depends(get_db)):
    key = f"{request.client.host if request.client else '?'}|{data.email.lower()}"
    now = time.time()
    _attempts[key] = [t for t in _attempts[key] if now - t < WINDOW_S]
    if len(_attempts[key]) >= MAX_ATTEMPTS:
        raise HTTPException(429, "Demasiados intentos. Esperá unos minutos.")
    user = db.scalar(select(User).where(User.email == data.email.lower()))
    if not user or not user.is_active or not verify_password(data.password, user.password_hash):
        _attempts[key].append(now)
        db.add(AuditLog(actor=data.email.lower()[:190], action="login_failed"))
        db.commit()
        raise HTTPException(401, "Credenciales inválidas")
    _attempts.pop(key, None)
    db.add(AuditLog(actor=user.email, action="login"))
    db.commit()
    return TokenOut(access_token=create_token(user.id, user.role.name), role=user.role.name, email=user.email)


@router.get("/me")
def me(user: User = Depends(current_user)):
    return {"id": user.id, "email": user.email, "full_name": user.full_name, "role": user.role.name}


@router.get("/users", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _: User = Depends(require("admin"))):
    return list(db.scalars(select(User).order_by(User.id)))


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(data: UserIn, db: Session = Depends(get_db), admin: User = Depends(require("admin"))):
    if db.scalar(select(User).where(User.email == data.email.lower())):
        raise HTTPException(409, "El usuario ya existe")
    role = db.scalar(select(Role).where(Role.name == data.role))
    user = User(email=data.email.lower(), full_name=data.full_name, password_hash=hash_password(data.password), role_id=role.id)
    db.add(user)
    audit(db, admin, "user_create", data.email.lower(), role=data.role)
    db.commit()
    return user
