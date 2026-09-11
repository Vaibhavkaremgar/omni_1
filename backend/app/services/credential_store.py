from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.fernet import Fernet

from app.core.config import get_settings


def _fernet() -> Fernet:
    """Derive a Fernet key from AUTH_SECRET_KEY so no extra env var is needed."""
    secret = get_settings().auth_secret_key.encode()
    # SHA-256 → 32 bytes → URL-safe base64 → valid Fernet key
    key = base64.urlsafe_b64encode(hashlib.sha256(secret).digest())
    return Fernet(key)


def encrypt_credentials(data: dict[str, Any]) -> str:
    return _fernet().encrypt(json.dumps(data).encode()).decode()


def decrypt_credentials(token: str) -> dict[str, Any]:
    return json.loads(_fernet().decrypt(token.encode()))
