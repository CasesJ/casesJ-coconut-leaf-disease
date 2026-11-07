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


_real_firestore_client = firestore.client


def blocked(*args, **kwargs):
    raise AssertionError("External network, Firebase, and historical SQLite access are forbidden in tests")


class OfflineCredential(credentials.Base):
    def get_credential(self):
        return AnonymousCredentials()


def emulator_only_firestore_client(*args, **kwargs):
    """Permit only the explicitly fenced local Firestore integration tests.

    This is intentionally stricter than merely checking the emulator host: an
    Admin app for the production project cannot obtain a client even if a test
    accidentally sets a local host.
    """
    app = kwargs.get("app")
    if (os.environ.get("FIRESTORE_EMULATOR_HOST") == "127.0.0.1:8080"
            and os.environ.get("GOOGLE_CLOUD_PROJECT") == "demo-coconutai-hybrid-tests"
            and app is not None
            and app.project_id == "demo-coconutai-hybrid-tests"):
        return _real_firestore_client(*args, **kwargs)
    return blocked(*args, **kwargs)


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
for adapter, method in ((storage, "bucket"), (db, "reference"),
                        (auth, "verify_id_token"), (auth, "create_user"),
                        (auth, "update_user"), (auth, "set_custom_user_claims"),
                        (auth, "get_user_by_email"), (auth, "delete_user")):
    _guards.setattr(adapter, method, blocked)
_guards.setattr(firestore, "client", emulator_only_firestore_client)
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
