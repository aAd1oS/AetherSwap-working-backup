import threading
import time
import uuid
import math
from collections import Counter
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config_loader import get_buff_credentials, get_steam_credentials, load_app_config_validated
from app.config_schema import DEFAULTS, merge
from app.pipeline_context import PipelineContext
from app.pipeline_steps import (
    TARGET_REACHED,
    PAYMENT_REVIEW_REQUIRED,
    SKIP_BALANCE_UNAVAILABLE,
    SKIP_NO_FAILED,
    SKIP_VERIFICATION_FAILED,
    filter_iflow_rows,
    lock_and_confirm_payment,
    pick_stable_item,
)
from app.services.iflow_client import fetch_iflow_rows
from app.services.analysis_client import StabilityAnalyzer
from app.services.buff_client import create_buff_client_from_config
from app.services.buff_balance import get_buff_balance, get_buff_balance_probe
from app.services.steam_client import SteamClient
from app.state import get_state, append_sale
from app.strategy_engine import apply_strategy_to_config
from app.order_state import get_blocking_payment_orders, get_daily_budget_summary
from buff.buyer import BuffAuthExpired, BuffVerificationRequired
from utils.buff_protection import BuffManualCircuitOpen, BuffTemporaryCircuitOpen
from steamdt.models import SteamDTQueryParams
from utils.delay import jittered_sleep
from utils.money import USD_TO_CNY_DEFAULT, list_price_display_to_cents
from utils.network_check import get_network_checker
from utils.proxy_manager import get_proxy_manager
from datetime import datetime

from app.sell_pipeline import run_sell_phase_on_inventory_update

DEFAULT_RETRY_INTERVAL_SECONDS = 300
DEFAULT_START_TIME_HOUR = 8
DEFAULT_END_TIME_HOUR = 22
FAILED_GOODS_TTL_SECONDS = 1800


def _is_in_time_window(start_hour: int, end_hour: int) -> bool:
    hour = datetime.now().hour
    if start_hour < end_hour:
        return start_hour <= hour < end_hour
    return hour >= start_hour or hour < end_hour


def _preview_balance_amount(preview: dict):
    preview = preview if isinstance(preview, dict) else {}
    for key in ("balance", "reported_balance"):
        try:
            amount = float(preview.get(key))
        except (TypeError, ValueError):
            continue
        if math.isfinite(amount) and amount >= 0:
            return round(amount, 2)
    return None


def _refresh_buff_balance_after_retry(
    ctx: PipelineContext,
    buyer,
    payment_mode: str,
) -> bool:
    if str(payment_mode or "").strip().lower() not in {"balance", "balance_first"}:
        return False

    probe = get_buff_balance_probe()
    try:
        goods_id = int(probe.get("goods_id") or 0)
    except (TypeError, ValueError):
        goods_id = 0
    game = str(probe.get("game") or "csgo")
    sell_order_id = str(probe.get("sell_order_id") or "").strip()
    price = str(probe.get("price") or "").strip()
    if goods_id <= 0 or not sell_order_id or not price:
        ctx.debug("[Buff余额] Retry 结束，暂无完整的只读余额探针，本轮跳过自动刷新")
        return False

    previous = get_buff_balance()
    previous_amount = previous.get("balance") if previous.get("has_value") else None
    ctx.set_status(
        "running",
        "REFRESHING_BUFF_BALANCE",
        progress_item="Retry completed; checking BUFF balance",
    )
    try:
        preview = buyer.preview_balance_payment_once(
            game,
            goods_id,
            sell_order_id,
            price,
        )
    except (BuffAuthExpired, BuffVerificationRequired, BuffManualCircuitOpen, BuffTemporaryCircuitOpen):
        raise
    except Exception as exc:
        ctx.log(
            f"[Buff余额] Retry 结束自动刷新失败: {type(exc).__name__}: {exc}；继续下一轮",
            "warn",
            category="buff",
        )
        return False

    if (preview or {}).get("balance_observation_trustworthy") is False:
        cached = get_buff_balance()
        observed_amount = _preview_balance_amount(preview)
        observed_text = f"{observed_amount:.2f}" if observed_amount is not None else "未知"
        if cached.get("has_value"):
            retained_text = f"，未覆盖最近可信余额 {float(cached['balance']):.2f}"
        else:
            retained_text = "，未写入账号余额"
        ctx.log(
            f"[Buff余额] Retry 预览只返回订单级不可用通道 {observed_text}{retained_text}；继续下一轮",
            "warn",
            category="buff",
        )
        return False

    current_amount = _preview_balance_amount(preview)
    if current_amount is None:
        reason = str((preview or {}).get("reason") or "购买预览未返回可识别余额")
        ctx.log(
            f"[Buff余额] Retry 结束自动刷新未取得余额: {reason}；继续下一轮",
            "warn",
            category="buff",
        )
        return False

    try:
        previous_value = float(previous_amount)
    except (TypeError, ValueError):
        previous_value = None
    if previous_value is not None and current_amount > previous_value + 0.009:
        detail = f"{previous_value:.2f} -> {current_amount:.2f}（检测到余额增加）"
    elif previous_value is not None:
        detail = f"{current_amount:.2f}"
    else:
        detail = f"{current_amount:.2f}"
    ctx.log(
        f"[Buff余额] Retry 结束已自动刷新: {detail}",
        "info",
        category="buff",
    )
    return True


