import threading


def _setup_accounts(tmp_path, monkeypatch):
    from app import accounts

    monkeypatch.setattr(accounts, "_ACCOUNTS_FILE", tmp_path / "accounts.json")
    monkeypatch.setattr(accounts, "_cache", None)
    first = accounts.add_account(username="first", steam_id="111")
    second = accounts.add_account(username="second", steam_id="222")
    accounts.set_current(first["id"])
    return accounts, first, second


def test_switch_is_blocked_by_registered_operation(tmp_path, monkeypatch):
    accounts, first, second = _setup_accounts(tmp_path, monkeypatch)
    from app.account_operations import account_operation, switch_account_atomically

    with account_operation("库存刷新", first["id"]):
        ok, error = switch_account_atomically(
            second["id"], lambda: accounts.set_current(second["id"])
        )

    assert ok is False
    assert "库存刷新" in error
    assert accounts.get_current_id() == first["id"]


def test_operation_is_released_after_exception(tmp_path, monkeypatch):
    accounts, first, second = _setup_accounts(tmp_path, monkeypatch)
    from app.account_operations import account_operation, switch_account_atomically

    try:
        with account_operation("出售任务", first["id"]):
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    ok, error = switch_account_atomically(
        second["id"], lambda: accounts.set_current(second["id"])
    )
    assert ok is True
    assert error == ""
    assert accounts.get_current_id() == second["id"]


def test_operation_and_switch_registration_are_atomic(tmp_path, monkeypatch):
    accounts, first, second = _setup_accounts(tmp_path, monkeypatch)
    from app.account_operations import account_operation, switch_account_atomically

    entered = threading.Event()
    release = threading.Event()

    def worker():
        with account_operation("自动收货", first["id"]):
            entered.set()
            release.wait(timeout=2)

    thread = threading.Thread(target=worker)
    thread.start()
    assert entered.wait(timeout=1)
    ok, _error = switch_account_atomically(
        second["id"], lambda: accounts.set_current(second["id"])
    )
    release.set()
    thread.join(timeout=2)

    assert ok is False
    assert accounts.get_current_id() == first["id"]
