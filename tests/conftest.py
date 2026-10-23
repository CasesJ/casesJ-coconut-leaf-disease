"""Offline-only test process: never load deployment credentials or contact Firebase.

These guards are installed before application imports during test collection.
Tests explicitly replace the adapters they exercise with in-memory doubles.
"""
import os
import socket
import sqlite3

import dotenv
import firebase_admin
from firebase_admin import auth, credentials, db, firestore, storage
from google.auth.credentials import AnonymousCredentials
import pytest


def blocked(*args, **kwargs):
    raise AssertionError("External network, Firebase, and historical SQLite access are forbidden in tests")


class OfflineCredential(credentials.Base):
    def get_credential(self):
        return AnonymousCredentials()


_guards = pytest.MonkeyPatch()
_guards.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
for key in ("GOOGLE_APPLICATION_CREDENTIALS", "FIREBASE_SERVICE_ACCOUNT_JSON",
            "FIREBASE_AUTH_EMULATOR_HOST", "FIRESTORE_EMULATOR_HOST", "FIREBASE_STORAGE_EMULATOR_HOST"):
    _guards.delenv(key, raising=False)
_guards.setenv("GCLOUD_PROJECT", "demo-neuralcoco-tests")
_guards.setenv("EXPERT_ACCOUNT_EMAIL", "expert@example.test")
_guards.setenv("EXPERT_ACCOUNT_PASSWORD", "offline-test-password-only")
_guards.setenv("FIREBASE_STORAGE_BUCKET", "demo-neuralcoco-tests.appspot.com")
if not firebase_admin._apps:
    firebase_admin.initialize_app(OfflineCredential(), options={"projectId": "demo-neuralcoco-tests"})
for adapter, method in ((firestore, "client"), (storage, "bucket"), (db, "reference"),
                        (auth, "verify_id_token"), (auth, "create_user"),
                        (auth, "update_user"), (auth, "set_custom_user_claims"),
                        (auth, "get_user_by_email"), (auth, "delete_user")):
    _guards.setattr(adapter, method, blocked)
def local_only(original):
    def connect(sock, address):
        # Windows asyncio implements socketpair using a loopback connection.
        if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
            return original(sock, address)
        return blocked()
    return connect


_guards.setattr(socket.socket, "connect", local_only(socket.socket.connect))
_guards.setattr(socket.socket, "connect_ex", local_only(socket.socket.connect_ex))
_guards.setattr(sqlite3, "connect", blocked)


@pytest.fixture(autouse=True)
def clear_auth_overrides():
    yield
    from app.main import app
    app.dependency_overrides.clear()