def _wait_retry_and_refresh_buff_balance(
    ctx: PipelineContext,
    wait_seconds: int,
    buyer,
    payment_mode: str,
) -> bool:
    if ctx.wait_retry(wait_seconds):
        return True
    _refresh_buff_balance_after_retry(ctx, buyer, payment_mode)
    return False


def _new_round_summary(round_number: int, start_acc: float) -> dict:
    return {
        "round_number": int(round_number),
        "start_acc": round(float(start_acc), 2),
        "candidate_count": 0,
        "scan_attempts": 0,
        "successes": [],
        "failure_counts": {},
        "notes": [],
        "emitted": False,
    }


def _add_round_failure(summary: dict, reason: str, count: int = 1) -> None:
    if not summary or count <= 0:
        return
    failures = summary.setdefault("failure_counts", {})
    label = str(reason or "其他未通过").strip()
    failures[label] = int(failures.get(label, 0)) + int(count)


def _summary_item_names(successes: list, limit: int = 5) -> str:
    counts = Counter(
        str(entry.get("name") or "未命名商品").strip()
        for entry in successes
    )
    parts = []
    for name, count in counts.items():
        short_name = name if len(name) <= 38 else name[:37] + "…"
        parts.append(f"{short_name} ×{count}" if count > 1 else short_name)
    shown = parts[:limit]
    if len(parts) > limit:
        shown.append(f"另 {len(parts) - limit} 种")
    return "、".join(shown)


def _emit_round_summary(
    ctx: PipelineContext,
    summary: dict,
    target: float,
    next_action: str,
) -> None:
    if not summary or summary.get("emitted"):
        return
    summary["emitted"] = True

    successes = list(summary.get("successes") or [])
    failure_counts = dict(summary.get("failure_counts") or {})
    failure_total = sum(int(value) for value in failure_counts.values())
    round_spent = round(
        sum(float(entry.get("amount") or 0) for entry in successes),
        2,
    )
    daily_actual = round(float(summary.get("start_acc") or 0) + round_spent, 2)
    remaining = round(max(0.0, float(target) - daily_actual), 2)
    balance = get_buff_balance()
    if balance.get("has_value"):
        buff_text = f"BUFF 最近可信 ¥{float(balance['balance']):.2f}"
        if balance.get("uncertain"):
            buff_text += "（本次通道未采信）"
    else:
        buff_text = "BUFF 余额尚未读取"

    flow_id = str(getattr(ctx, "flow_id", "") or "-")
    ctx.log(
        f"━━━━ 任务 {flow_id} · 第 {int(summary.get('round_number') or 0)} 轮总结 ━━━━",
        "info",
        category="round_summary_header",
    )
    ctx.log(
        f"扫描　候选 {int(summary.get('candidate_count') or 0)} 件｜"
        f"实际检查 {int(summary.get('scan_attempts') or 0)} 件次",
        "info",
        category="round_summary_scan",
    )
    if successes:
        ctx.log(
            f"成功　{len(successes)} 单｜{_summary_item_names(successes)}",
            "info",
            category="round_summary_success",
        )
    else:
        ctx.log("成功　0 单", "info", category="round_summary_neutral")

    if failure_total:
        failure_text = "、".join(
            f"{reason} {count}"
            for reason, count in failure_counts.items()
        )
        ctx.log(
            f"未通过　{failure_total} 件次｜{failure_text}",
            "warn",
            category="round_summary_failure",
        )
    else:
        ctx.log("未通过　0 件次", "info", category="round_summary_neutral")

    for note in summary.get("notes") or []:
        ctx.log(f"说明　{note}", "info", category="round_summary_note")
    ctx.log(
        f"资金　本轮投入 ¥{round_spent:.2f}｜今日累计 ¥{daily_actual:.2f}/{float(target):.2f}｜"
        f"剩余额度 ¥{remaining:.2f}｜{buff_text}",
        "info",
        category="round_summary_money",
    )
    ctx.log(
        f"下一步　{next_action}",
        "info",
        category="round_summary_end",
    )


