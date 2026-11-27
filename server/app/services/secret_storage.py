"""Authenticated app-setting secrets. Plaintext is accepted only by explicit writes/migration."""
from __future__ import annotations

import json
import os
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException

KEY_ENV = "CAREERPILOT_SECRETS_KEY"
VERSION = "fernet-v1"
_FAMILIES = frozenset({"llm", "github", "gitcode"})


class SecretStorageError(HTTPException):
    def __init__(self):
        super().__init__(503, "Credential storage unavailable. Check deployment key and offline migration.")


def credential_setting(key: str) -> bool:
    return key.split(":", 1)[0] in _FAMILIES


def _cipher() -> Fernet:
    try:
        return Fernet(os.environ[KEY_ENV].encode("ascii"))
    except (KeyError, ValueError, UnicodeError):
        raise SecretStorageError() from None


def _secret_field(name: str) -> bool:
    name = name.lower()
    return name in {"token", "password", "secret", "api_key", "authorization"} or name.endswith(
        ("_api_key", "_token", "_password", "_secret"))


def _context(key: str, path: tuple) -> list:
    return ["app_settings", key, *path]


def _decrypt(key: str, path: tuple, value: dict) -> str:
    if set(value) != {"$secret", "ciphertext"} or value["$secret"] != VERSION:
        raise SecretStorageError()
    try:
        payload = json.loads(_cipher().decrypt(value["ciphertext"].encode("ascii")))
        if (not isinstance(payload, dict) or set(payload) != {"context", "secret"}
                or payload["context"] != _context(key, path) or not isinstance(payload["secret"], str)):
            raise SecretStorageError()
        return payload["secret"]
    except (InvalidToken, ValueError, TypeError, AttributeError, UnicodeError):
        raise SecretStorageError() from None


def _walk(key: str, value: Any, path: tuple, *, seal: bool, secret: bool = False) -> Any:
    if isinstance(value, dict) and "$secret" in value:
        plaintext = _decrypt(key, path, value)
        return dict(value) if seal else plaintext
    if secret:
        if value is None or value == "":
            return value
        if not isinstance(value, str) or not seal:
            raise SecretStorageError()
        payload = json.dumps({"context": _context(key, path), "secret": value}, ensure_ascii=False).encode()
        return {"$secret": VERSION, "ciphertext": _cipher().encrypt(payload).decode("ascii")}
    if isinstance(value, dict):
        return {name: _walk(key, item, (*path, name), seal=seal, secret=_secret_field(name))
                for name, item in value.items()}
    if isinstance(value, list):
        return [_walk(key, item, (*path, index), seal=seal) for index, item in enumerate(value)]
    return value


def seal_setting(key: str, value: dict) -> dict:
    """Encrypt explicit input; validate and retain existing envelopes unchanged."""
    if not credential_setting(key):
        return value
    if not isinstance(value, dict):
        raise SecretStorageError()
    return _walk(key, value, (), seal=True)


def open_setting(key: str, value: dict) -> dict:
    """Read an online setting. Nonempty legacy plaintext is an error, never a fallback."""
    if not credential_setting(key):
        return value
    if not isinstance(value, dict):
        raise SecretStorageError()
    return _walk(key, value, (), seal=False)
