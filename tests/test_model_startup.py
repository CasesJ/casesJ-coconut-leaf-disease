"""Startup initializes the existing singleton before accepting requests."""
import asyncio
from threading import get_ident
from types import SimpleNamespace

import pytest

from app import main


def setup_startup(monkeypatch, load):
    async def connect():
        return True

    monkeypatch.setattr(main, 'importlib', SimpleNamespace(import_module=load))
    monkeypatch.setattr('drone_gps.init_drone_gps', lambda **_: SimpleNamespace(connect=connect))
    monkeypatch.setattr('drone_gps.get_drone_gps', lambda: None)


def test_model_and_account_initialization_run_before_serving_and_off_event_loop(monkeypatch):
    main_thread = get_ident()
    events = []

    def load(name):
        assert get_ident() != main_thread
        assert name == 'model'
        events.append('model')

    def account(*args):
        assert get_ident() != main_thread
        events.append('account')
        return SimpleNamespace(email='expert@example.test'), False

    setup_startup(monkeypatch, load)
    monkeypatch.setattr('app.infrastructure.firebase.auth.ensure_user_account', account)

    async def exercise():
        async with main.lifespan(main.app):
            assert events == ['model', 'account']

    asyncio.run(exercise())


def test_missing_model_stops_startup_before_account_mutation(monkeypatch):
    def load(name):
        raise FileNotFoundError('missing test weights')

    setup_startup(monkeypatch, load)
    monkeypatch.setattr('app.infrastructure.firebase.auth.ensure_user_account',
                        lambda *_: pytest.fail('Account must not change after model startup failure'))

    async def exercise():
        async with main.lifespan(main.app):
            pytest.fail('Missing model must not accept requests')

    with pytest.raises(FileNotFoundError):
        asyncio.run(exercise())


def test_separate_global_hybrid_deployment_skips_render_model_startup(monkeypatch):
    setup_startup(monkeypatch, lambda *_: pytest.fail("Render must not load local model in global hybrid mode"))
    monkeypatch.setattr(main, "INFERENCE_DEPLOYMENT_MODE", "hybrid")
    monkeypatch.setattr("app.infrastructure.firebase.auth.ensure_user_account",
                        lambda *_args: (SimpleNamespace(email="expert@example.test"), False))

    async def exercise():
        async with main.lifespan(main.app):
            pass

    asyncio.run(exercise())