def _fetch_and_filter_deals(
    ctx: PipelineContext,
    cfg: dict,
    retry_interval: int,
    exclude_goods_ids=None,
):
    ctx.set_status("running", "FETCHING_DEALS", progress_total=0, progress_done=0, progress_item="")
    ctx.log("正在拉取 SteamDT 数据…", "info", category="steamdt")
    try:
        rows = fetch_iflow_rows(cfg)
    except Exception as e:
        ctx.log(f"SteamDT 拉取失败: {type(e).__name__}: {e}，{retry_interval}秒后重试", "warn", category="steamdt")
        return None, True  # (rows, fetch_failed)
    if not rows:
        ctx.log(f"SteamDT 未返回任何数据，{retry_interval}秒后重试", "warn", category="steamdt")
        return None, False
    ctx.log(f"SteamDT 返回 {len(rows)} 条原始数据", "info", category="steamdt")
    if ctx.verbose:
        iflow_cfg = cfg.get("steamdt") or cfg.get("iflow", {})
        q = SteamDTQueryParams(
            page=int(iflow_cfg.get("page_num", 1)),
            page_size=int(iflow_cfg.get("page_size", 200)),
            min_sell_price=str(iflow_cfg.get("min_price", 2)),
            max_sell_price=int(iflow_cfg.get("max_price", 5000)),
            min_transaction_count=str(iflow_cfg.get("min_volume", 200)),
        )
        ctx.debug(f"[详细流程] SteamDT 请求参数: page={q.page} pageSize={q.page_size} minPrice={q.min_sell_price} maxPrice={q.max_sell_price} minTx={q.min_transaction_count}")
        ctx.debug(f"[详细流程] 原始数据共 {len(rows)} 条，SteamDT 返回顺序（前20条）:")
        for i, r in enumerate(rows[:20]):
            nm = (getattr(r, "name", None) or "")[:42]
            ctx.debug(f"  {i+1:2}. {nm} | sell={getattr(r, 'sell_ratio', '')} buy={getattr(r, 'buy_ratio', '')}")
    filtered = filter_iflow_rows(
        rows,
        cfg,
        log_fn=lambda msg, lvl="info": ctx.log(msg, lvl),
        exclude_goods_ids=exclude_goods_ids,
    )
    ctx.log(f"筛选后剩余 {len(filtered)} 条", "info")
    if ctx.verbose and filtered:
        ctx.debug(f"[详细流程] 筛选后共 {len(filtered)} 条，顺序不变（前20条）:")
        for i, item in enumerate(filtered[:20]):
            nm = (item.get("name") or "")[:42]
            ctx.debug(f"  {i+1:2}. {nm} | 比例={item.get('ratio', '')} 最低价={item.get('min_price', '')}")
    return filtered, False


