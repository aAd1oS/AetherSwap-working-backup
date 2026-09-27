"""Process-local guard for account-scoped operations.

The guard closes the small race between checking whether work is running and
switching the current account. It deliberately stays process-local: one
Windows instance still operates one Steam account at a time.
"""
from __future__ import annotations

import threading
import uuid
from contextlib import contextmanager
from typing import Callable, Iterator, Optional

from app.accounts import get_current_id


class AccountOperationConflict(RuntimeError):
    pass


_lock = threading.RLock()
_active: dict[str, tuple[str, str]] = {}


def active_operation_names() -> list[str]:
    with _lock:
        return sorted({name for name, _account_id in _active.values()})


def begin_account_operation(name: str, account_id: Optional[str] = None) -> tuple[str, str]:
    with _lock:
        current_id = str(get_current_id() or "")
        expected_id = str(account_id or current_id)
        if not expected_id:
            raise AccountOperationConflict("当前没有已选择账号")
        if current_id != expected_id:
            raise AccountOperationConflict("当前账号已变化，操作已取消")
        token = uuid.uuid4().hex
        _active[token] = (str(name or "账号操作"), expected_id)
        return token, expected_id


def end_account_operation(token: str) -> None:
    with _lock:
        _active.pop(str(token or ""), None)


@contextmanager
def account_operation(name: str, account_id: Optional[str] = None) -> Iterator[str]:
    token, expected_id = begin_account_operation(name, account_id)
    try:
        yield expected_id
    finally:
        end_account_operation(token)


def assert_current_account(account_id: str) -> None:
    with _lock:
        if str(get_current_id() or "") != str(account_id or ""):
            raise AccountOperationConflict("当前账号已变化，已阻止提交平台请求")


def switch_account_atomically(account_id: str, switch_fn: Callable[[], bool]) -> tuple[bool, str]:
    """Run the final account check and switch under the operation registry lock."""
    with _lock:
        if _active:
            names = "、".join(active_operation_names())
            return False, f"当前正在执行：{names}；结束后才能切换账号"
        if not switch_fn():
            return False, "账号不存在"
        return True, ""
