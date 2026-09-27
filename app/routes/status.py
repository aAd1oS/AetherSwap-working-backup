"""Status, log, plan, and payment-related routes."""
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter
from app.state import (
    clear_log,
    confirm_payment,
    get_log,
    get_round_summaries,
    get_pending_payment,
    get_plan,
    get_status,
    set_pending_payment,
)
from app.config_loader import get_buff_credentials, load_app_config_validated
from pydantic import BaseModel
from typing import Optional
from utils.buff_protection import get_buff_request_protection
router = APIRouter()
class ConfirmBody(BaseModel):
    order_id: str
    ok: bool


class UserActionLogBody(BaseModel):
    action: str
    phase: str = "start"
    ok: Optional[bool] = None
    summary: str = ""
    partial: bool = False


_USER_ACTION_LABELS = {
    "refresh_inventory": "刷新 Steam 库存",
    "sync_receipts": "刷新入库信息",
    "refresh_prices": "刷新商品现价",
    "refresh_all_holdings": "刷新全部持仓信息",
    "refresh_buff_balance": "刷新 BUFF 余额",
}


def _fmt_log_time(value):
    if value is None:
        return ""
    return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")


@router.get("/api/status")
def api_status():
    st = get_status()
    buff_creds = get_buff_credentials()
    st["buff_no_cookie"] = not bool((buff_creds.get("cookies") or "").strip())
    pipeline_cfg = (load_app_config_validated().get("pipeline") or {})
    recovery_cap = pipeline_cfg.get("buff_protection_recovery_candidate_cap", 30)
    normal_cap = pipeline_cfg.get("iflow_top_n", 30)
    st["buff_protection"] = get_buff_request_protection().snapshot(recovery_cap, normal_cap)
    return st

@router.get("/api/log")
def api_log(since: int = 0):
    return {"lines": get_log(since)}
@router.post("/api/log/clear")
def api_log_clear():
    clear_log()
    return {"ok": True}


@router.post("/api/log/user-action")
def api_log_user_action(body: UserActionLogBody):
    from app.state import log

    label = _USER_ACTION_LABELS.get(str(body.action or "").strip())
    if not label:
        return {"ok": False, "error": "不支持的用户操作类型"}
    if body.phase == "start":
        log(f"用户操作：{label}", "info", category="user_action")
        return {"ok": True}
    summary = " ".join(str(body.summary or "").split())[:300] or ("成功" if body.ok else "失败")
    if body.partial:
        level, category = "warn", "user_result_partial"
    elif body.ok:
        level, category = "info", "user_result_success"
    else:
        level, category = "warn", "user_result_failure"
    log(f"操作结果：{label}——{summary}", level, category=category)
    return {"ok": True}
@router.post("/api/log/export")
def api_log_export():
    lines = get_log(0)
    log_dir = Path("log")
    log_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = log_dir / f"debug_{ts}.txt"
    content = "\n".join(
        f"{_fmt_log_time(e.get('t'))} [{e.get('level', 'info')}] {e.get('msg', '')}"
        for e in lines
    ) + "\n"
    filename.write_text(content, encoding="utf-8")
    return {"ok": True, "path": str(filename), "lines": len(lines)}


@router.post("/api/log/export-summary")
def api_round_summary_export():
    lines = get_round_summaries()
    if not lines:
        return {"ok": False, "error": "当前日志中还没有轮次总结"}

    groups = []
    current = []
    for entry in lines:
        category = str(entry.get("category") or "")
        if category == "round_summary_header" and current:
            groups.append(current)
            current = []
        current.append(f"{_fmt_log_time(entry.get('t'))} {entry.get('msg', '')}".rstrip())
        if category == "round_summary_end":
            groups.append(current)
            current = []
    if current:
        groups.append(current)

    log_dir = Path("log")
    log_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = log_dir / f"round_summary_{ts}.txt"
    content = "\n\n\n".join("\n".join(group) for group in groups) + "\n"
    filename.write_text(content, encoding="utf-8")
    return {
        "ok": True,
        "path": str(filename),
        "rounds": sum(
            1 for entry in lines
            if entry.get("category") == "round_summary_header"
        ),
        "lines": len(lines),
    }
@router.get("/api/plan")
def api_plan():
    return {"plan": get_plan()}
@router.get("/api/pending_payment")
def api_pending_payment():
    return {"pending": get_pending_payment()}
@router.post("/api/confirm_payment")
def api_confirm_payment(body: ConfirmBody):
    if not confirm_payment(body.order_id, body.ok):
        return {"ok": False, "error": "订单已过期、订单号不匹配或当前没有待付款订单"}
    return {"ok": True, "order_id": body.order_id}
