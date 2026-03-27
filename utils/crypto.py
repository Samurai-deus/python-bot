"""
Fernet symmetric encryption for sensitive values (API keys, secrets).

Usage:
    from utils.crypto import encrypt, decrypt

    token = encrypt("my-secret-api-key")   # returns base64 string
    value = decrypt(token)                  # returns original string

Configuration:
    Set ENCRYPTION_KEY in .env (generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`)

Fail-closed:
    - If ENCRYPTION_KEY is not set → RuntimeError on first encrypt/decrypt call
    - If the token is malformed or was encrypted with a different key → InvalidToken exception
"""
import os
import logging

logger = logging.getLogger(__name__)

_fernet = None


def _get_fernet():
    """Lazy-load Fernet instance. Raises RuntimeError if ENCRYPTION_KEY is missing."""
    global _fernet
    if _fernet is not None:
        return _fernet

    from cryptography.fernet import Fernet

    raw_key = os.environ.get("ENCRYPTION_KEY", "").strip()
    if not raw_key:
        raise RuntimeError(
            "ENCRYPTION_KEY is not set. "
            "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )

    try:
        _fernet = Fernet(raw_key.encode())
    except Exception as e:
        raise RuntimeError(f"ENCRYPTION_KEY is invalid: {e}") from e

    return _fernet


def encrypt(value: str) -> str:
    """
    Encrypt a plaintext string. Returns a URL-safe base64 Fernet token.

    Raises:
        RuntimeError: if ENCRYPTION_KEY is not configured
    """
    token_bytes = _get_fernet().encrypt(value.encode("utf-8"))
    return token_bytes.decode("utf-8")


def decrypt(token: str) -> str:
    """
    Decrypt a Fernet token back to plaintext.

    Raises:
        RuntimeError: if ENCRYPTION_KEY is not configured
        cryptography.fernet.InvalidToken: if token is invalid or key is wrong
    """
    plaintext_bytes = _get_fernet().decrypt(token.encode("utf-8"))
    return plaintext_bytes.decode("utf-8")


def generate_key() -> str:
    """Generate a new Fernet key (for setup / key rotation). Prints to stdout."""
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode("utf-8")


if __name__ == "__main__":
    key = generate_key()
    print(f"New ENCRYPTION_KEY:\n{key}")
    print("\nAdd to your .env file:")
    print(f"ENCRYPTION_KEY={key}")
