#!/usr/bin/env python3
"""
Generate secrets for Shorts Auto-Poster:
- DJANGO_SECRET_KEY
- ENCRYPTION_KEY (Fernet key)
- ADMIN_PASSWORD_HASH (bcrypt)
"""
import getpass
import secrets
import sys
import base64
import os

try:
    import bcrypt
except ImportError:
    bcrypt = None

try:
    from cryptography.fernet import Fernet
except ImportError:
    Fernet = None


def generate_django_secret_key() -> str:
    return secrets.token_urlsafe(50)


def generate_encryption_key() -> str:
    if Fernet is not None:
        return Fernet.generate_key().decode()
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


def generate_password_hash(password: str) -> str:
    if bcrypt is not None:
        salt = bcrypt.gensalt(rounds=12)
        return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")
    import hashlib
    # Fallback sha256 with warning if bcrypt not yet installed
    print("WARNING: bcrypt not installed, using sha256 placeholder", file=sys.stderr)
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def main():
    print("=" * 60)
    print("Shorts Auto-Poster: Secrets Generator")
    print("=" * 60)
    
    django_secret = generate_django_secret_key()
    encryption_key = generate_encryption_key()
    
    password = ""
    if len(sys.argv) > 1:
        password = sys.argv[1]
    else:
        try:
            password = getpass.getpass("Enter Admin Password (leave blank for random): ").strip()
        except (EOFError, KeyboardInterrupt):
            pass
            
    if not password:
        password = secrets.token_urlsafe(16)
        print(f"Generated Random Admin Password: {password}")
        
    pw_hash = generate_password_hash(password)
    
    print("\nCopy these values to your .env file or Render environment variables:\n")
    print(f"DJANGO_SECRET_KEY={django_secret}")
    print(f"ENCRYPTION_KEY={encryption_key}")
    print(f"ADMIN_PASSWORD_HASH={pw_hash}")
    print("=" * 60)


if __name__ == "__main__":
    main()
