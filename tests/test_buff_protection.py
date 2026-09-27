import pytest
import requests
from types import SimpleNamespace

from buff.buyer import BuffBuyer
from app.pipeline_steps import filter_iflow_rows
from utils.buff_protection import (
    BUFF_HTTP_429_PAUSE_SECONDS,
    BUFF_NETWORK_PAUSE_SECONDS,
    BuffManualCircuitOpen,
    BuffTemporaryCircuitOpen,
    get_buff_request_protection,
)


class _Response:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code
        self.text = "body"

    def json(self):
        return self._data


class _NoWaitThrottle:
    def wait(self, domain, min_interval):
        return None


@pytest.fixture(autouse=True)
def _reset_protection(monkeypatch):
    monkeypatch.setattr("app.notify.notify_buff_request_protection", lambda *_args: True)
    protection = get_buff_request_protection()
    protection.reset_for_tests()
    yield
    protection.reset_for_tests()


def _disable_wait(monkeypatch):
    monkeypatch.setattr("buff.buyer.get_throttle", lambda: _NoWaitThrottle())


def test_get_sell_orders_retries_only_invalid_argument(monkeypatch):
    _disable_wait(monkeypatch)
    calls = []
    responses = [
        _Response({"code": "Invalid Argument", "error": "Invalid Argument"}),
        _Response({"code": "OK", "data": {"items": [{"id": "1", "price": "8.8"}]}}),
    ]

    def request(*args, **kwargs):
        calls.append(dict(kwargs["params"]))
        return responses.pop(0)

    monkeypatch.setattr("buff.buyer.direct_request", request)
    result = BuffBuyer("session=x; csrf_token=y").get_sell_orders(123)

    assert result == [{"id": "1", "price": "8.8"}]
    assert len(calls) == 2
    assert "mode" in calls[0]
    assert "mode" not in calls[1]


def test_get_sell_orders_does_not_retry_other_api_errors(monkeypatch):
    _disable_wait(monkeypatch)
    calls = []
    monkeypatch.setattr(
        "buff.buyer.direct_request",
        lambda *args, **kwargs: calls.append(kwargs) or _Response({"code": "Error", "error": "other"}),
    )

    assert BuffBuyer("session=x").get_sell_orders(123) is None
    assert len(calls) == 1


def test_successful_sell_orders_are_cached_for_60_seconds(monkeypatch):
    _disable_wait(monkeypatch)
    calls = []
    monkeypatch.setattr(
        "buff.buyer.direct_request",
        lambda *args, **kwargs: calls.append(kwargs) or _Response({"code": "OK", "data": {"items": [{"id": "1"}]}}),
    )
    buyer = BuffBuyer("session=x")

    assert buyer.get_sell_orders(123) == [{"id": "1"}]
    assert buyer.get_sell_orders(123) == [{"id": "1"}]
    assert len(calls) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"code": "Action Forbidden"},
        {"code": "Error", "error": "市场接口访问功能暂时关闭，请稍后再试"},
    ],
)
def test_action_forbidden_opens_manual_circuit_until_cookie_update(monkeypatch, payload):
    _disable_wait(monkeypatch)
    calls = []
    monkeypatch.setattr(
        "buff.buyer.direct_request",
        lambda *args, **kwargs: calls.append(kwargs) or _Response(payload),
    )
    buyer = BuffBuyer("session=x")

    with pytest.raises(BuffManualCircuitOpen):
        buyer.get_sell_orders(123)
    with pytest.raises(BuffManualCircuitOpen):
        buyer.get_sell_orders(456)
    assert len(calls) == 1

    protection = get_buff_request_protection()
    protection.mark_manual_cookie_updated()
    assert protection.snapshot()["recovery_mode"] is True
    assert protection.effective_candidate_cap() == 30
    assert protection.effective_candidate_cap(12) == 12


def test_open_circuit_blocks_a_cached_sell_order(monkeypatch):
    _disable_wait(monkeypatch)
    monkeypatch.setattr(
        "buff.buyer.direct_request",
        lambda *args, **kwargs: _Response({"code": "OK", "data": {"items": [{"id": "cached"}]}}),
    )
    buyer = BuffBuyer("session=x")
    assert buyer.get_sell_orders(123) == [{"id": "cached"}]

    protection = get_buff_request_protection()
    with pytest.raises(BuffManualCircuitOpen):
        protection.record_response(200, {"code": "Action Forbidden"})
    with pytest.raises(BuffManualCircuitOpen):
        buyer.get_sell_orders(123)


