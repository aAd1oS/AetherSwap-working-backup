"""Process-wide safety controls for authenticated BUFF HTTP traffic."""

from __future__ import annotations

import threading
import time
from typing import Any, Dict

BUFF_MIN_INTERVAL_SECONDS = 3.0
BUFF_SELL_ORDERS_CACHE_SECONDS = 60.0
BUFF_HTTP_429_PAUSE_SECONDS = 15 * 60
BUFF_NETWORK_PAUSE_SECONDS = 5 * 60
BUFF_NETWORK_FAILURE_THRESHOLD = 3
BUFF_RECOVERY_MODE_SECONDS = 30 * 60
BUFF_NORMAL_CANDIDATE_CAP = 30
BUFF_RECOVERY_CANDIDATE_CAP = 30


class BuffProtectionError(RuntimeError):
    """Base class for BUFF safety controls that callers must not swallow."""


class BuffManualCircuitOpen(BuffProtectionError):
    """A platform rejection requires manual inspection and Cookie refresh."""


class BuffTemporaryCircuitOpen(BuffProtectionError):
    """BUFF requests are paused until ``pause_until``."""

    def __init__(self, reason: str, pause_until: float, now: float) -> None:
        self.reason = reason
        self.pause_until = pause_until
        self.retry_after = max(0.0, pause_until - now)
        super().__init__(f"{reason}; retry after {self.retry_after:.0f}s")


class BuffRequestProtection:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._manual_reason = ""
        self._pause_reason = ""
        self._pause_until = 0.0
        self._network_failures = 0
        self._recovery_until = 0.0

    def before_request(self) -> None:
        now = time.time()
        recovered_reason = ""
        with self._lock:
            manual_reason = self._manual_reason
            pause_reason = self._pause_reason
            pause_until = self._pause_until
            if pause_until and now >= pause_until:
                recovered_reason = pause_reason
                self._pause_reason = ""
                self._pause_until = 0.0
                self._recovery_until = now + BUFF_RECOVERY_MODE_SECONDS
                pause_reason = ""
                pause_until = 0.0
        if recovered_reason:
            self._notify("pause_recovered", recovered_reason)
        if manual_reason:
            raise BuffManualCircuitOpen(manual_reason)
        if pause_until > now:
            raise BuffTemporaryCircuitOpen(pause_reason, pause_until, now)

    def record_response(self, status_code: int, data: Any) -> None:
        text = str(data or "").casefold()
        if status_code == 429 or "http_429" in text:
            self._open_temporary("BUFF HTTP 429", BUFF_HTTP_429_PAUSE_SECONDS)
        manual_markers = (
            "action forbidden",
            "市场接口访问功能暂时关闭",
            "市场接口已关闭",
            "market interface is temporarily closed",
        )
        if any(marker in text for marker in manual_markers):
            reason = "BUFF Action Forbidden / 市场接口访问功能暂时关闭，需要人工检查并更新 Cookie"
            with self._lock:
                should_notify = not bool(self._manual_reason)
                self._manual_reason = reason
                self._pause_reason = ""
                self._pause_until = 0.0
            if should_notify:
                self._notify("triggered", reason)
            raise BuffManualCircuitOpen(reason)
        with self._lock:
            self._network_failures = 0

    def record_network_failure(self, exc: Exception) -> None:
        with self._lock:
            self._network_failures += 1
            failures = self._network_failures
        if failures >= BUFF_NETWORK_FAILURE_THRESHOLD:
            self._open_temporary(
                f"BUFF ordinary network failures reached {failures}",
                BUFF_NETWORK_PAUSE_SECONDS,
            )

    def mark_manual_cookie_updated(self) -> None:
        now = time.time()
        with self._lock:
            previous_reason = self._manual_reason or self._pause_reason
            self._manual_reason = ""
            self._pause_reason = ""
            self._pause_until = 0.0
            self._network_failures = 0
            self._recovery_until = now + BUFF_RECOVERY_MODE_SECONDS
        if previous_reason:
            self._notify("manual_recovered", previous_reason)

    @staticmethod
    def _normalized_candidate_cap(value: Any, default: int) -> int:
        try:
            parsed = int(value)
            return parsed if parsed > 0 else default
        except (TypeError, ValueError):
            return default

    def effective_candidate_cap(
        self,
        recovery_candidate_cap: Any = None,
        normal_candidate_cap: Any = None,
    ) -> int:
        now = time.time()
        recovery_finished = False
        with self._lock:
            recovery_until = self._recovery_until
            if recovery_until and now >= recovery_until:
                self._recovery_until = 0.0
                recovery_finished = True
        if recovery_finished:
            self._notify("recovery_finished", "30 分钟恢复观察期已结束")
        if now < recovery_until:
            return self._normalized_candidate_cap(
                recovery_candidate_cap,
                BUFF_RECOVERY_CANDIDATE_CAP,
            )
        return self._normalized_candidate_cap(
            normal_candidate_cap,
            BUFF_NORMAL_CANDIDATE_CAP,
        )

    def snapshot(
        self,
        recovery_candidate_cap: Any = None,
        normal_candidate_cap: Any = None,
    ) -> Dict[str, Any]:
        now = time.time()
        candidate_cap = self.effective_candidate_cap(
            recovery_candidate_cap,
            normal_candidate_cap,
        )
        with self._lock:
            return {
                "manual_open": bool(self._manual_reason),
                "manual_reason": self._manual_reason,
                "pause_reason": self._pause_reason,
                "pause_until": self._pause_until,
                "retry_after": max(0.0, self._pause_until - now),
                "network_failures": self._network_failures,
                "recovery_until": self._recovery_until,
                "recovery_mode": now < self._recovery_until,
                "candidate_cap": candidate_cap,
            }

    def reset_for_tests(self) -> None:
        with self._lock:
            self._manual_reason = ""
            self._pause_reason = ""
            self._pause_until = 0.0
            self._network_failures = 0
            self._recovery_until = 0.0

    def _open_temporary(self, reason: str, seconds: int) -> None:
        now = time.time()
        pause_until = now + seconds
        with self._lock:
            should_notify = not (self._pause_until > now)
            self._pause_reason = reason
            self._pause_until = max(self._pause_until, pause_until)
            self._network_failures = 0
            effective_until = self._pause_until
        if should_notify:
            self._notify("triggered", reason)
        raise BuffTemporaryCircuitOpen(reason, effective_until, now)

    @staticmethod
    def _notify(event: str, reason: str) -> None:
        try:
            from app.notify import notify_buff_request_protection

            notify_buff_request_protection(event, reason)
        except Exception:
            pass


_protection = BuffRequestProtection()


def get_buff_request_protection() -> BuffRequestProtection:
    return _protection
