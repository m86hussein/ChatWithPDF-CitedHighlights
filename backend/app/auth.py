import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .db import User, get_db

router = APIRouter(prefix="/api/auth", tags=["auth"])

COOKIE = "session"
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SCRYPT = dict(n=2**14, r=8, p=1, dklen=64)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), **_SCRYPT)
    return hmac.compare_digest(digest.hex(), digest_hex)


# Equalise login timing for unknown emails so accounts can't be enumerated.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(12))


def _issue(resp: Response, user: User) -> None:
    exp = datetime.now(timezone.utc) + timedelta(hours=settings.jwt_ttl_hours)
    token = jwt.encode({"sub": user.id, "exp": exp}, settings.jwt_secret, algorithm="HS256")
    resp.set_cookie(
        COOKIE, token, httponly=True, secure=settings.cookie_secure, samesite="lax",
        max_age=settings.jwt_ttl_hours * 3600, path="/",
    )


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "Not authenticated")
    try:
        sub = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])["sub"]
    except jwt.PyJWTError:
        raise HTTPException(401, "Session expired")
    user = db.get(User, sub)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


class Credentials(BaseModel):
    email: str
    password: str
    name: str = ""


def _user_out(u: User) -> dict:
    return {"id": u.id, "email": u.email, "name": u.name}


@router.post("/register")
def register(body: Credentials, resp: Response, db: Session = Depends(get_db)):
    if not settings.allow_registration:
        raise HTTPException(403, "Registration is disabled")
    email = body.email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(400, "Invalid email")
    if len(body.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "Email already registered")
    user = User(email=email, name=body.name.strip()[:200], password_hash=hash_password(body.password))
    db.add(user)
    db.commit()
    _issue(resp, user)
    return _user_out(user)


@router.post("/login")
def login(body: Credentials, resp: Response, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.strip().lower()))
    ok = verify_password(body.password, user.password_hash if user else _DUMMY_HASH)
    if not user or not ok:
        raise HTTPException(401, "Invalid email or password")
    _issue(resp, user)
    return _user_out(user)


@router.post("/logout")
def logout(resp: Response):
    resp.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return _user_out(user)
