import pytest


@pytest.fixture(autouse=True)
def _isolate_secrets_store(monkeypatch):
    try:
        monkeypatch.setattr("trispoke.secrets_store.store.get_secret", lambda *_a, **_kw: None)
    except Exception:
        pass
