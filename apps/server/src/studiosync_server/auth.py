import hashlib
import secrets
import time

from sqlalchemy.orm import Session

from .models import Credential, User


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def password_hash(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    value = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
    return salt + ":" + value.hex()


def verify_password(password: str, stored: str) -> bool:
    return secrets.compare_digest(password_hash(password, stored.split(":")[0]), stored)


def issue_token(db: Session, user: User) -> dict:
    token = secrets.token_urlsafe(32)
    expires = time.time() + 8 * 3600
    db.add(Credential(digest=digest(token), user_id=user.id, expires_at=expires))
    return {"token": token, "user_id": user.id, "expires_at": expires}
