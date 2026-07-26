"""
Minimal auth for Phase 1: email/password login, JWT bearer tokens.
Matches PRD Section 8: "simple email/password or magic-link is likely
sufficient for an ~2-week event — full SSO is probably overkill."
"""
import os
import datetime as dt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from .database import get_db
from . import models

SECRET_KEY = os.environ.get("JWT_SECRET", "dev-secret-change-me")
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 12

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(user_id: int) -> str:
    expire = dt.datetime.utcnow() + dt.timedelta(hours=TOKEN_EXPIRE_HOURS)
    return jwt.encode({"sub": str(user_id), "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> models.User:
    creds_exc = HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired token")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload.get("sub"))
    except (JWTError, TypeError, ValueError):
        raise creds_exc
    user = db.get(models.User, user_id)
    if not user or not user.is_active:
        raise creds_exc
    return user


def require_roles(*roles):
    def checker(user: models.User = Depends(get_current_user)) -> models.User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "insufficient permissions")
        return user
    return checker
