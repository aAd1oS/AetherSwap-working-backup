from pathlib import Path

from app import pipeline
from app import account_scope


ROOT = Path(__file__).resolve().parents[1]


class _Context:
    def __init__(self):
        self.logs = []
        self.statuses = []

    def log(self, message, level="info", category="pipeline"):
        self.logs.append((message, level, category))

    def set_status(self, status, stage, **kwargs):
        self.statuses.append((status, stage, kwargs))


def test_round_limit_stops_only_after_configured_completed_rounds():
    ctx = _Context()

    completed, stopped = pipeline._complete_limited_round(ctx, 0, 2, 1)
    assert (completed, stopped) == (1, False)
    assert ctx.statuses == []
    assert any("1/2" in entry[0] for entry in ctx.logs)

    completed, stopped = pipeline._complete_limited_round(ctx, completed, 2, 1)
    assert (completed, stopped) == (2, True)
    assert ctx.statuses[-1][0:2] == ("idle", "ROUND_LIMIT_REACHED")
    assert ctx.statuses[-1][2]["progress_done"] == 2
    assert "成功购买 1 单" in ctx.logs[-1][0]


def test_zero_round_limit_remains_unlimited():
    ctx = _Context()
    completed, stopped = pipeline._complete_limited_round(ctx, 9, 0, 0)

    assert (completed, stopped) == (10, False)
    assert ctx.logs == []
    assert ctx.statuses == []


def test_final_round_summary_describes_wait_then_stop():
    assert pipeline._round_summary_next_action(300, 4, 5, counts_as_complete=True) == (
        "等待 300 秒后自动结束"
    )
    assert pipeline._round_summary_next_action(300, 4, 5, counts_as_complete=False) == (
        "300 秒后重新拉取"
    )


def test_round_limit_setting_is_wired_to_settings_ui():
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    settings_js = (ROOT / "web" / "js" / "settings.js").read_text(encoding="utf-8")

    assert 'id="cfg-max-run-rounds"' in html
    assert 'p.max_run_rounds ?? 0' in settings_js
    assert 'max_run_rounds: readIntInput("cfg-max-run-rounds")' in settings_js
    assert '/js/settings.js?v=3' in html


def test_buff_protection_recovery_cap_is_wired_to_settings_ui():
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    settings_js = (ROOT / "web" / "js" / "settings.js").read_text(encoding="utf-8")

    assert 'id="cfg-buff-protection-recovery-candidate-cap"' in html
    assert "p.buff_protection_recovery_candidate_cap ?? 30" in settings_js
    assert 'buff_protection_recovery_candidate_cap: readIntInput("cfg-buff-protection-recovery-candidate-cap")' in settings_js


def test_normal_candidate_cap_is_visible_in_system_settings():
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    settings_js = (ROOT / "web" / "js" / "settings.js").read_text(encoding="utf-8")

    assert 'id="cfg-iflow_top_n"' in html
    assert 'id="cfg-iflow_top_n" min="1" step="1"' in html
    assert 'id="cfg-iflow_top_n" min="1" max=' not in html
    assert 'iflow_top_n: el("cfg-iflow_top_n")' in settings_js


def test_pipeline_waits_after_each_successful_round_then_stops(monkeypatch):
    events = []

    class FakeState:
        def clear_stop(self):
            return None

        def set_buff_auth_expired(self, *_args):
            return None

        def set_buff_verification_required(self, *_args):
            return None

    class FakeContext(_Context):
        verbose = False
        flow_id = "round-limit-test"

        def __init__(self, *_args, **_kwargs):
            super().__init__()

        def debug(self, *_args, **_kwargs):
            return None

        def is_stop_requested(self):
            return False

    class FakeProxyManager:
        def is_proxy_enabled(self):
            return False

    class FakeNetworkChecker:
        def report_success(self):
            events.append("network-ok")

    monkeypatch.setattr(pipeline, "get_state", lambda: FakeState())
    monkeypatch.setattr(pipeline, "PipelineContext", FakeContext)
    monkeypatch.setattr(pipeline, "apply_strategy_to_config", lambda config, *_args: config)
    monkeypatch.setattr(pipeline, "get_daily_budget_summary", lambda _target: {
        "used": 0,
        "confirmed": 0,
        "reserved": 0,
        "remaining": 10,
    })
    monkeypatch.setattr(pipeline, "get_blocking_payment_orders", lambda: [])
    monkeypatch.setattr(pipeline, "get_buff_credentials", lambda: {"cookies": "present"})
    monkeypatch.setattr(pipeline, "get_steam_credentials", lambda: {"steam_id": "76561198000000000"})
    monkeypatch.setattr(account_scope, "validate_current_account_identity", lambda: (True, ""))
    monkeypatch.setattr(pipeline, "get_proxy_manager", lambda: FakeProxyManager())
    monkeypatch.setattr(pipeline, "SteamClient", object)
    monkeypatch.setattr(pipeline, "StabilityAnalyzer", lambda **_kwargs: object())
    monkeypatch.setattr(pipeline, "create_buff_client_from_config", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(pipeline, "get_network_checker", lambda: FakeNetworkChecker())
    monkeypatch.setattr(
        pipeline,
        "_fetch_and_filter_deals",
        lambda *_args, **_kwargs: events.append("fetch") or ([], False),
    )
    monkeypatch.setattr(
        pipeline,
        "_wait_retry_and_refresh_buff_balance",
        lambda _ctx, seconds, *_args: events.append(("wait", seconds)) or False,
    )
    monkeypatch.setattr(
        pipeline,
        "get_buff_balance",
        lambda: {"has_value": False},
    )

    pipeline._run_pipeline({
        "buff": {"pay_method": "wechat"},
        "pipeline": {
            "target_balance": 10,
            "retry_interval_seconds": 60,
            "max_run_rounds": 2,
        },
    })

    assert events.count("fetch") == 2
    assert events.count(("wait", 60)) == 2