def _process_deals_for_target(
    ctx: PipelineContext,
    filtered: list,
    cfg: dict,
    target: float,
    current_acc: float,
    total_bought: int,
    steam_client,
    analyzer,
    buyer,
    failed_goods_ids: set,
    skipped_this_round: set,
    stability_failed_this_round: set,
    round_summary: dict,
    attempted_goods_ids=None,
):
    acc = current_acc
    bought = total_bought
    n_filtered = len(filtered)

    def buff_log(msg: str, level: str = "info") -> None:
        ctx.log(msg, level, category="buff")

    while acc < target:
        if ctx.is_stop_requested():
            ctx.log("用户请求停止", "warn", category="buff")
            ctx.set_status("stopped", "已停止")
            return acc, bought, True

        ctx.set_status("running", "CHECKING_STABILITY", progress_total=n_filtered, progress_done=0, progress_item="")
        chosen, new_stability_failed = pick_stable_item(
            filtered, cfg, steam_client, analyzer, ctx.is_stop_requested,
            log_fn=ctx.log,
            exclude_goods_ids=failed_goods_ids | skipped_this_round | stability_failed_this_round,
            buff_client=buyer,
            attempted_goods_ids=attempted_goods_ids,
        )
        round_summary["scan_attempts"] = int(round_summary.get("scan_attempts") or 0) + len(new_stability_failed)
        _add_round_failure(
            round_summary,
            "价格/卖单/稳定性预检",
            len(new_stability_failed),
        )
        stability_failed_this_round |= new_stability_failed

        if ctx.is_stop_requested():
            ctx.set_status("stopped", "已停止")
            return acc, bought, True
        if chosen is None:
            break
        round_summary["scan_attempts"] = int(round_summary.get("scan_attempts") or 0) + 1
        if acc >= target:
            break

        ctx.log(f"购买本件: {chosen['name']} goods_id={chosen['goods_id']} 参考价={chosen.get('min_price')}", "info", category="buff")

        def on_entering_payment() -> None:
            ctx.set_status("running", "CHECKOUT_PENDING", progress_item=chosen.get("name", ""))

        paid = lock_and_confirm_payment(
            buyer, chosen, cfg, target, acc,
            ctx.state.set_pending_payment,
            ctx.state.wait_payment_confirm,
            ctx.state.confirm_payment,
            ctx.state.is_stop_requested,
            ctx.state.append_purchase,
            log_fn=buff_log,
            on_entering_payment=on_entering_payment,
        )

        if ctx.is_stop_requested():
            ctx.set_status("stopped", "已停止")
            return acc, bought, True

        if paid is TARGET_REACHED:
            ctx.log("累计已达/超过目标，结束购买", "info", category="buff")
            round_summary.setdefault("notes", []).append("剩余额度不足以购买下一件，未继续锁单")
            acc = target
            break
        if paid is SKIP_NO_FAILED:
            gid = chosen.get("goods_id")
            if gid is not None:
                skipped_this_round.add(gid)
            _add_round_failure(round_summary, "单价或今日额度上限")
            ctx.log("安全采购上限不足，跳过本件", "warn", category="buff")
            continue
        if paid is SKIP_VERIFICATION_FAILED:
            gid = chosen.get("goods_id")
            if gid is not None:
                failed_goods_ids.add(gid)
            _add_round_failure(round_summary, "二次验证未通过")
            ctx.log("二次验证未通过，跳过本件", "warn", category="buff")
            continue
        if paid is SKIP_BALANCE_UNAVAILABLE:
            gid = chosen.get("goods_id")
            if gid is not None:
                skipped_this_round.add(gid)
            _add_round_failure(round_summary, "BUFF 余额通道不可用")
            if str((cfg.get("buff") or {}).get("pay_method") or "").strip().lower() == "balance_first":
                ctx.log("智能支付无法安全确认或切换付款方式，本件未锁单", "warn", category="buff")
            else:
                ctx.log("BUFF 可用资金当前不可用，本件未锁单", "warn", category="buff")
            continue
        if paid is PAYMENT_REVIEW_REQUIRED:
            _add_round_failure(round_summary, "付款结果需人工核对")
            ctx.log("余额锁单或付款结果需要人工核对，已立即停止任务并阻止继续锁单", "error", category="buff")
            ctx.set_status("error", "BUFF_PAYMENT_REVIEW_REQUIRED")
            return acc, bought, True
        if paid is None:
            gid = chosen.get("goods_id")
            if gid is not None:
                failed_goods_ids.add(gid)
            _add_round_failure(round_summary, "锁单或付款确认失败")
            ctx.log("锁单/确认未成功，跳过本件", "warn", category="buff")
            continue

        acc += paid
        acc = round(acc, 2)
        bought += 1
        round_summary.setdefault("successes", []).append({
            "name": chosen.get("name") or chosen.get("steam_market_name") or "未命名商品",
            "amount": round(float(paid), 2),
        })
        ctx.set_status("running", "CHECKOUT_PENDING", progress_done=bought, progress_item=chosen.get("name", ""))
        ctx.log(f"已确认付款 本笔={paid:.2f} 累计={acc:.2f}/{target}", "info", category="buff")
        if acc >= target:
            break
        ctx.debug("下一件将重新按 SteamDT 顺序试稳定性")
        jittered_sleep(1.0, 0.0)

    return acc, bought, False


