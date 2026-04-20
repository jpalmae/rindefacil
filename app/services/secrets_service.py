import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app


def _encryption_secret():
    secret = (current_app.config.get('SETTINGS_ENCRYPTION_KEY') or '').strip()
    return secret or None


def _fernet():
    secret = _encryption_secret()
    if not secret:
        return None
    key_material = hashlib.sha256(secret.encode('utf-8')).digest()
    return Fernet(base64.urlsafe_b64encode(key_material))


def can_encrypt_settings():
    return _fernet() is not None


def encrypt_setting(value):
    if value is None:
        return None
    if isinstance(value, str) and value.startswith('enc:'):
        return value

    fernet = _fernet()
    if fernet is None:
        raise RuntimeError('SETTINGS_ENCRYPTION_KEY is not configured.')

    encrypted = fernet.encrypt(str(value).encode('utf-8')).decode('utf-8')
    return f'enc:{encrypted}'


def decrypt_setting(value):
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    if not value.startswith('enc:'):
        return value

    fernet = _fernet()
    if fernet is None:
        raise RuntimeError('SETTINGS_ENCRYPTION_KEY is not configured.')

    token = value[4:].encode('utf-8')
    try:
        return fernet.decrypt(token).decode('utf-8')
    except InvalidToken as exc:
        raise RuntimeError('Encrypted setting could not be decrypted.') from exc
