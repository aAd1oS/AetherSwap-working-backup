import socket


def _clear_cache(module):
    module._local_accelerator_cache = {}
    module._local_accelerator_cache_at = 0.0


def test_local_accelerator_requires_loopback_dns(monkeypatch):
    from steam import session

    _clear_cache(session)
    monkeypatch.setattr(
        session.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.2.3.4", 443))],
    )

    status = session.get_local_steam_accelerator_status(force=True)

    assert status["active"] is False
    assert status["reason"] == "dns_not_loopback"


def test_local_accelerator_requires_local_443_listener(monkeypatch):
    from steam import session

    _clear_cache(session)
    monkeypatch.setattr(
        session.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
    )

    def fail_connect(*args, **kwargs):
        raise ConnectionRefusedError()

    monkeypatch.setattr(session.socket, "create_connection", fail_connect)

    status = session.get_local_steam_accelerator_status(force=True)

    assert status["active"] is False
    assert status["reason"] == "listener_unavailable"


def test_local_accelerator_is_ready_only_with_dns_and_listener(monkeypatch):
    from steam import session

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    _clear_cache(session)
    monkeypatch.setattr(
        session.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
    )
    monkeypatch.setattr(
        session.socket,
        "create_connection",
        lambda *args, **kwargs: FakeConnection(),
    )

    status = session.get_local_steam_accelerator_status(force=True)

    assert status["active"] is True
    assert status["reason"] == "ready"