def _run_pipeline(config: dict) -> None:
    state = get_state()
    state.clear_stop()
    state.set_buff_auth_expired(False)
    state.set_buff_verification_required(False)
    cfg = apply_strategy_to_config(merge(DEFAULTS, config), "buy")
    pipeline_cfg = cfg.get("pipeline", {})
    verbose = bool(pipeline_cfg.get("verbose_debug", False))
    ctx = PipelineContext(state, str(uuid.uuid4())[:8], verbose=verbose)

    target = float(pipeline_cfg.get("target_balance", 100))
    budget = get_daily_budget_summary(target)
    blocking_orders = get_blocking_payment_orders()
    if blocking_orders:
        order_ids = ", ".join(str(order.get("external_order_id")) for order in blocking_orders[:3])
        ctx.log(
            f"检测到 {len(blocking_orders)} 个待支付或待核对订单（{order_ids}），已阻止继续锁单；请先处理订单状态",
            "error",
            category="buff",
        )
        ctx.set_status("error", "PENDING_ORDER_REVIEW")
        return
    exclude = pipeline_cfg.get("exclude_keywords", [])
    cred_buff = get_buff_credentials()
    cookies_buff = cred_buff.get("cookies", "")
    if not cookies_buff:
        ctx.log("未配置 Buff cookies", "error", category="config")
        ctx.set_status("error", "CONFIG_ERROR")
        return
    buff_payment_mode = str((cfg.get("buff") or {}).get("pay_method") or "alipay").strip().lower()
    steam_credentials = get_steam_credentials()
    from app.account_scope import validate_current_account_identity
    identity_ok, identity_error = validate_current_account_identity()
    if not identity_ok:
        ctx.log(f"账号身份保护: {identity_error}，任务未启动", "error", category="account")
        ctx.set_status("error", "STEAM_ACCOUNT_IDENTITY_MISMATCH")
        return
    if buff_payment_mode in {"balance", "balance_first"}:
        if not bool((cfg.get("buff") or {}).get("balance_auto_pay_acknowledged", False)):
            ctx.log("已选择余额自动支付模式，但尚未勾选自动扣款确认；任务未启动", "error", category="config")
            ctx.set_status("error", "BALANCE_AUTO_PAY_NOT_ACKNOWLEDGED")
            return
        if not str((steam_credentials or {}).get("steam_id") or "").strip():
            ctx.log("BUFF 余额支付需要已绑定的 SteamID64；任务未启动", "error", category="config")
            ctx.set_status("error", "STEAM_ID_REQUIRED_FOR_BALANCE_PAY")
            return

    ctx.log("买入阶段启动", "info")
    max_discount = pipeline_cfg.get("max_discount")
    sort_by = (cfg.get("steamdt") or cfg.get("iflow") or {}).get("sort_by", "sell")
    sort_labels = {"sell": "最优寄售", "buy": "最优求购"}
    sort_desc = sort_labels.get(sort_by, sort_by)
    retry_interval = int(pipeline_cfg.get("retry_interval_seconds", DEFAULT_RETRY_INTERVAL_SECONDS))
    ctx.log(
        f"配置: 目标余额={target}, 排除关键词={exclude}, 最高折扣={max_discount}, "
        f"排序={sort_desc}({sort_by}), 无符合时{retry_interval}秒后重试",
        "info",
    )
    if ctx.verbose:
        ctx.debug("详细调试已开启")

    proxy_manager = get_proxy_manager()
    if proxy_manager.is_proxy_enabled():
        ctx.set_status("running", "PROXY_WARMUP")
        ctx.log("代理池已启用，预热将在后台启动，pipeline 同步开始运行...", "info")
        proxy_warmup_thread = threading.Thread(
            target=proxy_manager.warmup, daemon=True, name="proxy-warmup"
        )
        proxy_warmup_thread.start()
    else:
        ctx.debug("代理池未启用或策略为关闭，跳过预热")

    acc = float(budget.get("used") or 0)
    ctx.log(
        f"今日投入额度: 目标={target:.2f}, 已确认={budget.get('confirmed', 0):.2f}, "
        f"未决占用={budget.get('reserved', 0):.2f}, 剩余={budget.get('remaining', 0):.2f}",
        "info",
        category="pipeline",
    )
    if acc >= target:
        ctx.log("今日投入额度已用完，不再启动新的购买", "info", category="pipeline")
        ctx.set_status("idle", "DAILY_BUDGET_REACHED")
        return
    total_bought = 0
    time_limit_enabled = bool(pipeline_cfg.get("start_time_limit_enabled", False))
    start_time_hour = max(0, min(23, int(pipeline_cfg.get("start_time_hour", DEFAULT_START_TIME_HOUR))))
    end_time_hour = max(0, min(23, int(pipeline_cfg.get("end_time_hour", DEFAULT_END_TIME_HOUR))))

    steam_client = SteamClient()
    analyzer = StabilityAnalyzer(usd_to_cny=USD_TO_CNY_DEFAULT)
    buyer = create_buff_client_from_config(cred_buff, cfg, steam_credentials=steam_credentials)
    failed_goods_ids_ttl: dict = {}
    previous_round_checked_ids: set = set()
    round_number = 0

    while True:
        if ctx.is_stop_requested():
            ctx.log("用户请求停止", "warn")
            ctx.set_status("stopped", "已停止")
            return

        if time_limit_enabled and not _is_in_time_window(start_time_hour, end_time_hour):
            ctx.set_status("running", "TIME_LIMIT_WAIT", progress_item=f"Allowed window: {start_time_hour}:00-{end_time_hour}:00")
            ctx.log(f"启动时间限制: 当前不在 {start_time_hour}:00–{end_time_hour}:00 内，60 秒后重试", "info")
            if ctx.wait_retry(60):
                return
            continue

        round_number += 1
        round_summary = _new_round_summary(round_number, acc)
        current_round_checked_ids: set = set()
        advance_round_cooldown = False
        try:
            now_ts = time.time()
            expired_ids = [gid for gid, exp in failed_goods_ids_ttl.items() if now_ts >= exp]
            for gid in expired_ids:
                del failed_goods_ids_ttl[gid]
            if expired_ids:
                ctx.log(f"Unblocked {len(expired_ids)} expired failed goods_id", "info", category="pipeline")
            failed_goods_ids = set(failed_goods_ids_ttl.keys())
            candidate_exclusions = failed_goods_ids | previous_round_checked_ids
            if previous_round_checked_ids:
                ctx.log(
                    f"[候选轮换] 上一轮实际检查 {len(previous_round_checked_ids)} 件，本轮先行排除",
                    "info",
                    category="pipeline",
                )

            filtered, fetch_failed = _fetch_and_filter_deals(
                ctx,
                cfg,
                retry_interval,
                exclude_goods_ids=candidate_exclusions,
            )
            advance_round_cooldown = not fetch_failed
            round_summary["candidate_count"] = len(filtered or [])
            net = get_network_checker()
            if fetch_failed:
                round_summary["notes"].append("SteamDT 数据拉取失败，本轮未进入商品检查")
                offline = net.report_failure(
                    log_fn=lambda msg, lvl: ctx.log(msg, lvl, category="network")
                )
                if offline:
                    _emit_round_summary(
                        ctx,
                        round_summary,
                        target,
                        "等待网络恢复后开始下一轮",
                    )
                    ctx.set_status("running", "NETWORK_OFFLINE")
                    recovered = net.wait_until_online(
                        is_stop_fn=ctx.is_stop_requested,
                        log_fn=lambda msg, lvl: ctx.log(msg, lvl, category="network"),
                    )
                    if not recovered:
                        ctx.set_status("stopped", "已停止")
                        return
                    continue
            else:
                net.report_success()

            if ctx.is_stop_requested():
                round_summary["notes"].append("收到停止指令")
                _emit_round_summary(ctx, round_summary, target, "任务已停止")
                ctx.set_status("stopped", "已停止")
                return
            if not filtered:
                if not fetch_failed:
                    round_summary["notes"].append("筛选后没有可检查的候选商品")
                _emit_round_summary(
                    ctx,
                    round_summary,
                    target,
                    f"{retry_interval} 秒后重新拉取",
                )
                if _wait_retry_and_refresh_buff_balance(
                    ctx,
                    retry_interval,
                    buyer,
                    buff_payment_mode,
                ):
                    return
                continue

            ctx.log("支付方式与 Buff 客户端已就绪", "info", category="buff")

            acc, total_bought, stopped = _process_deals_for_target(
                ctx, filtered, cfg, target, acc, total_bought,
                steam_client, analyzer, buyer,
                failed_goods_ids,
                set(),
                set(),
                round_summary,
                attempted_goods_ids=current_round_checked_ids,
            )
            if stopped:
                _emit_round_summary(
                    ctx,
                    round_summary,
                    target,
                    "任务已停止或等待人工核对",
                )
                return

            expire_ts = time.time() + FAILED_GOODS_TTL_SECONDS
            for gid in failed_goods_ids:
                if gid not in failed_goods_ids_ttl:
                    failed_goods_ids_ttl[gid] = expire_ts

            if acc >= target:
                _emit_round_summary(
                    ctx,
                    round_summary,
                    target,
                    "买入阶段结束",
                )
                break
            ctx.debug(f"本轮无满足条件饰品，等待 {retry_interval}s 重新拉取")
            _emit_round_summary(
                ctx,
                round_summary,
                target,
                f"{retry_interval} 秒后重新拉取",
            )
            if _wait_retry_and_refresh_buff_balance(
                ctx,
                retry_interval,
                buyer,
                buff_payment_mode,
            ):
                return

        except BuffManualCircuitOpen as e:
            round_summary["notes"].append("BUFF 人工熔断已触发")
            _emit_round_summary(ctx, round_summary, target, "停止任务并等待人工处理")
            ctx.log(f"BUFF 已触发人工熔断: {e}", "error", category="buff")
            ctx.set_status("error", "BUFF_MANUAL_CIRCUIT_OPEN")
            return
        except BuffTemporaryCircuitOpen as e:
            wait_seconds = max(1, int(e.retry_after + 0.999))
            round_summary["notes"].append(f"BUFF 请求保护暂停：{e.reason}")
            _emit_round_summary(
                ctx,
                round_summary,
                target,
                f"保护等待 {wait_seconds} 秒后开始下一轮",
            )
            ctx.log(f"BUFF 请求保护暂停: {e.reason}，{wait_seconds} 秒后重试", "warn", category="buff")
            ctx.set_status("running", "BUFF_RATE_LIMIT_PAUSED", progress_item=e.reason)
            if ctx.wait_retry(wait_seconds):
                return
            continue
        except BuffAuthExpired:
            round_summary["notes"].append("BUFF 登录已过期")
            _emit_round_summary(ctx, round_summary, target, "停止任务并等待重新登录")
            ctx.state.set_buff_auth_expired(True)
            ctx.log("Buff 登录已过期，请在界面重新登录", "error", category="buff")
            ctx.set_status("error", "BUFF_AUTH_EXPIRED")
            return
        except BuffVerificationRequired as e:
            reason = str(e) or "Buff 需要刷新页面或完成人机验证"
            round_summary["notes"].append("BUFF 需要完成人机验证")
            _emit_round_summary(ctx, round_summary, target, "停止任务并等待完成验证")
            ctx.state.set_buff_verification_required(True, reason)
            ctx.log(f"Buff 需要刷新页面状态或完成人机验证: {reason}", "error", category="buff")
            ctx.set_status("error", "BUFF_VERIFICATION_REQUIRED")
            return
        finally:
            if advance_round_cooldown:
                previous_round_checked_ids = set(current_round_checked_ids)

    ctx.set_status("running", "STEAM_COOLDOWN")
    ctx.log("买入阶段完成", "info")
    ctx.log(f"本次共成功购买 {total_bought} 单。Steam 交易冷却。", "info")
    ctx.set_status("idle", "")


_pipeline_thread = None
_pipeline_start_lock = threading.Lock()


def is_pipeline_running() -> bool:
    with _pipeline_start_lock:
        return _pipeline_thread is not None and _pipeline_thread.is_alive()


def _run_pipeline_guarded(config: dict) -> None:
    global _pipeline_thread
    try:
        _run_pipeline(config)
    finally:
        with _pipeline_start_lock:
            _pipeline_thread = None


def start_pipeline(config: dict) -> bool:
    global _pipeline_thread
    with _pipeline_start_lock:
        if _pipeline_thread is not None and _pipeline_thread.is_alive():
            return False
        t = threading.Thread(target=_run_pipeline_guarded, args=(config,), daemon=True, name="buy-pipeline")
        _pipeline_thread = t
        t.start()
        return True
