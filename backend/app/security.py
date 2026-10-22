"""Session authentication and explicit browser write protection."""

import hashlib
import secrets
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select

from app.database import transaction
from app.models import LoginSession, User, now

hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return hasher.hash(password)


def verify_password(encoded: str, password: str) -> bool:
    try:
        return hasher.verify(encoded, password)
    except VerificationError:
        return False


def issue_session(db, user: User) -> tuple[str, str]:
    token = secrets.token_urlsafe(40)
    csrf = secrets.token_hex(24)
    db.add(
        LoginSession(
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            user_id=user.id,
            csrf_token=csrf,
            expires_at=now() + timedelta(hours=12),
        )
    )
    return token, csrf


def principal(request: Request) -> User:
    token = request.cookies.get("studio_session", "")
    digest = hashlib.sha256(token.encode()).hexdigest()
    with transaction() as db:
        session = db.scalar(
            select(LoginSession).where(LoginSession.token_hash == digest, LoginSession.expires_at > now())
        )
        if not session:
            raise HTTPException(401, "Please sign in to continue.")
        user = db.get(User, session.user_id)
        if not user or not user.active:
            raise HTTPException(401, "This account is unavailable.")
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            submitted = request.headers.get("x-csrf-token", "")
            if not secrets.compare_digest(submitted, session.csrf_token):
                raise HTTPException(403, "Invalid request verification token.")
        request.state.session_id = session.id
        request.state.csrf_token = session.csrf_token
        return user


def owner(user: User = Depends(principal)) -> User:
    if user.role != "Owner":
        raise HTTPException(403, "Owner access is required.")
    return user
