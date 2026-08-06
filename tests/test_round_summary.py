from pathlib import Path

import pytest

from app import pipeline
from app.routes import status
from app.state import State


class _FakeState:
    def set_pending_payment(self, *_args, **_kwargs):
        return None

    def wait_payment_confirm(self, *_args, **_kwargs):
        return True

    def confirm_payment(self, *_args, **_kwargs):
        return None

    def is_stop_requested(self):
        return False

    def append_purchase(self, *_args, **_kwargs):
        return None


class _FakeContext:
    flow_id = "run-test"

    def __init__(self):
        self.state = _FakeState()
        self.logs = []

    def log(self, message, level="info", category="pipeline"):
        self.logs.append({"msg": message, "level": level, "category": category})

    def debug(self, *_args, **_kwargs):
        return None

    def set_status(self, *_args, **_kwargs):
        return None

    def is_stop_requested(self):
        return False


def test_round_summary_emits_compact_colored_categories(monkeypatch):
    ctx = _FakeContext()
    summary = pipeline._new_round_summary(2, 4.50)
    summary.update({
        "candidate_count": 25,
        "scan_attempts": 7,
        "successes": [
            {"name": "Item A", "amount": 2.22},
            {"name": "Item A", "amount": 2.30},
        ],
        "failure_counts": {
            "价格/卖单/稳定性预检": 4,
            "BUFF 余额通道不可用": 1,
        },
    })
    monkeypatch.setattr(
        pipeline,
        "get_buff_balance",
        lambda: {"has_value": True, "balance": 15.11, "uncertain": True},
    )

    pipeline._emit_round_summary(ctx, summary, 20.0, "300 秒后重新拉取")
    pipeline._emit_round_summary(ctx, summary, 20.0, "不应重复输出")

    categories = [entry["category"] for entry in ctx.logs]
    assert categories == [
        "round_summary_header",
        "round_summary_scan",
        "round_summary_success",
        "round_summary_failure",
        "round_summary_money",
        "round_summary_end",
    ]
    messages = [entry["msg"] for entry in ctx.logs]
    assert any("第 2 轮总结" in message for message in messages)
    assert any("候选 25 件｜实际检查 7 件次" in message for message in messages)
    assert any("成功　2 单｜Item A ×2" in message for message in messages)
    assert any("未通过　5 件次" in message for message in messages)
    assert any("本轮投入 ¥4.52" in message for message in messages)
    assert any("今日累计 ¥9.02/20.00" in message for message in messages)
    assert any("BUFF 最近可信 ¥15.11（本次通道未采信）" in message for message in messages)


def test_process_deals_collects_success_and_failure_counts(monkeypatch):
    ctx = _FakeContext()
    filtered = [
        {"goods_id": 1, "name": "Bought Item", "min_price": 2.22},
        {"goods_id": 2, "name": "Precheck Failed", "min_price": 2.10},
        {"goods_id": 3, "name": "No Balance", "min_price": 2.30},
    ]
    picks = iter([
        (filtered[0], {2}),
        (filtered[2], set()),
        (None, set()),
    ])
    payments = iter([2.22, pipeline.SKIP_BALANCE_UNAVAILABLE])
    monkeypatch.setattr(pipeline, "pick_stable_item", lambda *_args, **_kwargs: next(picks))
    monkeypatch.setattr(pipeline, "lock_and_confirm_payment", lambda *_args, **_kwargs: next(payments))
    monkeypatch.setattr(pipeline, "jittered_sleep", lambda *_args, **_kwargs: None)
    summary = pipeline._new_round_summary(1, 0.0)
    summary["candidate_count"] = len(filtered)

    acc, bought, stopped = pipeline._process_deals_for_target(
        ctx,
        filtered,
        {"buff": {"pay_method": "balance"}},
        10.0,
        0.0,
        0,
        object(),
        object(),
        object(),
        set(),
        set(),
        set(),
        summary,
    )

    assert stopped is False
    assert acc == pytest.approx(2.22)
    assert bought == 1
    assert summary["scan_attempts"] == 3
    assert summary["successes"] == [{"name": "Bought Item", "amount": 2.22}]
    assert summary["failure_counts"] == {
        "价格/卖单/稳定性预检": 1,
        "BUFF 余额通道不可用": 1,
    }


def test_round_summaries_survive_rolling_log_eviction_and_clear():
    state = State()
    for round_number in (1, 2):
        state.log(
            f"第 {round_number} 轮",
            category="round_summary_header",
            flow_id="flow-a",
        )
        state.log("下一步", category="round_summary_end", flow_id="flow-a")
    for index in range(600):
        state.log(f"ordinary-{index}")

    assert len(state.get_log()) == 500
    assert [entry["msg"] for entry in state.get_round_summaries()] == [
        "第 1 轮",
        "下一步",
        "第 2 轮",
        "下一步",
    ]

    state.clear_log()
    assert state.get_round_summaries() == []


def test_summary_export_contains_every_round(monkeypatch, tmp_path):
    lines = [
        {"t": 1, "msg": "第 1 轮", "category": "round_summary_header"},
        {"t": 2, "msg": "第 1 轮资金", "category": "round_summary_money"},
        {"t": 3, "msg": "第 1 轮结束", "category": "round_summary_end"},
        {"t": 4, "msg": "第 2 轮", "category": "round_summary_header"},
        {"t": 5, "msg": "第 2 轮结束", "category": "round_summary_end"},
    ]
    monkeypatch.setattr(status, "get_round_summaries", lambda: lines)
    monkeypatch.chdir(tmp_path)

    result = status.api_round_summary_export()

    output_path = tmp_path / result["path"]
    content = output_path.read_text(encoding="utf-8")
    assert result["ok"] is True
    assert result["rounds"] == 2
    assert "第 1 轮资金" in content
    assert "第 2 轮结束" in content
    assert "\n\n\n" in content


def test_round_summary_frontend_has_export_and_category_styles():
    root = Path(__file__).resolve().parents[1]
    html = (root / "web" / "index.html").read_text(encoding="utf-8")
    console_js = (root / "web" / "js" / "console.js").read_text(encoding="utf-8")
    main_js = (root / "web" / "js" / "main.js").read_text(encoding="utf-8")
    css = (root / "web" / "css" / "components.css").read_text(encoding="utf-8")

    assert 'id="btn-export-round-summary"' in html
    assert 'API + "/log/export-summary"' in console_js
    assert "round_summary_success" in console_js
    assert 'el("btn-export-round-summary")' in main_js
    assert ".log-round-summary-header" in css
    assert ".log-round-summary-end" in css
