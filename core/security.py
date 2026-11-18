"""
Security and encryption utilities (Fernet for token encryption).
"""
import base64
import logging
from typing import Optional
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

logger = logging.getLogger(__name__)


def get_fernet() -> Optional[Fernet]:
    """Return a Fernet cipher instance if ENCRYPTION_KEY is configured."""
    key = getattr(settings, "ENCRYPTION_KEY", None)
    if not key:
        return None
    try:
        # Key must be 32 url-safe base64-encoded bytes
        if isinstance(key, str):
            key = key.encode("utf-8")
        return Fernet(key)
    except Exception as e:
        logger.error("Failed to initialize Fernet cipher: %s", e)
        return None


def encrypt_token(token: Optional[str]) -> Optional[str]:
    """Encrypt a plaintext token using Fernet."""
    if token is None or token == "":
        return token
    cipher = get_fernet()
    if cipher is None:
        raise ValueError("ENCRYPTION_KEY is not configured or invalid in settings.")
    encrypted_bytes = cipher.encrypt(token.encode("utf-8"))
    return encrypted_bytes.decode("utf-8")


def decrypt_token(token_enc: Optional[str]) -> Optional[str]:
    """Decrypt an encrypted token using Fernet."""
    if token_enc is None or token_enc == "":
        return token_enc
    cipher = get_fernet()
    if cipher is None:
        raise ValueError("ENCRYPTION_KEY is not configured or invalid in settings.")
    try:
        decrypted_bytes = cipher.decrypt(token_enc.encode("utf-8"))
        return decrypted_bytes.decode("utf-8")
    except InvalidToken:
        logger.error("Failed to decrypt token: InvalidToken or wrong key.")
        raise ValueError("Failed to decrypt token: invalid token or wrong encryption key.")