def test_http_429_pauses_for_15_minutes(monkeypatch):
    _disable_wait(monkeypatch)
    monkeypatch.setattr(
        "buff.buyer.direct_request",
        lambda *args, **kwargs: _Response({"code": "HTTP_429"}, status_code=429),
    )

    with pytest.raises(BuffTemporaryCircuitOpen) as raised:
        BuffBuyer("session=x").get_sell_orders(123)
    assert BUFF_HTTP_429_PAUSE_SECONDS - 1 <= raised.value.retry_after <= BUFF_HTTP_429_PAUSE_SECONDS


def test_third_consecutive_network_failure_pauses_for_5_minutes(monkeypatch):
    _disable_wait(monkeypatch)
    monkeypatch.setattr(
        "buff.buyer.direct_request",
        lambda *args, **kwargs: (_ for _ in ()).throw(requests.ConnectionError("reset")),
    )
    buyer = BuffBuyer("session=x")

    assert buyer.get_sell_orders(1) is None
    assert buyer.get_sell_orders(2) is None
    with pytest.raises(BuffTemporaryCircuitOpen) as raised:
        buyer.get_sell_orders(3)
    assert BUFF_NETWORK_PAUSE_SECONDS - 1 <= raised.value.retry_after <= BUFF_NETWORK_PAUSE_SECONDS


def test_candidate_cap_is_30_outside_recovery_mode():
    assert get_buff_request_protection().effective_candidate_cap() == 30


def test_candidate_caps_allow_values_above_30():
    protection = get_buff_request_protection()
    assert protection.effective_candidate_cap(60, 60) == 60

    protection.mark_manual_cookie_updated()
    assert protection.effective_candidate_cap(45, 60) == 45


def test_candidate_filter_caps_normal_and_manual_recovery_modes():
    rows = [
        SimpleNamespace(
            name=f"Item {index}",
            name_cn="",
            min_price="10",
            sell_ratio="0.8",
            platform=f"https://buff.163.com/goods/{1000 + index}",
            steam_link="",
            volume="100",
        )
        for index in range(70)
    ]
    config = {
        "pipeline": {
            "iflow_top_n": 50,
            "buff_protection_recovery_candidate_cap": 30,
            "exclude_keywords": [],
        },
        "iflow": {"sort_by": "sell", "min_volume": 0},
    }

    assert len(filter_iflow_rows(rows, config)) == 50
    get_buff_request_protection().mark_manual_cookie_updated()
    assert len(filter_iflow_rows(rows, config)) == 30

    config["pipeline"]["buff_protection_recovery_candidate_cap"] = 12
    assert len(filter_iflow_rows(rows, config)) == 12


def test_protection_transition_notifies_once(monkeypatch):
    notifications = []
    monkeypatch.setattr(
        "app.notify.notify_buff_request_protection",
        lambda event, reason: notifications.append((event, reason)) or True,
    )
    protection = get_buff_request_protection()

    with pytest.raises(BuffManualCircuitOpen):
        protection.record_response(200, {"code": "Action Forbidden"})
    with pytest.raises(BuffManualCircuitOpen):
        protection.record_response(200, {"code": "Action Forbidden"})

    assert [event for event, _reason in notifications] == ["triggered"]

    protection.mark_manual_cookie_updated()
    assert [event for event, _reason in notifications] == ["triggered", "manual_recovered"]


def test_temporary_pause_enters_configurable_recovery_mode_when_it_expires(monkeypatch):
    now = [1000.0]
    notifications = []
    monkeypatch.setattr("utils.buff_protection.time.time", lambda: now[0])
    monkeypatch.setattr(
        "app.notify.notify_buff_request_protection",
        lambda event, reason: notifications.append((event, reason)) or True,
    )
    protection = get_buff_request_protection()

    with pytest.raises(BuffTemporaryCircuitOpen):
        protection.record_response(429, {"code": "HTTP_429"})
    now[0] += BUFF_HTTP_429_PAUSE_SECONDS + 1

    protection.before_request()
    snapshot = protection.snapshot(12)

    assert snapshot["recovery_mode"] is True
    assert snapshot["candidate_cap"] == 12
    assert [event for event, _reason in notifications] == ["triggered", "pause_recovered"]
